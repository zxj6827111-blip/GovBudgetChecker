"""IR-18 评分器回归：分母必须由真值独立固定。

背景（2026-10-25 复核 F3）：v1 评分器只在命中表/行之后才累计金额与空白格数，
漏表漏行会同步缩小分母，出现“漏得越多、召回越高”。9 条种子实测分母 152
而非 166，金额格召回被虚报为 0.9211（实为 0.8434）。
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "score_cell_truth_regression", ROOT / "scripts" / "score_cell_truth.py"
)
scorer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(scorer)


def snapshot(rows, cells, mappings=None, code="FIN_02_income"):
    return {"tables": [{
        "table_code": code, "page_start": 1, "page_end": 1,
        "rows": rows, "cells": cells, "mappings": mappings or [],
    }]}


def truth(rows, code="FIN_02_income"):
    return {"sample_id": "probe", "table_code": code, "physical_page": 1, "rows": rows}


TWO_ROWS = [
    {"code": "206", "cells": {"total_actual": "1.00"}},
    {"code": "221", "cells": {"total_actual": "2.00"}},
]


def test_missing_row_still_counts_in_the_amount_denominator():
    result = scorer.score_sample(truth(TWO_ROWS), snapshot(
        [["206", "1.00"]],
        [{"row_idx": 0, "col_idx": 0, "raw_text": "206"},
         {"row_idx": 0, "col_idx": 1, "raw_text": "1.00"}],
        [{"source_col_idx": 1, "canonical_measure": "total_actual"}],
    ))
    assert (result["amount_hits"], result["amount_total"]) == (1, 2)


def test_missing_table_still_counts_every_truth_cell():
    result = scorer.score_sample(truth(TWO_ROWS), {"tables": []})
    assert (result["amount_hits"], result["amount_total"]) == (0, 2)
    assert (result["row_hits"], result["row_total"]) == (0, 2)


def test_unmapped_column_is_not_counted_as_preserved_blank():
    rows = [{"code": "206", "cells": {"total_actual": "BLANK"}}]
    result = scorer.score_sample(truth(rows), snapshot(
        [["206"]], [{"row_idx": 0, "col_idx": 0, "raw_text": "206"}],
    ))
    assert (result["blank_hits"], result["blank_total"]) == (0, 1)


def test_a_real_empty_cell_counts_as_preserved_blank():
    rows = [{"code": "206", "cells": {"total_actual": "BLANK"}}]
    result = scorer.score_sample(truth(rows), snapshot(
        [["206", ""]],
        [{"row_idx": 0, "col_idx": 0, "raw_text": "206"},
         {"row_idx": 0, "col_idx": 1, "raw_text": ""}],
        [{"source_col_idx": 1, "canonical_measure": "total_actual"}],
    ))
    assert (result["blank_hits"], result["blank_total"]) == (1, 1)


def test_cache_key_includes_the_parser_fingerprint():
    """同一原件在解析器变化后必须重新解析，不能复用旧快照。"""
    from src.services.pdf_parser import PARSER_FINGERPRINT

    assert PARSER_FINGERPRINT
    assert scorer.SCORER_VERSION == "cell-truth-v2"
