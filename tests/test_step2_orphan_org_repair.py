"""Step 2 回归测试：孤儿组织重绑脚本与 CMM-019 组织缺失报错文案。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.repair_orphan_org_links import repair_upload_status_files, resolve_target_org
from src.engine.checklist_identity_rules import CMM019_CoverElements
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import build_document
from src.services.org_name_identity import OrgNameIndex


def test_cmm019_distinguishes_missing_directory_org_from_cover_defect() -> None:
    doc = build_document(
        "sample.pdf",
        ["上海市测试局\n2025 年度决算", "第二部分 上海市测试局 2025 年度部门决算表"],
        [[], []],
        0,
    )
    doc.report_kind = "final"
    doc.report_year = 2025
    doc.dominant_year = 2025
    doc.document_profile = SimpleNamespace(
        report_kind=SimpleNamespace(value="final"),
        report_year=SimpleNamespace(value=2025),
        writing_scope={
            "status": "insufficient",
            "value": None,
            "cover_independent_scope": None,
            "evidence": [],
            "snapshot": {"available": False, "capability_scope": {}},
            "archive_basis": {
                "organization_id": "stale-org-id",
                "organization_name": None,
                "level": None,
                "source": "upload_selection",
            },
        },
    )
    with pytest.raises(RuleDeferred) as caught:
        CMM019_CoverElements().apply(doc)
    assert "所属组织已不在目录中（层级未知）" in caught.value.detail


def test_repair_upload_status_files_remaps_unique_and_dept_disambiguated(tmp_path: Path) -> None:
    orgs = [
        SimpleNamespace(id="org-fin", name="上海市财政局", level="department"),
        SimpleNamespace(id="org-bd-dept", name="上海市大数据中心", level="department"),
        SimpleNamespace(id="org-bd-unit", name="上海市大数据中心", level="unit"),
        SimpleNamespace(
            id="org-lg",
            name="中国（上海）自由贸易试验区临港新片区管理委员会",
            level="department",
        ),
    ]
    j1 = tmp_path / "job1"
    j1.mkdir()
    (j1 / "status.json").write_text(
        json.dumps({"organization_id": "old-fin", "organization_name": "上海市财政局", "doc_type": "dept_final"}),
        encoding="utf-8",
    )
    j2 = tmp_path / "job2"
    j2.mkdir()
    (j2 / "status.json").write_text(
        json.dumps({"organization_id": "old-bd", "organization_name": "上海市大数据中心", "doc_type": "dept_final"}),
        encoding="utf-8",
    )
    j3 = tmp_path / "job3"
    j3.mkdir()
    (j3 / "status.json").write_text(
        json.dumps({
            "organization_id": "old-lg",
            "organization_name": "中国自由贸易试验区临港新片区管理委员会",
            "doc_type": "dept_final",
        }),
        encoding="utf-8",
    )

    summary = repair_upload_status_files(tmp_path, orgs, apply=True)
    assert summary["repaired"] == 3
    assert summary["skipped"] == []
    assert json.loads((j1 / "status.json").read_text())["organization_id"] == "org-fin"
    assert json.loads((j2 / "status.json").read_text())["organization_id"] == "org-bd-dept"
    j3_data = json.loads((j3 / "status.json").read_text())
    assert j3_data["organization_id"] == "org-lg"
    assert j3_data["organization_name"] == "中国（上海）自由贸易试验区临港新片区管理委员会"

    # 幂等性：再次执行无新增修改
    second = repair_upload_status_files(tmp_path, orgs, apply=True)
    assert second["already_valid"] == 3
    assert second["repaired"] == 0
