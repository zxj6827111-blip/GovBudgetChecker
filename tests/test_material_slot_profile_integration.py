"""PR #42 评审修复轮：真正的 DocumentProfile 必须进入槽位归属链路。

背景（独立评审发现的接线缺口）
------------------------------
主分析链路已经算出完整的 ``DocumentProfile``（含 ``conflicts``、
``report_year``、``caliber``、``profile_status``），但交给结构化入库的
metadata 里只有最终选中文种等几个标量。于是：

- 画像已经判定"文种有两个互斥候选"，入库侧看不到，本该停在映射待确认的
  材料被当成身份已确认；
- 任务年度与材料实际年度不一致时，入库侧看不到第二个候选，年度冲突丢失；
- ``caliber`` 永远取不到值，材料口径恒为 ``unknown``。

本文件的断言分两层：

1. ``build_ingest_metadata`` 的纯函数行为——这是被测试的单元；
2. ``_run_pipeline_inner`` 实际跑通后，捕获 ``run_structured_ingest`` 收到的
   metadata，确认画像**真的被传了过去**。第二层是行为断言而不是读源码断言：
   "字段写在那里"和"真的传过去了"是两件事，后者才是这次要修的缺口。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from api import main as pipeline_mod
from api import runtime
from src.services import material_slot_resolver as resolver
from src.services.material_slot_service import MaterialSlotService, allocate_for_document
from src.services.structured_ingest_runner import build_ingest_metadata
from support_material_slot_db import FakeSlotConnection

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

CHECKSUM = "c" * 64


def _metadata(**overrides) -> Dict[str, Any]:
    base = {
        "organization_id": UNIT_ID,
        "organization_name": "上海市普陀区规划和自然资源局本级",
        "report_year": "2024",
        "report_kind": "final",
        "doc_type": "dept_final",
        "filename": "规划和自然资源局本级2024年度单位决算.pdf",
    }
    base.update(overrides)
    return base


def _decide(metadata: Dict[str, Any]):
    return resolver.decide_slot_allocation(
        metadata=metadata, org_records=ORG_RECORDS, checksum=CHECKSUM
    )


# ==== 1. build_ingest_metadata：纯函数行为 ==================================


def test_build_ingest_metadata_keeps_the_scalar_fields():
    payload = build_ingest_metadata(
        organization_id=UNIT_ID,
        organization_name="某单位",
        fiscal_year=2024,
        doc_type="dept_final",
        report_year=2024,
        report_kind="final",
        checksum=CHECKSUM,
    )
    assert payload["organization_id"] == UNIT_ID
    assert payload["report_kind"] == "final"
    assert payload["checksum"] == CHECKSUM
    # 没有画像时不该凭空造一个
    assert "document_profile" not in payload


def test_build_ingest_metadata_accepts_a_dict_profile():
    profile = {"report_kind": {"value": "final"}, "conflicts": []}
    payload = build_ingest_metadata(document_profile=profile)
    assert payload["document_profile"] == profile


def test_build_ingest_metadata_accepts_a_profile_object():
    """真实调用点传的是 DocumentProfile 对象，不是已经转好的 dict。"""

    class _Profile:
        def to_dict(self) -> Dict[str, Any]:
            return {"report_kind": {"value": "final"}, "conflicts": [{"field": "report_kind"}]}

    payload = build_ingest_metadata(document_profile=_Profile())
    assert payload["document_profile"]["conflicts"] == [{"field": "report_kind"}]


def test_build_ingest_metadata_rejects_garbage_instead_of_dropping_it():
    """传错类型必须报错，不能静默丢弃。

    静默丢弃正是本轮要修的缺陷类型：画像没传进来不会报错，只会让台账少识别
    一批冲突，事后无从察觉。
    """
    with pytest.raises(TypeError):
        build_ingest_metadata(document_profile="不是画像")


def test_build_ingest_metadata_accepts_the_real_document_profile():
    """用真实画像类跑一遍，确认接口形状没有对不上。"""
    from src.schemas.document_profile import DocumentProfile

    profile = DocumentProfile()
    payload = build_ingest_metadata(document_profile=profile)
    assert payload["document_profile"]["resolver_version"] == profile.resolver_version


# ==== 2. 画像进入归属判定：文种冲突 =========================================


def test_profile_kind_conflict_forces_mapping_required():
    """画像已判定文种互斥，入库侧不能因为只看到最终值就当成已确认。"""
    decision = _decide(
        _metadata(
            document_profile={
                "report_kind": {"value": "final", "source": "cover_title"},
                "conflicts": [
                    {"field": "report_kind", "values": ["budget", "final"]}
                ],
            }
        )
    )
    assert decision.status == resolver.DECISION_MAPPING_REQUIRED
    assert decision.reason == resolver.REASON_KIND_CONFLICT
    assert decision.identity.is_resolved is False


def test_no_profile_means_no_conflict_is_invented():
    """没有画像时不能凭空判定冲突——冲突是结论，不是默认值。"""
    decision = _decide(_metadata())
    assert decision.ok is True
    assert decision.reason == resolver.REASON_OK


# ==== 3. 画像进入归属判定：年度冲突 =========================================


def test_profile_year_conflict_forces_mapping_required():
    """任务记录 2024，画像从 PDF 识别到 2025 —— 两个候选必须报冲突。"""
    decision = _decide(
        _metadata(
            report_year="2024",
            document_profile={"report_year": {"value": 2025, "source": "cover_label"}},
        )
    )
    assert decision.status == resolver.DECISION_MAPPING_REQUIRED
    assert decision.reason == resolver.REASON_YEAR_CONFLICT
    assert decision.identity.fiscal_year is None


def test_profile_year_agreement_does_not_create_a_conflict():
    """两边一致时不报冲突——否则真实材料会被大面积误判为待确认。"""
    decision = _decide(
        _metadata(
            report_year="2024",
            document_profile={"report_year": {"value": 2024, "source": "cover_label"}},
        )
    )
    assert decision.ok is True
    assert decision.identity.fiscal_year == 2024


# ==== 4. 画像进入归属判定：caliber ==========================================


@pytest.mark.asyncio
async def test_profile_caliber_is_written_to_the_slot():
    conn = FakeSlotConnection()
    conn.seed_version(1, created_at=10)
    summary = await allocate_for_document(
        conn,
        metadata=_metadata(
            document_profile={"caliber": {"value": "summary", "source": "cover_title"}}
        ),
        checksum=CHECKSUM,
        org_records=ORG_RECORDS,
        document_version_id=1,
    )
    assert summary["slot_caliber"] == "summary"


@pytest.mark.asyncio
async def test_missing_profile_keeps_caliber_unknown():
    conn = FakeSlotConnection()
    conn.seed_version(1, created_at=10)
    summary = await allocate_for_document(
        conn,
        metadata=_metadata(),
        checksum=CHECKSUM,
        org_records=ORG_RECORDS,
        document_version_id=1,
    )
    assert summary["slot_caliber"] == "unknown"


@pytest.mark.asyncio
async def test_unrecognized_caliber_is_not_treated_as_a_value():
    """画像识别不出时 value 为 None，不能当成一种口径写进去。"""
    conn = FakeSlotConnection()
    service = MaterialSlotService(conn)
    decision = _decide(_metadata(document_profile={"caliber": {"value": None}}))
    row = await service.upsert_from_decision(decision, caliber="unknown")
    assert conn._slot_by_id(row["id"])["caliber"] == "unknown"


# ==== 5. 行为级接线：流水线真的把画像传给了结构化入库 =======================


class _FakePdf:
    def __init__(self, page_count: int) -> None:
        self.pages = [object()] * page_count

    def __enter__(self) -> "_FakePdf":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


async def _run_pipeline_capturing_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple:
    """跑一次真实流水线，把交给结构化入库的 metadata 原样取回来。

    返回 ``(metadata, job_dir)``：job_dir 用于回读状态文件，核对传过去的画像
    与落盘的画像确实是同一个。
    """
    job_dir = tmp_path / "job-profile-handoff"
    job_dir.mkdir()
    (job_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "status": "queued",
            "mode": "rules",
            "use_local_rules": True,
            "use_ai_assist": False,
            "report_year": 2025,
            "report_kind": "final",
            "checksum": CHECKSUM,
        },
    )

    page_text = "上海市普陀区人民政府石泉路街道办事处2025年度部门决算\n" * 6
    monkeypatch.setattr(pipeline_mod.pdfplumber, "open", lambda _path: _FakePdf(1))
    monkeypatch.setattr(
        pipeline_mod, "_extract_visible_text_from_page", lambda _page: page_text
    )
    monkeypatch.setattr(pipeline_mod, "_extract_tables_from_page", lambda _page: [])
    monkeypatch.setattr(
        pipeline_mod, "persist_analysis_job_snapshot", AsyncMock(return_value=True)
    )

    captured: Dict[str, Any] = {}
    ingest_mock = AsyncMock(
        return_value={"status": "skipped", "review_item_count": 0, "review_items": []}
    )

    async def _capture(*, job_id: str, pdf_path: Path, metadata: Dict[str, Any]):
        captured.update(metadata)
        return await ingest_mock(job_id=job_id, pdf_path=pdf_path, metadata=metadata)

    monkeypatch.setattr(pipeline_mod, "run_structured_ingest", _capture)

    await pipeline_mod._run_pipeline_inner(job_dir)
    return captured, job_dir


@pytest.mark.asyncio
async def test_pipeline_hands_the_document_profile_to_structured_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """行为断言：画像确实出现在结构化入库收到的 metadata 里。

    这条比"源码里写了 document_profile=..."强得多——字段写在别处、
    或者被中间层丢掉，都不会让这条用例通过。
    """
    captured, _job_dir = await _run_pipeline_capturing_metadata(tmp_path, monkeypatch)

    assert "document_profile" in captured, "画像没有进入结构化入库 metadata"
    profile = captured["document_profile"]
    assert isinstance(profile, dict)
    # 冲突信息与年度候选都在，槽位归属才可能判出冲突
    assert "conflicts" in profile
    assert "report_kind" in profile and "report_year" in profile
    assert "caliber" in profile
    # 既有的标量字段一个没丢
    for key in ("organization_id", "organization_name", "doc_type", "report_year", "report_kind"):
        assert key in captured, f"元数据字段 {key} 丢失"


@pytest.mark.asyncio
async def test_pipeline_profile_is_the_same_object_as_the_recorded_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """传给台账的画像必须与分析结果里记录的画像是同一个。

    "同一个"是这次修复的核心要求：入库侧不许重新解析 PDF、也不许另造一套画像。
    如果哪天有人在入库侧新建了一个画像，这里会因为 resolver 之外还比对了
    报告类型与年度而失败——两套画像只要取值不同就说明已经分叉。
    """
    captured, job_dir = await _run_pipeline_capturing_metadata(tmp_path, monkeypatch)
    handed_over = captured["document_profile"]

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    recorded = (payload.get("result") or {}).get("meta", {}).get("document_profile")
    assert isinstance(recorded, dict), "状态文件里没有记录画像，无法比对同源"

    for key in ("report_kind", "report_year", "subject_level", "caliber", "profile_status"):
        assert handed_over.get(key) == recorded.get(key), f"画像字段 {key} 在两处不一致"


def test_structured_ingest_output_field_is_untouched():
    """既有的**输出**字段 document_profile（结构化解析类型字符串）不能被改成画像。

    那是既有接口契约，``canonical_nine_table`` 这类取值前端已经在消费；
    本次新增的是**输入** metadata 里的业务画像，两者是不同的东西。
    """
    from src.services import structured_ingest_runner as runner

    payload = runner._detect_document_profile(Path("上海市普陀区某单位2024年度部门决算.pdf"))
    assert payload in {"canonical_nine_table", "execution_budget_packet", "narrative_report"}
