"""验收返工边界：R21 单处口径与人工口径优先链。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.rules_v33 import Document  # noqa: E402
from src.engine.checklist_identity_rules import CMM018_ScopeTerminology  # noqa: E402


def _doc(texts, *, scope="unit", source="cover"):
    document = Document(
        "isolated.pdf", len(texts), 0, texts, [[] for _ in texts],
        [None for _ in texts], [[] for _ in texts], report_kind="final",
    )
    document.document_profile = SimpleNamespace(
        writing_scope={"status": "confirmed", "value": scope, "source": source}
    )
    return document


def _opposite(count: int):
    return ["2024年度单位决算"] + ["本表反映部门本年度收支决算情况。"] * count


def test_single_to_four_opposite_captions_are_all_reported():
    for count in (1, 2, 3, 4):
        issues = CMM018_ScopeTerminology().apply(_doc(_opposite(count)))
        assert issues, f"{count} 处相反表注必须报告"


def test_human_confirmed_department_scope_beats_bare_unit_cover():
    document = _doc(["2024年度决算"] + ["本表反映部门本年度收支决算情况。"] * 4,
                    scope="department", source="human_confirmation")
    assert CMM018_ScopeTerminology().apply(document) == []


def test_human_confirmed_department_still_reports_unit_captions():
    document = _doc(["2024年度决算"] + ["本表反映单位本年度收支决算情况。"] * 4,
                    scope="department", source="human_confirmation")
    assert CMM018_ScopeTerminology().apply(document)


def test_same_scope_captions_are_not_reported():
    document = _doc(["2024年度单位决算"] + ["本表反映单位本年度收支决算情况。"] * 4,
                    scope="unit", source="cover")
    assert CMM018_ScopeTerminology().apply(document) == []
