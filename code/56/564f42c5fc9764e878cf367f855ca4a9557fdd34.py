"""来源与章节边界的机制反例；不计入真实材料验收。"""

from decimal import Decimal
from src.schemas.parse_artifact import ParsedTextBlock, ParseArtifact
from src.services.fiscal_narrative import reconstruct_narrative
from src.services.fiscal_input_builder import FiscalInputBuilder


def block(text, page=1, y=20, index=0):
    return ParsedTextBlock(f"p{page}-b{index}-{y}", page, text, (20, y, 500, y + 10))


def claims(*texts):
    entries = reconstruct_narrative([block(t, y=20 + i * 20) for i, t in enumerate(texts)])
    return entries, [claim for e in entries for claim in e["amount_claims"]]


def test_typed_mentions_keep_sign_precision_and_invalid_grouping():
    entries, values = claims(
        "一、收入情况",
        "单位：万元",
        "2024年收入−12.30万元，占12.30%，车辆3辆，科目编码201-39-99。",
        "支出（2.50）万元；费用1,23.00万元；支出0万元。",
    )
    assert [c["raw_text"] for c in values] == ["−12.30万元", "（2.50）万元", "1,23.00万元", "0万元"]
    assert [c["normalized_value_wan"] for c in values] == ["-12.30", "-2.50", None, "0"]
    kinds = {m["kind"] for e in entries for m in e["numeric_mentions"]}
    assert {"date", "ratio", "quantity", "code", "invalid"}.issubset(kinds)
    assert all("".join(s["quote"] for s in c["sources"]).strip() == c["raw_text"] for c in values)
    assert values[0]["amount_year"] == 2024
    assert values[2]["status"] == "requires_regional_read"


def test_explicit_unit_conflict_kept_and_new_section_clears_default():
    _, values = claims(
        "一、收入情况", "单位：万元", "收入120000元。", "二、支出情况", "支出12.00。"
    )
    assert len(values) == 1 and values[0]["unit_conflicts"]
    assert values[0]["normalized_value_wan"] is None


def test_new_page_does_not_inherit_previous_item_amount_or_unit():
    entries = reconstruct_narrative(
        [
            block("一、收入说明"),
            block("（一）财政拨款，单位：万元", y=40),
            block("收入12.00", y=60),
            block("支出99.00。", page=2),
        ]
    )
    assert entries[-1]["continuation_status"] == "unproven_page_boundary"
    assert entries[-1]["item_uid"] is None and not entries[-1]["amount_claims"]
    assert entries[-2]["amount_claims"][0]["normalized_value_wan"] == "12.00"


def test_explicit_same_item_can_continue_unit_and_keeps_distinct_sources():
    entries = reconstruct_narrative(
        [block("（一）财政拨款，单位：万元"), block("（一）（续）收入12.00。", page=2)]
    )
    assert entries[1]["continuation_status"] == "explicit_same_item"
    assert entries[1]["item_uid"] == entries[0]["item_uid"]
    assert entries[1]["amount_claims"][0]["normalized_value_wan"] == "12.00"
    assert entries[1]["amount_claims"][0]["sources"][0]["page"] == 2


def test_next_section_cannot_supply_reason_or_amount_to_previous_item():
    entries, values = claims(
        "一、收入说明", "（一）收入12万元。", "二、支出说明", "因新增项目支出99万元。"
    )
    first, second = values
    assert first["raw_text"] == "12万元" and second["raw_text"] == "99万元"
    assert entries[1]["item_uid"] and entries[-1]["item_uid"] is None
    assert entries[1]["section_uid"] != entries[-1]["section_uid"]


def test_clause_periods_and_explicit_years_remain_separate_from_report_year():
    artifact = ParseArtifact(
        "a" * 64, text_blocks=[block("2023年上年收入12万元，本年支出20万元。")]
    )
    FiscalInputBuilder().facts(artifact, org_name="测试局", fiscal_year=2024, report_kind="final")
    values = artifact.narrative_entries[0]["amount_claims"]
    assert [(c["report_year"], c["amount_year"], c["period"]) for c in values] == [
        (2024, 2023, "上年"),
        (2024, 2024, "本年"),
    ]
    assert ParseArtifact.from_dict(artifact.to_dict()).to_dict() == artifact.to_dict()


def test_sign_separated_by_space_is_invalid_not_positive():
    _, values = claims("支出− 12.30万元。")
    assert len(values) == 1 and values[0]["raw_text"] == "− 12.30万元"
    assert values[0]["normalized_value_wan"] is None


def test_dash_na_and_placeholder_do_not_become_zero():
    entries, values = claims("单位：万元", "经费无此项，收入XX万元，支出—。")
    assert not values
    assert {"not_applicable", "placeholder", "dash"}.issubset(
        {m["kind"] for e in entries for m in e["numeric_mentions"]}
    )


def test_paragraph_claim_source_spans_refer_to_original_fragments():
    entries = reconstruct_narrative(
        [block("（一）收入情况"), block("收入12.30万元，", y=35), block("其中补助2万元。", y=50)]
    )
    entry = entries[-1]
    assert len(entry["sources"]) == 3 and len(entry["amount_claims"]) == 2
    for claim in entry["amount_claims"]:
        assert Decimal(claim["normalized_value_wan"]) > 0
        for ref in claim["sources"]:
            source = next(s for s in entry["sources"] if s["block_uid"] == ref["block_uid"])
            assert source["text"][ref["start_in_block"] : ref["end_in_block"]] == ref["quote"]
            assert ref["granularity"] == "fragment"  # 不虚构逐字坐标。


def test_overlapping_native_and_visual_are_alternatives_not_concatenated_digits():
    native = block("收入12万元。")
    visual = ParsedTextBlock("ocr_tile_a:block-0", 1, "收入12万元。", native.bbox, 0.995, "ocr")
    entries = reconstruct_narrative([native, visual])
    assert len(entries) == 1 and entries[0]["text"] == "收入12万元。"
    assert [c["normalized_value_wan"] for c in entries[0]["amount_claims"]] == ["12"]
    assert entries[0]["alignment_candidates"][0]["status"] == "agreed"
    visual.text = "收入72万元。"
    entries = reconstruct_narrative([native, visual])
    assert entries[0]["amount_claims"][0]["raw_text"] == "12万元"
    assert entries[0]["amount_claims"][0]["recognition_conflict"] is True
    assert entries[0]["amount_claims"][0]["status"] == "requires_regional_read"
    assert entries[0]["alignment_candidates"][0]["text"] == "收入72万元。"


def test_one_visual_fragment_can_reference_several_native_words_without_fake_char_boxes():
    blocks = [
        ParsedTextBlock("n1", 1, "收入", (20, 20, 40, 30)),
        ParsedTextBlock("n2", 1, "12万元。", (40, 20, 80, 30)),
        ParsedTextBlock("ocr_tile_a:block-0", 1, "收入12万元。", (20, 20, 80, 30), 0.995, "ocr"),
    ]
    entries = reconstruct_narrative(blocks)
    assert len(entries) == 1 and entries[0]["text"] == "收入12万元。"
    assert entries[0]["amount_claims"][0]["normalized_value_wan"] == "12"
    assert all(
        ref["granularity"] == "fragment" for ref in entries[0]["amount_claims"][0]["sources"]
    )
