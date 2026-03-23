from src.schemas.issues import AnalysisConfig, JobContext
from src.services.ai_findings import AIFindingsService
from src.services.ai_issue_interpreter import interpret_ai_issue, normalize_ai_severity


def test_interpret_ai_issue_builds_readable_ratio_message() -> None:
    interpreted = interpret_ai_issue(
        {
            "problem_type": "同比百分比错误",
            "quote": "同比增长12.5%",
            "expected": "8.3%",
            "actual": "12.5%",
            "difference": "4.2个百分点",
            "table_or_section": "一般公共预算支出情况说明",
            "severity": "high",
            "confidence": 0.84,
        },
        page_number=12,
        fallback_rule_id="AI-SEM-007",
    )

    assert interpreted["rule_id"] == "AI-RATIO-007"
    assert interpreted["title"] == "同比/比例复算异常"
    assert "第12页“一般公共预算支出情况说明”存在同比、占比或完成率复算异常" in interpreted["message"]
    assert "当前写为 12.5%，按当前证据复核应为 8.3%" in interpreted["message"]
    assert interpreted["suggestion"] == "请按表内金额重新复算同比、占比或完成率，并同步修正文中百分比表述。"
    assert interpreted["location"]["section"] == "一般公共预算支出情况说明"
    assert interpreted["metrics"]["expected"] == "8.3%"
    assert interpreted["metrics"]["actual"] == "12.5%"
    assert interpreted["metrics"]["diff"] == "4.2个百分点"


def test_normalize_ai_severity_maps_manual_review_to_medium() -> None:
    assert normalize_ai_severity("manual_review") == "manual_review"


def test_convert_ai_issue_accepts_structured_payload_without_title_or_message() -> None:
    service = AIFindingsService(AnalysisConfig())

    item = service._convert_ai_issue(
        {
            "problem_type": "模板残留",
            "severity": "manual_review",
            "quote": "XX单位",
            "context": "预算编制说明：XX单位请补充后发布。",
            "page": 3,
            "table_or_section": "预算编制说明",
        },
        JobContext(
            job_id="job-ai-interpret",
            pdf_path="",
            page_texts=[],
            meta={},
        ),
        2,
    )

    assert item is not None
    assert item.rule_id == "AI-TPL-002"
    assert item.title == "待人工复核：模板残留或占位符未清理"
    assert item.severity == "manual_review"
    assert item.severity_label == "待人工复核"
    assert item.location["section"] == "预算编制说明"
    assert "第3页“预算编制说明”仍存在模板残留、占位符或未清理内容" in item.message
    assert item.suggestion == "请结合原表、原文和截图证据进行人工复核后确认是否需要修改。"
    assert "待人工复核" in item.tags
