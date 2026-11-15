"""Render: frame-exact segments -> cut.mov -> final 4K60 HEVC delivery.

1. One ProRes 422 segment per timeline entry, decoded from the ORIGINAL take with the
   conform chain (mirror + SDR + tv range) at 2160x3840/60: -ss seek + exact frame count,
   audio trimmed/padded to exactly N/fps seconds with 4 ms fades.
2. Concat (stream copy) -> cut.mov.
3. Final: graphics clips (ProRes 4444 alpha from HyperFrames) overlaid at their start
   times, English captions burned with libass, two-pass loudnorm (+ optional ducked
   music), hevc_videotoolbox Main 8-bit hvc1, -g 120, VUI forced to BT.709 / level 5.1.
   One lossy generation after the mezzanine.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..config import FONTS_DIR, SETTINGS
from ..models import Project, Timeline
from ..util.proc import CommandError, ProgressFn, run_ffmpeg
from .conform import TAG_709, video_chain

VUI_709 = ("hevc_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1"
           ":video_full_range_flag=0:level=5.1")
FADE_S = 0.004


def _segment_args(src: str, start: float, frames: int, fps: int, vf: str, codec: list[str], out: Path) -> list[str]:
    dur = frames / fps
    af = (f"aresample=48000,apad,atrim=0:{dur:.6f},afade=t=in:d={FADE_S},"
          f"afade=t=out:st={max(0.0, dur - FADE_S):.6f}:d={FADE_S}")
    return ["-ss", f"{start:.6f}", "-i", src, "-frames:v", str(frames),
            "-vf", vf + ",tpad=stop_mode=clone:stop_duration=1", "-af", af,
            *codec, "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "2", "-t", f"{dur:.6f}", str(out)]


def render_segments(project: Project, timeline: Timeline, out_dir: Path, settings=SETTINGS,
                    progress: ProgressFn | None = None) -> list[Path]:
    d = settings.delivery
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    total = max(1, len(timeline.entries))
    for i, e in enumerate(timeline.entries):
        take = project.take(e.take_id)
        frames = e.src_out_f - e.src_in_f
        out = out_dir / f"seg{i + 1:03d}.mov"
        vf = video_chain(take.info, mirror=take.mirror, width=d.width, height=d.height, fps=d.fps,
                         out_pix_fmt="p210le")
        vt = ["-c:v", "prores_videotoolbox", "-profile:v", "2"]
        try:
            run_ffmpeg(_segment_args(take.source_path, e.src_in_f / d.fps, frames, d.fps, vf, vt, out),
                       ffmpeg=settings.ffmpeg)
        except CommandError:
            vf_sw = vf.replace("format=p210le", "format=yuv422p10le")
            run_ffmpeg(_segment_args(take.source_path, e.src_in_f / d.fps, frames, d.fps, vf_sw,
                                     ["-c:v", "prores_ks", "-profile:v", "2"], out), ffmpeg=settings.ffmpeg)
        paths.append(out)
        if progress:
            progress((i + 1) / total, f"segment {i + 1}/{total}")
    return paths


def concat_segments(paths: list[Path], out: Path, settings=SETTINGS) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in paths), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], ffmpeg=settings.ffmpeg)
    return out


def _audio_graph(has_music: bool, music_idx: int, music_gain_db: float, dur: float) -> tuple[str, str]:
    """(graph producing [mix], label). Music is looped, trimmed, attenuated and ducked under his voice."""
    if not has_music:
        return "[0:a]aresample=48000[mix]", "mix"
    return (f"[0:a]aresample=48000,asplit=2[voice][key];"
            f"[{music_idx}:a]aresample=48000,atrim=0:{dur:.6f},volume={music_gain_db}dB[bed];"
            f"[bed][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck];"
            f"[voice][duck]amix=inputs=2:duration=first:normalize=0[mix]"), "mix"


def final_encode(cut: Path, out: Path, *, captions_ass: Path | None = None,
                 overlays: list[tuple] | None = None, music: Path | None = None,
                 music_gain_db: float = -20.0, settings=SETTINGS, preview: bool = False,
                 progress: ProgressFn | None = None) -> Path:
    from .audioio import duration_of
    from .loudness import loudnorm_filter, loudnorm_measure

    d = settings.delivery
    overlays = overlays or []
    work = out.parent
    work.mkdir(parents=True, exist_ok=True)
    dur = duration_of(cut)

    inputs: list[str] = ["-i", str(cut.resolve())]
    for clip, *_ in overlays:   # (clip, start) full-frame, or (clip, start, x, y) placed
        inputs += ["-i", str(Path(clip).resolve())]
    music_idx = 1 + len(overlays)
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(Path(music).resolve())]

    # audio pass 1 (measure the mix that will be normalised)
    a_graph, a_label = _audio_graph(bool(music), music_idx, music_gain_db, dur)
    meas = loudnorm_measure([*inputs], a_graph.rsplit(f"[{a_label}]", 1)[0], settings)
    a_final = f"{a_graph};[{a_label}]{loudnorm_filter(meas, settings)}[aout]"

    # video: overlays at their start times, then captions (libass), then tag BT.709
    v = ["[0:v]format=yuv444p10le[v0]"]
    last = "v0"
    for k, (_, start, *pos) in enumerate(overlays, start=1):
        x, y = (pos + [0, 0])[:2]
        v.append(f"[{k}:v]setpts=PTS-STARTPTS+{start:.6f}/TB[o{k}]")
        v.append(f"[{last}][o{k}]overlay=x={int(x)}:y={int(y)}:format=yuv444p10:alpha=straight:eof_action=pass[v{k}]")
        last = f"v{k}"
    tail = []
    if captions_ass is not None:
        local = work / "captions.ass"
        if captions_ass.resolve() != local.resolve():
            shutil.copyfile(captions_ass, local)
        fonts = work / "fonts"
        if not fonts.exists():
            fonts.symlink_to(FONTS_DIR, target_is_directory=True)
        tail.append("ass=filename=captions.ass:fontsdir=fonts")  # relative: ffmpeg runs in `work`
    if preview:
        tail += ["scale=1080:1920:flags=lanczos", "fps=30"]
    tail += [f"format={d.pix_fmt}", TAG_709]
    v.append(f"[{last}]" + ",".join(tail) + "[vout]")
    graph = ";".join(v) + ";" + a_final
    script = work / ("final_graph.txt" if not preview else "preview_graph.txt")
    script.write_text(graph, encoding="utf-8")

    if preview:
        venc = ["-c:v", "h264_videotoolbox", "-b:v", "12M", "-r", "30"]
    else:
        venc = ["-c:v", "hevc_videotoolbox", "-profile:v", "main", "-b:v", d.video_bitrate,
                "-maxrate", d.video_maxrate, "-bufsize", "120M", "-g", str(d.gop), "-tag:v", d.vtag,
                "-bsf:v", VUI_709, "-colorspace", d.color_space, "-color_range", d.color_range,
                "-r", str(d.fps)]
    args = [*inputs, "-/filter_complex", script.name, "-map", "[vout]", "-map", "[aout]", *venc,
            "-c:a", d.audio_codec, "-b:a", d.audio_bitrate, "-ar", str(d.audio_rate), "-ac",
            str(d.audio_channels), "-t", f"{dur:.6f}", "-movflags", "+faststart", str(out.resolve())]
    _run_in(work, args, settings, dur, progress)
    return out


def _run_in(cwd: Path, args: list[str], settings, total_s: float, progress) -> None:
    """run_ffmpeg in `cwd` so the graph's relative ass/fonts paths resolve (no quoting issues)."""
    run_ffmpeg(args, ffmpeg=settings.ffmpeg, total_s=total_s, progress=progress, cwd=cwd)
