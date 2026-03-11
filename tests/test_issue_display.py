from src.engine.common_rules import CMM004_CodeMirrorConsistency
from src.engine.rules_v33 import Document
from src.schemas.issues import IssueItem
from src.utils.issue_display import build_issue_display


def test_build_issue_display_formats_legacy_code_mismatch():
    issue = {
        "title": "\u7c7b\u6b3e\u9879\u91d1\u989d\u4e0d\u4e00\u81f4\uff0c\u51712\u9879",
        "message": "\u7c7b\u6b3e\u9879\u91d1\u989d\u4e0d\u4e00\u81f4\uff0c\u51712\u9879",
        "location": {"page": 1},
        "evidence": [
            {
                "page": 1,
                "text": "208:18843998.0!=1843998.0,2080506:302986.0!=3029866.0",
            }
        ],
    }

    display = build_issue_display(issue)

    assert display["summary"] == "\u7c7b\u6b3e\u9879\u91d1\u989d\u4e0d\u4e00\u81f4\uff0c\u51712\u9879"
    assert display["page_text"] == "\u7b2c1\u9875"
    assert "\u7f16\u7801 208: 18,843,998 vs 1,843,998\uff0c\u5dee\u989d 17,000,000" in display["detail_lines"]
    assert "\u7f16\u7801 2080506: 302,986 vs 3,029,866\uff0c\u5dee\u989d 2,726,880" in display["detail_lines"]


def test_build_issue_display_formats_code_presence_difference():
    issue = {
        "title": "\u6536\u5165/\u652f\u51fa\u603b\u8868\u7c7b\u6b3e\u9879\u7f16\u7801\u96c6\u4e0d\u5b8c\u5168\u4e00\u81f4",
        "message": "\u6536\u5165/\u652f\u51fa\u603b\u8868\u7c7b\u6b3e\u9879\u7f16\u7801\u96c6\u4e0d\u5b8c\u5168\u4e00\u81f4",
        "location": {"page": 1},
        "evidence": [
            {
                "page": 1,
                "text": "income_only=2080599; expense_only=2090599",
            }
        ],
    }

    display = build_issue_display(issue)

    assert display["page_text"] == "\u7b2c1\u9875"
    assert "\u4ec5\u6536\u5165\u8868\u51fa\u73b0\u7f16\u7801: 2080599" in display["detail_lines"]
    assert "\u4ec5\u652f\u51fa\u8868\u51fa\u73b0\u7f16\u7801: 2090599" in display["detail_lines"]


def test_issue_item_populates_display_from_structured_evidence():
    item = IssueItem(
        id="rule:cmm004:test",
        source="rule",
        rule_id="CMM-004",
        severity="medium",
        title="\u7c7b\u6b3e\u9879\u7f16\u7801 208 \u91d1\u989d\u4e0d\u4e00\u81f4",
        message="\u7c7b\u6b3e\u9879\u7f16\u7801 208 \u91d1\u989d\u4e0d\u4e00\u81f4",
        evidence=[{"page": 1, "text": "code=208; income=18843998.0; expense=1843998.0; diff=17000000.0"}],
        location={"page": 1, "table": "\u6536\u5165/\u652f\u51fa\u603b\u8868", "row": "208"},
        metrics={},
        tags=[],
    )

    assert item.display is not None
    assert item.display.summary == "\u7c7b\u6b3e\u9879\u7f16\u7801 208 \u91d1\u989d\u4e0d\u4e00\u81f4"
    assert item.display.page_text == "\u7b2c1\u9875"
    assert item.display.location_text == "\u8868: \u6536\u5165/\u652f\u51fa\u603b\u8868 / \u884c: 208"
    assert "\u7f16\u7801 208: \u6536\u5165\u8868 18,843,998\uff0c\u652f\u51fa\u8868 1,843,998\uff0c\u5dee\u989d 17,000,000" in item.display.detail_lines


def test_build_issue_display_formats_assignment_style_message():
    issue = {
        "title": "T1\u4e0eT4\u6536\u5165\u603b\u8ba1\u4e0d\u4e00\u81f4",
        "message": "T1\u4e0eT4\u6536\u5165\u603b\u8ba1\u4e0d\u4e00\u81f4: T1=123.40, T4=100.00",
        "location": {"page": 7, "table": "BUD_T1/BUD_T4"},
        "evidence": [{"page": 7, "text": "T1=123.40, T4=100.00"}],
    }

    display = build_issue_display(issue)

    assert display["page_text"] == "\u7b2c7\u9875"
    assert display["location_text"] == "\u8868: BUD_T1/BUD_T4"
    assert "T1 123.40\uff0cT4 100\uff0c\u5dee\u989d 23.40" in display["detail_lines"]


def test_build_issue_display_formats_multi_page_location():
    issue = {
        "title": "\u8868\u9898\u91cd\u590d\u8fc7\u591a",
        "message": "\u8868\u9898\u91cd\u590d\u8fc7\u591a",
        "location": {"table": "BUD_T1", "pages": [11, 13, 11]},
        "evidence": [],
    }

    display = build_issue_display(issue)

    assert display["page_text"] == "\u7b2c11\u300113\u9875"
    assert display["location_text"] == "\u76f8\u5173\u9875: 11, 13 / \u8868: BUD_T1"


def test_cmm004_emits_granular_findings():
    doc = Document(
        path="sample_final.pdf",
        pages=2,
        filesize=1024,
        page_texts=[
            "\u6536\u5165\u51b3\u7b97\u8868\n208 \u6536\u5165\u4e00 18843998.0\n208 05 06 \u6536\u5165\u4e8c 302986.0\n208 05 99 \u6536\u5165\u4e09 100.0",
            "\u652f\u51fa\u51b3\u7b97\u8868\n208 \u652f\u51fa\u4e00 1843998.0\n208 05 06 \u652f\u51fa\u4e8c 3029866.0\n209 05 99 \u652f\u51fa\u4e09 120.0",
        ],
        page_tables=[[], []],
        units_per_page=[None, None],
        years_per_page=[[], []],
        anchors={},
        dominant_year=2025,
        dominant_unit="\u4e07\u5143",
    )

    issues = CMM004_CodeMirrorConsistency().apply(doc)
    messages = {issue.message for issue in issues}

    assert "\u7c7b\u6b3e\u9879\u7f16\u7801 208 \u91d1\u989d\u4e0d\u4e00\u81f4" in messages
    assert "\u7c7b\u6b3e\u9879\u7f16\u7801 2080506 \u91d1\u989d\u4e0d\u4e00\u81f4" in messages
    assert "\u7c7b\u6b3e\u9879\u7f16\u7801 2080599 \u4ec5\u51fa\u73b0\u5728\u6536\u5165\u8868" in messages
    assert "\u7c7b\u6b3e\u9879\u7f16\u7801 2090599 \u4ec5\u51fa\u73b0\u5728\u652f\u51fa\u8868" in messages

    issue_map = {issue.message: issue for issue in issues}
    assert issue_map["\u7c7b\u6b3e\u9879\u7f16\u7801 208 \u91d1\u989d\u4e0d\u4e00\u81f4"].location["page"] == 1
    assert issue_map["\u7c7b\u6b3e\u9879\u7f16\u7801 208 \u91d1\u989d\u4e0d\u4e00\u81f4"].location["table"] == "\u6536\u5165/\u652f\u51fa\u603b\u8868"
