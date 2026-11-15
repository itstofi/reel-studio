"""Web API: projects, script parsing, approval gate, Range + traversal safety."""
import pytest
from fastapi.testclient import TestClient

from reelstudio.api import create_app
from reelstudio.edit.planner import plan_summary  # noqa: F401  (import check)
from reelstudio.models import Beat, Plan, Segment
from reelstudio.store import ProjectStore, write_json


@pytest.fixture()
def client(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    return TestClient(create_app(store)), store


def test_project_script_and_plan_gate(client):
    c, store = client
    pid = c.post("/api/projects", json={"name": "Test Reel", "lang": "ar"}).json()["id"]
    assert [p["id"] for p in c.get("/api/projects").json()] == [pid]
    r = c.put(f"/api/projects/{pid}/script", json={"ar_text": "سطر واحد\nسطر اثنان [عاجل]", "en_text": "Line one\nLine two [BREAKING]"})
    lines = r.json()["script"]["lines"]
    assert len(lines) == 2 and lines[1]["onscreen"] == "BREAKING"
    assert c.put(f"/api/projects/{pid}/script", json={"ar_text": "a\nb", "en_text": "a"}).status_code == 400
    assert c.get(f"/api/projects/{pid}/plan").status_code == 404
    assert c.post(f"/api/projects/{pid}/run/render").status_code == 400          # no plan
    write_json(store.paths(pid).plan, Plan(beats=[Beat(id="B01", segments=[Segment(id="s", take_id="t01", src_in=0, src_out=1)])]))
    assert c.post(f"/api/projects/{pid}/run/render").status_code == 400          # not approved
    assert c.post(f"/api/projects/{pid}/plan/approve").json()["approved"] is True
    assert c.get(f"/api/projects/{pid}/plan").json()["plan"]["approved"] is True


def test_files_range_and_traversal(client, tmp_path):
    c, store = client
    pid = c.post("/api/projects", json={"name": "Files"}).json()["id"]
    f = store.paths(pid).root / "render" / "x.bin"
    f.parent.mkdir(parents=True)
    f.write_bytes(bytes(range(256)) * 10)
    r = c.get(f"/api/projects/{pid}/files/render/x.bin", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == bytes(range(10, 20)) and r.headers["content-range"] == "bytes 10-19/2560"
    (tmp_path / "secret.txt").write_text("no")
    assert c.get(f"/api/projects/{pid}/files/..%2F..%2Fsecret.txt").status_code in (403, 404)
    assert c.get(f"/api/projects/{pid}/files/render/missing.mov").status_code == 404
