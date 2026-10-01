"""ZC-02 阶段验收测试：目标表绑定、跨页身份、三公列序与主体识别。

对应缺陷：SYS-02（目标页最大表 + 盲拼下一页）、SYS-04（三公固定取位）、
SYS-17（连写封面"财政局"覆盖"部门决算"），并接入本地 OCR（SYS-10 基建）。

必须验证（任务包 §三 ZC-02）：
- 同页大小两表：选目标表，不选最大表；
- 三页以上续表：所有适用行可追溯，不能只读前两页；
- 下一页是另一张表：不拼成续表；
- 三公合法列序变化：不产生错配报警；
- 连写封面主体识别 + 上海市名称替换（普陀区/虹口区）后规则结论不变；
- 混合 PDF / 超过十页 / 旋转：产物完整、页状态如实；
- OCR：默认关闭；启用后逐页补识别、分数原样保留、原生与 OCR 冲突不覆盖。
（真实 PaddleOCR 容器启动与性能验证需 Docker 环境，本机缺环境 → 未验收。）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from src.engine.budget_rules import _get_budget_table_rows
from src.engine.common_rules import CMM001_ThreePublicNarrativeConsistency
from src.engine.pipeline import run_rules_with_outcomes
from src.engine.rules_v33 import build_document
from src.services.document_profile_resolver import resolve_document_profile
from src.services.pdf_parser import PDFParser
from src.services.ocr_client import OcrBlock, OcrPageResult, KEY_NUMBER_MIN_SCORE


# ---------------------------------------------------------------------------
# 1. 目标表绑定与跨页恢复（SYS-02）
# ---------------------------------------------------------------------------


def test_same_page_two_tables_binds_target_not_largest() -> None:
    """反例：目标表较小、无关采购表较大——选目标表。"""
    small = [["项目", "预算数", "项目", "预算数"], ["收入总计", "100.00", "支出总计", "100.00"]]
    large = [
        ["无关项目表", "金额"],
        ["甲", "500.00"],
        ["乙", "500.00"],
        ["合计", "1000.00"],
    ]
    doc = build_document(
        "budget.pdf", ["2026年部门财务收支预算总表\n单位：万元"], [[small, large]], 0
    )
    rows, _page = _get_budget_table_rows(doc, {"BUD_T1": [1]}, "BUD_T1")
    assert rows == small


def test_multi_table_page_without_evidence_is_fail_closed() -> None:
    """同页多表且无签名证据：不猜最大表，返回 None（转未完成）。"""
    small = [["甲", "1"], ["乙", "2"]]
    large = [["丙", "3"], ["丁", "4"], ["戊", "5"]]
    doc = build_document("budget.pdf", ["某页"], [[small, large]], 0)
    rows, _page = _get_budget_table_rows(doc, {"BUD_T1": [1]}, "BUD_T1")
    assert rows is None


def test_continuation_across_three_pages_is_recovered() -> None:
    """三页续表：所有适用行可追溯，不能只读前两页。"""
    page1 = [
        ["项目", "预算数"],
        ["办公经费", "100.00"],
        ["印刷费", "20.00"],
    ]
    page2 = [
        ["项目", "预算数"],
        ["差旅费", "30.00"],
        ["培训费", "10.00"],
    ]
    page3 = [
        ["项目", "预算数"],
        ["维修费", "60.00"],
        ["专用材料费", "5.00"],
        ["合计", "225.00"],
    ]
    doc = build_document(
        "budget.pdf",
        ["预算支出表", "续", "续"],
        [[page1], [page2], [page3]],
        0,
    )
    rows, _page = _get_budget_table_rows(doc, {"BUD_T3": [1]}, "BUD_T3")
    assert rows is not None
    joined = "\n".join("".join(row) for row in rows)
    for token in ("办公经费", "差旅费", "培训费", "维修费", "专用材料费", "合计"):
        assert token in joined, f"第 3 页的行必须被恢复：缺 {token}"


def test_next_page_new_table_is_not_merged() -> None:
    """下一页是另一张表（独立表头+合计）：身份冲突，不拼成续表。"""
    target_page = [
        ["项目", "预算数"],
        ["办公经费", "100.00"],
        ["印刷费", "20.00"],
    ]
    other_page = [
        ["政府采购预算表", "金额"],
        ["办公设备购置", "60.00"],
        ["合计", "60.00"],
    ]
    doc = build_document(
        "budget.pdf",
        ["2026年部门收支预算表", "政府采购预算表"],
        [[target_page], [other_page]],
        0,
    )
    rows, _page = _get_budget_table_rows(doc, {"BUD_T1": [1]}, "BUD_T1")
    assert all(row not in rows for row in other_page)


def test_data_only_continuation_with_same_columns_is_merged() -> None:
    """无重复表头的续页：列数一致 + 数据形态 → 认可为延续（兼容表头映射）。"""
    page1 = [
        ["项目", "预算数"],
        ["办公经费", "100.00"],
    ]
    page2 = [
        ["差旅费", "30.00"],
        ["培训费", "10.00"],
    ]
    doc = build_document("budget.pdf", ["t", "续"], [[page1], [page2]], 0)
    rows, _page = _get_budget_table_rows(doc, {"BUD_T3": [1]}, "BUD_T3")
    joined = "".join(str(cell) for row in rows or [] for cell in row)
    assert "差旅费" in joined


# ---------------------------------------------------------------------------
# 2. 三公语义列映射（SYS-04）
# ---------------------------------------------------------------------------


def _three_public_doc():
    text = (
        "2026年部门“三公”经费和机关运行经费预算表\n单位：万元\n"
        "三公经费预算30.00万元；因公出国（境）费0.00万元；公务用车购置及运行费20.00万元；"
        "公务用车购置费0.00万元；公务用车运行费20.00万元；公务接待费10.00万元。"
    )
    table = [
        [
            "合计",
            "因公出国（境）费",
            "公务用车购置及运行费",
            "购置费",
            "运行费",
            "公务接待费",
            "机关运行经费",
        ],
        ["30.00", "0.00", "20.00", "0.00", "20.00", "10.00", "50.00"],
    ]
    return build_document("budget.pdf", [text], [[table]], 0)


def test_three_public_legal_column_order_produces_no_mismatch() -> None:
    """合法列序「合计、出国、公车小计、购置、运行、接待」不产生错配报警。"""
    doc = _three_public_doc()
    issues, outcomes = run_rules_with_outcomes(
        doc, report_kind="budget", rules=[CMM001_ThreePublicNarrativeConsistency()]
    )
    assert issues == [], f"合法列序不得错配：{[i.message for i in issues]}"
    assert outcomes[0].to_dict()["status"] == "pass"


def test_three_public_canonical_column_order_still_verified() -> None:
    """对照正例：真实差异仍可检出（语义映射不把该报的压掉）。"""
    text = (
        "2026年部门“三公”经费预算表\n"
        "三公经费预算30.00万元；因公出国（境）费0.00万元；公务接待费10.00万元。"
    )
    table = [
        ["合计", "因公出国（境）费", "公务接待费"],
        ["30.00", "0.00", "8.00"],
    ]
    doc = build_document("budget.pdf", [text], [[table]], 0)
    issues, _outcomes = run_rules_with_outcomes(
        doc, report_kind="budget", rules=[CMM001_ThreePublicNarrativeConsistency()]
    )
    assert any("不一致" in i.message for i in issues), "接待费 10 vs 8 的真实差异必须报出"


def test_three_public_value_conflict_across_orders_detected() -> None:
    """列序打乱后数值差异仍按字段对应（而非按位置错配）。"""
    text = "2026年“三公”经费预算表\n三公经费预算30.00万元；公务接待费10.00万元。"
    # 接待列放到第二列（旧位置映射会把它当成出国费）
    table = [
        ["合计", "公务接待费", "因公出国（境）费"],
        ["30.00", "9.00", "0.00"],
    ]
    doc = build_document("budget.pdf", [text], [[table]], 0)
    issues, _outcomes = run_rules_with_outcomes(
        doc, report_kind="budget", rules=[CMM001_ThreePublicNarrativeConsistency()]
    )
    assert any("接待" in i.message and "不一致" in i.message for i in issues), (
        "接待费差异必须对应到接待字段，而不是错配到其他字段"
    )
    assert not any("出国" in i.message and "不一致" in i.message for i in issues)


# ---------------------------------------------------------------------------
# 3. 主体识别（SYS-17）与地区替换不变性
# ---------------------------------------------------------------------------


def test_conjoined_cover_keeps_department_level() -> None:
    """连写封面"上海市普陀区财政局2024年度部门决算"：识别为部门材料。"""
    profile = resolve_document_profile(
        path="上海市普陀区财政局2024年度部门决算.pdf",
        page_texts=["上海市普陀区财政局2024年度部门决算"],
    )
    assert profile.subject_level.value == "department"


def test_government_final_is_still_government() -> None:
    """对照：政府总决算不被降级为部门材料。"""
    profile = resolve_document_profile(
        path="普陀区2024年度政府决算.pdf",
        page_texts=["普陀区2024年度政府决算"],
    )
    assert profile.subject_level.value == "government"


@pytest.mark.parametrize("district", ["普陀区", "虹口区", "浦东新区"])
def test_district_name_substitution_keeps_profile_stable(district: str) -> None:
    """上海市名称替换为各区名称后，业务画像结论不变（共用模板规则）。"""
    profile = resolve_document_profile(
        path=f"上海市{district}教育局2024年度部门决算.pdf",
        page_texts=[f"上海市{district}教育局2024年度部门决算"],
    )
    assert profile.subject_level.value == "department"
    assert profile.report_kind.value == "final"


# ---------------------------------------------------------------------------
# 4. 超过十页 / 旋转页 / 混合页产物完整性
# ---------------------------------------------------------------------------


def _make_pdf(tmp_path: Path, page_lines: List[List[str]], rotation: int = 0) -> Path:
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "artifact.pdf"
    document = fitz.open()
    for lines in page_lines:
        page = document.new_page()
        if rotation:
            page.set_rotation(rotation)
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontsize=12, fontname="china-s")
            y += 18
    document.save(str(path))
    document.close()
    return path


def test_artifact_covers_more_than_ten_pages(tmp_path: Path) -> None:
    """超过十页：页状态与产物不缺页。"""
    pages = [[f"第{index}页 内容行"] for index in range(1, 13)]
    pdf_path = _make_pdf(tmp_path, pages)
    artifact = PDFParser(None).build_parse_artifact(str(pdf_path))
    assert artifact.page_count == 12
    assert len(artifact.page_states) == 12
    assert [state.page_number for state in artifact.page_states] == list(range(1, 13))
    assert any(block.text == "第1页" for block in artifact.text_blocks)
    restored = type(artifact).from_dict(artifact.to_dict())
    assert len(restored.text_blocks) == len(artifact.text_blocks)


def test_rotated_page_records_rotation(tmp_path: Path) -> None:
    """旋转页：旋转信息如实登记，产物不因旋转而失败。"""
    pdf_path = _make_pdf(tmp_path, [["收入支出决算总表", "合计 100.00"]], rotation=90)
    artifact = PDFParser(None).build_parse_artifact(str(pdf_path))
    assert artifact.page_count == 1
    assert artifact.page_states[0].rotation == 90


def test_mixed_pdf_records_scanned_pages(tmp_path: Path) -> None:
    """混合 PDF：无文本层页如实标 is_scanned，有文本页不受影响。"""
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "mixed.pdf"
    document = fitz.open()
    text_page = document.new_page()
    text_page.insert_text((72, 72), "收入支出决算总表", fontsize=12, fontname="china-s")
    document.new_page()  # 空页（无文本层）
    document.save(str(path))
    document.close()

    artifact = PDFParser(None).build_parse_artifact(str(path))
    states = {state.page_number: state for state in artifact.page_states}
    assert states[1].has_text_layer is True
    assert states[1].is_scanned is False
    assert states[2].has_text_layer is False
    assert states[2].is_scanned is True


# ---------------------------------------------------------------------------
# 5. OCR：默认关闭；启用后补识别、冲突保留、分数透传
# ---------------------------------------------------------------------------


def test_ocr_disabled_by_default(monkeypatch) -> None:
    """默认不启用 OCR（与 AI 同纪律：不配置就不调用外部服务）。"""
    from src.services.ocr_client import ocr_enabled
    from src.services.ocr_pipeline import maybe_ocr_scanned_pages

    monkeypatch.delenv("OCR_ENABLED", raising=False)
    assert ocr_enabled() is False

    import asyncio

    summary = asyncio.run(
        maybe_ocr_scanned_pages("x.pdf", {"scanned_pages": [1, 2]}, None)
    )
    assert summary is None


def test_ocr_block_score_passthrough_and_key_threshold() -> None:
    """识别分数原样保留；0.98 是确定性判定门槛常量，不做改写。"""
    block = OcrBlock(text="合计", score=0.9712, bbox=[1.0, 2.0, 3.0, 4.0], page_number=2)
    payload = block.to_dict()
    assert payload["score"] == 0.9712  # 分数不被四舍五入或校准
    assert KEY_NUMBER_MIN_SCORE == 0.98
    page = OcrPageResult(page_number=2, blocks=[block])
    assert page.ok and "合计" in page.page_text()


def test_ocr_result_normalisation_and_error_isolation() -> None:
    """服务端异常块不致命；错误页带 error 不抛异常。"""
    from src.services.ocr_client import _normalise_blocks

    blocks = _normalise_blocks(
        [
            {"text": "合计 123.45", "score": 0.99, "bbox": [0, 0, 10, 10]},
            {"text": "坏块", "score": "not-a-number"},
            "not-a-dict",
        ],
        page_number=3,
    )
    assert len(blocks) == 2
    assert blocks[1].score == 0.0

    page = OcrPageResult(page_number=4, error="TimeoutError: x")
    assert page.ok is False
    assert page.page_text() == ""


def test_ocr_pipeline_runs_when_enabled_and_preserves_native(monkeypatch, tmp_path) -> None:
    """启用 OCR：扫描页被识别；有残缺原生文本层时冲突如实记录、不覆盖。"""
    import asyncio

    from src.services import ocr_pipeline
    from src.schemas.parse_artifact import ParseArtifact, ParsePageState

    monkeypatch.setenv("OCR_ENABLED", "true")
    monkeypatch.setenv("OCR_MODEL_FINGERPRINT", "a" * 64)

    # 2 页 PDF：第 1 页有少量原生文本（残缺），第 2 页纯扫描
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "scan.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "残缺文本", fontsize=12, fontname="china-s")
    document.new_page()
    document.save(str(pdf_path))
    document.close()

    artifact = ParseArtifact(source_file_hash="x" * 64, page_count=2)
    artifact.page_states = [
        ParsePageState(page_number=1, has_text_layer=True, text_chars=4),
        ParsePageState(page_number=2, has_text_layer=False, text_chars=0, is_scanned=True),
    ]

    captured: Dict[str, Any] = {}

    async def fake_health():
        return {"ok": True, "model_fingerprint": "a" * 64}

    monkeypatch.setattr(ocr_pipeline, "check_ocr_health", fake_health)

    async def fake_ocr_pages(images, page_numbers, **_kwargs):
        captured["pages"] = list(page_numbers)
        return [
            OcrPageResult(
                page_number=page_numbers[0],
                blocks=[
                    OcrBlock(
                        text="收入支出决算总表 合计 100.00",
                        score=0.99,
                        bbox=[300.0, 300.0, 800.0, 340.0],
                    )
                ],
                tables=[{"rows": [["项目", "金额"], ["合计", "100.00"]], "bbox": [280.0, 280.0, 900.0, 600.0]}],
            ),
            OcrPageResult(page_number=page_numbers[1], error="TimeoutError"),
        ]

    monkeypatch.setattr(ocr_pipeline, "ocr_pages", fake_ocr_pages)

    summary = asyncio.run(
        ocr_pipeline.maybe_ocr_scanned_pages(
            str(pdf_path),
            {"scanned_pages": [1, 2]},
            artifact,
        )
    )

    assert summary is not None and summary["status"] == "partial"
    assert captured["pages"] == [1, 2]
    assert summary["failed_pages"] == [2]
    # 原生文本层与 OCR 并存 → 冲突登记，而非静默覆盖
    assert summary["conflicts"] == [
        {"page": 1, "native_chars": 4, "ocr_chars": len("收入支出决算总表 合计 100.00")}
    ]
    states = {state.page_number: state for state in artifact.page_states}
    assert states[1].note == "ocr_applied"
    assert states[1].text_chars == 4, "原生文本层不得被 OCR 改写"
    assert states[1].ocr_status == "candidate_only"
    assert len(artifact.text_blocks) == 1
    assert artifact.text_blocks[0].confidence == 0.99
    assert len(artifact.ocr_table_candidates) == 1
    assert artifact.ocr_table_candidates[0].verification_status == "unverified"


def test_ocr_requires_pinned_model_fingerprint(monkeypatch) -> None:
    """未固定模型清单指纹时，不调用 OCR 服务或将候选用于规则。"""
    import asyncio

    from src.services.ocr_pipeline import maybe_ocr_scanned_pages

    monkeypatch.setenv("OCR_ENABLED", "true")
    monkeypatch.delenv("OCR_MODEL_FINGERPRINT", raising=False)
    result = asyncio.run(
        maybe_ocr_scanned_pages("not-opened.pdf", {"scanned_pages": [1]})
    )
    assert result is not None
    assert result["status"] == "model_fingerprint_missing"
    assert result["usable_for_rules"] is False


def test_ocr_table_candidate_serializes_without_becoming_rule_table() -> None:
    """OCR 表格候选保留原始行与坐标空间，未核实前不冒充正式表实例。"""
    from src.schemas.parse_artifact import OcrTableCandidate, ParseArtifact

    artifact = ParseArtifact(
        source_file_hash="a" * 64,
        ocr_table_candidates=[
            OcrTableCandidate(
                candidate_uid="ocr-p2-t0",
                page_number=2,
                rows=[["项目", "金额"], ["合计", "100.00"]],
                render_bbox=(10.0, 20.0, 300.0, 200.0),
                page_bbox=None,
                coordinate_transform_id="ocr_render_page_2",
            )
        ],
    )
    restored = ParseArtifact.from_dict(artifact.to_dict())
    assert restored.table_fragments == []
    assert restored.ocr_table_candidates[0].rows[1][1] == "100.00"
    assert restored.ocr_table_candidates[0].page_bbox is None
    assert restored.ocr_table_candidates[0].verification_status == "unverified"


def test_ppstructure_response_preserves_table_rows_and_confidence() -> None:
    """PP-StructureV3 的 OCR 与表格输出可以分别归一，不改写识别分数。"""
    from ocr_service.app import _render_prediction

    result = _render_prediction(
        {
            "overall_ocr_res": {
                "rec_texts": ["合计"],
                "rec_scores": [0.98123],
                "rec_polys": [[[1, 2], [20, 2], [20, 12], [1, 12]]],
            },
            "parsing_res_list": [
                {
                    "block_label": "table",
                    "block_bbox": [5, 10, 90, 50],
                    "block_content": "| 项目 | 金额 |\n|---|---|\n| 合计 | 100.00 |",
                }
            ],
        }
    )
    assert result["blocks"][0]["score"] == 0.98123
    assert result["tables"][0]["rows"] == [["项目", "金额"], ["合计", "100.00"]]


def test_model_manifest_requires_matching_complete_files(tmp_path: Path) -> None:
    """离线模型目录的每个文件均须出现在匹配的 SHA256 manifest。"""
    import hashlib

    from ocr_service.app import _verify_model_manifest

    model = tmp_path / "model.pdiparams"
    model.write_bytes(b"sample model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    manifest = tmp_path / "MANIFEST.sha256"
    manifest.write_text(f"{digest}  {model.name}\n", encoding="utf-8")
    assert _verify_model_manifest(str(tmp_path)) == hashlib.sha256(manifest.read_bytes()).hexdigest()

    model.write_bytes(b"changed model")
    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        _verify_model_manifest(str(tmp_path))


def test_ocr_client_uses_service_multipart_contract() -> None:
    """OCR 客户端按服务端 UploadFile 契约发送 multipart/form-data。"""
    import asyncio

    from src.services.ocr_client import ocr_page

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"blocks": [{"text": "合计", "score": 0.99, "bbox": [1, 2, 3, 4]}]}

    class Client:
        async def post(self, url, **kwargs):
            assert url.endswith("/ocr")
            assert kwargs["files"]["image"] == ("page-3.png", b"png-bytes", "image/png")
            assert kwargs["params"]["deskew"] == "false"
            return Response()

    result = asyncio.run(ocr_page(Client(), image_png=b"png-bytes", page_number=3))
    assert result.ok
    assert result.blocks[0].text == "合计"


def test_ocr_service_accepts_multipart_and_returns_ppstructure(monkeypatch) -> None:
    """服务端路由可接受客户端 multipart 图片，并输出文本/表格结构。"""
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    from fastapi.testclient import TestClient

    from ocr_service import app as service_module

    class Predictor:
        def predict(self, *, input):
            assert input.shape[:2] == (12, 20)
            return [
                {
                    "overall_ocr_res": {
                        "rec_texts": ["金额"],
                        "rec_scores": [0.99],
                        "rec_polys": [[[1, 1], [10, 1], [10, 8], [1, 8]]],
                    },
                    "parsing_res_list": [
                        {
                            "block_label": "table",
                            "block_bbox": [0, 0, 20, 12],
                            "block_content": "| 项目 | 金额 |\n|---|---|\n| 合计 | 1.00 |",
                        }
                    ],
                }
            ]

    def fake_load_engine():
        service_module._engine_state["predictor"] = Predictor()
        service_module._engine_state["ready"] = True

    monkeypatch.setattr(service_module, "_load_engine", fake_load_engine)
    image = np.zeros((12, 20, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    with TestClient(service_module.app) as client:
        response = client.post(
            "/ocr",
            files={"image": ("page.png", encoded.tobytes(), "image/png")},
        )
        rejected_deskew = client.post(
            "/ocr",
            files={"image": ("page.png", encoded.tobytes(), "image/png")},
            params={"deskew": "true"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["blocks"][0]["text"] == "金额"
    assert payload["tables"][0]["rows"][-1] == ["合计", "1.00"]
    assert rejected_deskew.status_code == 422
