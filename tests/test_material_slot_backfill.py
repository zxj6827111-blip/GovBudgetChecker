"""历史回填盘点：dry-run 必须真的什么都不改。

本文件里最重要的一条是 ``test_dry_run_never_touches_the_database``：
它把数据库入口整个打成为异常，然后跑完整盘点。只要盘点过程中有任何一次
取连接的动作，用例就会失败。这比"检查返回值里 writes_performed == 0"强得多——
后者只是自报家门，前者是让"偷偷写库"在物理上不可能通过。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services import material_slot_resolver as resolver
from src.services.material_slot_backfill import (
    is_job_directory_name,
    render_markdown,
    scan_upload_root,
)

DISTRICT_ID = "833e15c63a38"
DEPT_ID = "8f773936806d"
UNIT_ID = "53f98bebfc3a"

ORG_RECORDS = [
    {"id": DISTRICT_ID, "name": "普陀区", "level": "district", "parent_id": None},
    {
        "id": DEPT_ID,
        "name": "上海市普陀区规划和自然资源局",
        "level": "department",
        "parent_id": DISTRICT_ID,
    },
    {
        "id": UNIT_ID,
        "name": "上海市普陀区规划和自然资源局本级",
        "level": "unit",
        "parent_id": DEPT_ID,
    },
]

JOB_OK = "a" * 32
JOB_NO_ORG = "b" * 32
JOB_NO_YEAR = "c" * 32
JOB_KIND_CONFLICT = "d" * 32
JOB_NO_CHECKSUM = "e" * 32
JOB_NOT_A_JOB_DIR = "putuo_final_samples"


def _write_status(root: Path, job_id: str, payload: dict) -> None:
    job_dir = root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "status.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def upload_root(tmp_path: Path) -> Path:
    root = tmp_path / "uploads"
    root.mkdir()
    _write_status(
        root,
        JOB_OK,
        {
            "organization_id": UNIT_ID,
            "organization_name": "上海市普陀区规划和自然资源局本级",
            "report_year": "2024",
            "report_kind": "final",
            "doc_type": "dept_final",
            "filename": "规划和自然资源局本级2024年度单位决算.pdf",
            "checksum": "1" * 64,
        },
    )
    _write_status(
        root,
        JOB_NO_ORG,
        {
            "report_year": "2024",
            "report_kind": "final",
            "doc_type": "dept_final",
            "filename": "某单位决算.pdf",
            "checksum": "2" * 64,
        },
    )
    _write_status(
        root,
        JOB_NO_YEAR,
        {
            "organization_id": UNIT_ID,
            "report_kind": "final",
            "doc_type": "dept_final",
            "filename": "未知年度.pdf",
            "checksum": "3" * 64,
        },
    )
    _write_status(
        root,
        JOB_KIND_CONFLICT,
        {
            "organization_id": UNIT_ID,
            "report_year": "2024",
            "report_kind": "budget",
            "doc_type": "dept_final",
            "filename": "口径矛盾.pdf",
            "checksum": "4" * 64,
        },
    )
    _write_status(
        root,
        JOB_NO_CHECKSUM,
        {"organization_id": UNIT_ID, "filename": "无校验和.pdf"},
    )
    # 非任务目录：uploads/ 下人工放置的样本目录必须被跳过，否则盘点数字虚高
    samples = root / JOB_NOT_A_JOB_DIR
    samples.mkdir()
    (samples / "sample.pdf").write_bytes(b"%PDF-1.4")
    return root


# ==== 目录识别 ==============================================================


def test_job_directory_detection():
    assert is_job_directory_name("a" * 32) is True
    assert is_job_directory_name("A" * 32) is True
    assert is_job_directory_name("putuo_final_samples") is False
    assert is_job_directory_name("a" * 31) is False
    assert is_job_directory_name("g" * 32) is False


def test_sample_directory_is_not_scanned(upload_root: Path):
    report = scan_upload_root(upload_root, org_records=ORG_RECORDS)
    assert report.scanned_jobs == 5
    assert all(item.job_id != JOB_NOT_A_JOB_DIR for item in report.items)


# ==== 分类正确性 ============================================================

def test_scan_classifies_each_reason(upload_root: Path):
    report = scan_upload_root(upload_root, org_records=ORG_RECORDS)
    by_job = {item.job_id: item for item in report.items}

    assert by_job[JOB_OK].reason == resolver.REASON_OK
    assert by_job[JOB_OK].bucket == "auto_mappable"
    assert by_job[JOB_NO_ORG].reason == resolver.REASON_ORGANIZATION_MISSING
    assert by_job[JOB_NO_YEAR].reason == resolver.REASON_YEAR_UNKNOWN
    assert by_job[JOB_KIND_CONFLICT].reason == resolver.REASON_KIND_CONFLICT
    assert by_job[JOB_NO_CHECKSUM].reason == resolver.REASON_NO_DOCUMENT_KEY
    assert by_job[JOB_NO_CHECKSUM].bucket == "no_document"

    assert report.buckets["auto_mappable"] == 1
    assert report.buckets["conflict"] == 1
    assert report.buckets["missing_year"] == 1
    assert report.buckets["missing_subject"] == 1
    assert report.buckets["no_document"] == 1


def test_repeated_runs_of_one_material_collapse_to_one_slot(tmp_path: Path):
    """同一份材料被分析三次，只算一个槽位、一个版本绑定。"""
    root = tmp_path / "uploads"
    root.mkdir()
    for index, job_id in enumerate(("1" * 32, "2" * 32, "3" * 32)):
        _write_status(
            root,
            job_id,
            {
                "organization_id": UNIT_ID,
                "report_year": "2024",
                "report_kind": "final",
                "doc_type": "dept_final",
                "filename": "同一份材料.pdf",
                "checksum": "9" * 64,
                "analyzed_at": index,
            },
        )
    report = scan_upload_root(root, org_records=ORG_RECORDS)
    assert report.scanned_jobs == 3
    assert report.buckets["auto_mappable"] == 3
    assert report.projected_slots == 1
    assert report.projected_versions == 1


def test_same_name_department_and_unit_produce_two_slots(tmp_path: Path):
    root = tmp_path / "uploads"
    root.mkdir()
    _write_status(
        root,
        "4" * 32,
        {
            "organization_id": DEPT_ID,
            "report_year": "2024",
            "report_kind": "final",
            "doc_type": "dept_final",
            "filename": "部门汇总决算.pdf",
            "checksum": "5" * 64,
        },
    )
    _write_status(
        root,
        "6" * 32,
        {
            "organization_id": UNIT_ID,
            "report_year": "2024",
            "report_kind": "final",
            "doc_type": "dept_final",
            "filename": "本级单位决算.pdf",
            "checksum": "6" * 64,
        },
    )
    report = scan_upload_root(root, org_records=ORG_RECORDS)
    assert report.projected_slots == 2


def test_report_marks_itself_as_dry_run(upload_root: Path):
    report = scan_upload_root(upload_root, org_records=ORG_RECORDS)
    payload = report.to_dict()
    assert payload["dry_run"] is True
    assert payload["writes_performed"] == 0
    assert payload["jobs_with_document"] == 4  # 一条没有校验和


def test_markdown_report_states_it_writes_nothing(upload_root: Path):
    report = scan_upload_root(upload_root, org_records=ORG_RECORDS)
    markdown = render_markdown(report, upload_root=str(upload_root))
    assert "不写入任何数据" in markdown
    assert "实际写入条数：0" in markdown
    assert "organization_missing" in markdown


def test_missing_upload_root_returns_empty_report(tmp_path: Path):
    report = scan_upload_root(tmp_path / "does-not-exist")
    assert report.scanned_jobs == 0
    assert report.projected_slots == 0
    assert report.writes_performed == 0


# ==== dry-run 的纯净性 ======================================================


@pytest.mark.asyncio
async def test_dry_run_never_touches_the_database(upload_root: Path, monkeypatch):
    """把数据库入口打成为异常后仍能跑完盘点，证明它一次都不碰库。"""
    from src.db.connection import DatabaseConnection

    def _explode(*args, **kwargs):
        raise AssertionError("dry-run 不应访问数据库")

    monkeypatch.setattr(DatabaseConnection, "get_pool", classmethod(_explode))
    monkeypatch.setattr(DatabaseConnection, "acquire", classmethod(_explode))

    report = scan_upload_root(upload_root, org_records=ORG_RECORDS)
    assert report.scanned_jobs == 5
    assert report.writes_performed == 0


def test_dry_run_does_not_modify_input_files(upload_root: Path):
    """盘点前后，任务目录的文件集合与内容必须逐字节一致。"""
    before = {
        path.relative_to(upload_root): path.read_bytes()
        for path in sorted(upload_root.rglob("*"))
        if path.is_file()
    }
    scan_upload_root(upload_root, org_records=ORG_RECORDS)
    after = {
        path.relative_to(upload_root): path.read_bytes()
        for path in sorted(upload_root.rglob("*"))
        if path.is_file()
    }
    assert before == after


def test_unreadable_status_is_skipped_not_guessed(tmp_path: Path):
    root = tmp_path / "uploads"
    job_dir = root / ("7" * 32)
    job_dir.mkdir(parents=True)
    (job_dir / "status.json").write_text("{ not json", encoding="utf-8")

    report = scan_upload_root(root, org_records=ORG_RECORDS)
    assert report.scanned_jobs == 1
    assert report.items[0].reason == resolver.REASON_NO_DOCUMENT_KEY
    assert report.items[0].detail["status_json"] == "missing_or_unreadable"
