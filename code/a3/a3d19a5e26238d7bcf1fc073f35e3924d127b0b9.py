from pathlib import Path
import sys,pytest
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import src.services.pdf_parser as parser
print("BASELINE",parser.__file__,parser.PARSER_FINGERPRINT,flush=True)
raise SystemExit(pytest.main(["-q","tests/test_budget_intake_20260929.py::test_finals_unresolved_total_le_11","tests/test_engine_repair_20260927.py::test_unresolved_total_le_15","tests/test_review_known_regressions.py::test_t7_thousands_amount_and_yoy_change_are_not_false_mismatches"]))
