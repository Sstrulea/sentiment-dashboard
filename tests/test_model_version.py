"""Audit 6B — payload.meta.model."""
from src.economic_render import _model_meta


def test_model_meta_is_the_newest_version():
    m = _model_meta()
    assert m["version"] == "2026.10.06" and m["notice_days"] == 14
    assert "same scale" in m["changes"] and "re-derived thresholds" in m["changes"]
    assert m["history"][-1]["version"] == m["version"]
    prev = m["history"][-2]
    assert prev["version"] == "2026.09.24" and "2Y spread" in prev["changes"]


def test_newest_by_date_wins(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("versions:\n  - {version: b, since: 2026-10-01, changes: y}\n"
                 "  - {version: a, since: 2026-09-01, changes: x}\n")
    assert _model_meta(p)["version"] == "b"
