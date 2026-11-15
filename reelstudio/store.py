"""Project persistence. One folder per reel; JSON artifacts written atomically.

projects/<id>/
  project.json            Project (takes, script, settings, stage status)
  takes/                  symlinks to the originals (never modified)
  work/<take_id>/         audio.wav, proxy.mp4, speech.json, blocks.json,
                          transcripts.json, face.json
  alignment.json          Alignment
  plan.json               Plan (approved by the creator before render)
  timeline.json           Timeline (frame-exact source -> output mapping)
  captions.json / .ass    CaptionCue list + rendered ASS
  layout.json             Layout used for captions/graphics
  inserts/                HyperFrames project for this reel
  voice/                  generated voice clips + clips.json
  render/                 segments/, cut.mov, overlay.mov, final.mov, preview.mp4
  qa/                     report.json + evidence
  logs/                   per-job logs
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, TypeAdapter

from .config import PROJECTS_DIR
from .models import Project

T = TypeVar("T")
_LOCK = threading.RLock()


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug[:48] or "reel"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_json(path: Path, data: BaseModel | list | dict) -> None:
    if isinstance(data, BaseModel):
        text = data.model_dump_json(indent=2)
    else:
        text = json.dumps(_jsonable(data), indent=2, ensure_ascii=False)
    atomic_write_text(path, text)


def read_json(path: Path, kind: type[T] | object) -> T:
    """Read JSON into a model or any type TypeAdapter understands (e.g. list[Block])."""
    raw = path.read_text(encoding="utf-8")
    return TypeAdapter(kind).validate_json(raw)  # type: ignore[arg-type]


def _jsonable(data):
    if isinstance(data, BaseModel):
        return data.model_dump(mode="json")
    if isinstance(data, dict):
        return {k: _jsonable(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_jsonable(v) for v in data]
    return data


class ProjectPaths:
    """Path helpers for one project folder."""

    def __init__(self, root: Path):
        self.root = root

    @property
    def project_json(self) -> Path: return self.root / "project.json"
    @property
    def takes_dir(self) -> Path: return self.root / "takes"
    def work(self, take_id: str) -> Path: return self.root / "work" / take_id
    def audio(self, take_id: str) -> Path: return self.work(take_id) / "audio.wav"
    def proxy(self, take_id: str) -> Path: return self.work(take_id) / "proxy.mp4"
    def speech(self, take_id: str) -> Path: return self.work(take_id) / "speech.json"
    def blocks(self, take_id: str) -> Path: return self.work(take_id) / "blocks.json"
    def transcripts(self, take_id: str) -> Path: return self.work(take_id) / "transcripts.json"
    def face(self, take_id: str) -> Path: return self.work(take_id) / "face.json"
    @property
    def alignment(self) -> Path: return self.root / "alignment.json"
    @property
    def plan(self) -> Path: return self.root / "plan.json"
    @property
    def timeline(self) -> Path: return self.root / "timeline.json"
    @property
    def captions_json(self) -> Path: return self.root / "captions.json"
    @property
    def captions_ass(self) -> Path: return self.root / "captions.ass"
    @property
    def layout(self) -> Path: return self.root / "layout.json"
    @property
    def inserts_dir(self) -> Path: return self.root / "inserts"
    @property
    def voice_dir(self) -> Path: return self.root / "voice"
    @property
    def render_dir(self) -> Path: return self.root / "render"
    @property
    def segments_dir(self) -> Path: return self.render_dir / "segments"
    @property
    def cut(self) -> Path: return self.render_dir / "cut.mov"
    @property
    def overlay(self) -> Path: return self.render_dir / "overlay.mov"
    @property
    def final(self) -> Path: return self.render_dir / "final.mov"
    @property
    def preview(self) -> Path: return self.render_dir / "preview.mp4"
    @property
    def qa_dir(self) -> Path: return self.root / "qa"
    @property
    def qa_report(self) -> Path: return self.qa_dir / "report.json"
    @property
    def logs_dir(self) -> Path: return self.root / "logs"


class ProjectStore:
    def __init__(self, base: Path = PROJECTS_DIR):
        self.base = base
        self.base.mkdir(parents=True, exist_ok=True)

    def paths(self, project_id: str) -> ProjectPaths:
        return ProjectPaths(self.base / project_id)

    def list(self) -> list[Project]:
        out = []
        for pj in sorted(self.base.glob("*/project.json")):
            try:
                out.append(read_json(pj, Project))
            except Exception:  # a corrupt project must not hide the others
                continue
        return sorted(out, key=lambda p: p.created_at, reverse=True)

    def create(self, name: str, **fields) -> Project:
        with _LOCK:
            base_id = slugify(name)
            pid, n = base_id, 2
            while (self.base / pid).exists():
                pid, n = f"{base_id}-{n}", n + 1
            project = Project(id=pid, name=name, **fields)
            self.save(project)
            return project

    def load(self, project_id: str) -> Project:
        return read_json(self.paths(project_id).project_json, Project)

    def save(self, project: Project) -> None:
        with _LOCK:
            write_json(self.paths(project.id).project_json, project)

    def update(self, project_id: str, fn) -> Project:
        """Read-modify-write under the store lock: fn(project) mutates in place."""
        with _LOCK:
            project = self.load(project_id)
            fn(project)
            self.save(project)
            return project
