"""Audit 6B — payload.meta.model."""
from src.economic_render import _model_meta


def test_model_meta_is_the_newest_version():
    m = _model_meta()
    assert m["version"] == "2026.11.25-v4" and m["since"] == "2026-11-25" and m["notice_days"] == 14
    assert m["changes"].startswith("FX scoring v4: pair score = COT positioning only")
    assert m["history"][-1]["version"] == m["version"]
    assert [h["version"] for h in m["history"][-5:-1]] == ["2026.09.24", "2026.10.07", "2026.10.07-v3",
                                                          "2026.10.08-metals"]
    assert "scoring v3" in m["history"][-3]["changes"]


def test_newest_by_date_wins(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("versions:\n  - {version: b, since: 2026-10-01, changes: y}\n"
                 "  - {version: a, since: 2026-09-01, changes: x}\n")
    assert _model_meta(p)["version"] == "b"
