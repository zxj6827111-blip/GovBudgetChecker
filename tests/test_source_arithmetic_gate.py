from dataclasses import replace

from src.services.fact_column_arithmetic import constrain_column_arithmetic
from tests.remediation.test_completeness_gate import ledger


def test_many_independent_source_errors_do_not_change_admission():
    facts = ledger([(str(100 + i), "0.00", "1.00") for i in range(20)])
    result = constrain_column_arithmetic(facts)
    assert all(f.recognition_status == "unverified" for f in result)
    assert len(result[0].extra_dims["source_arithmetic_anomalies"][0]["examples"]) == 20


def test_missing_header_is_independent_evidence_even_when_values_balance():
    facts = ledger([("100.00", "80.00", "20.00")])
    facts[0] = replace(facts[0], extra_dims={"source_header": ""})
    result = constrain_column_arithmetic(facts)
    assert all(f.recognition_status == "ambiguous" for f in result)
    assert result[0].extra_dims["column_arithmetic_conflicts"][0]["structure_evidence"][0]["reason"] == "source_header_missing"


def test_arithmetic_does_not_override_other_admission_rejections():
    facts = ledger([("100.00", "0.00", "1.00") for _ in range(5)])
    facts[0] = replace(facts[0], recognition_status="low_confidence")
    assert constrain_column_arithmetic(facts)[0].recognition_status == "low_confidence"
