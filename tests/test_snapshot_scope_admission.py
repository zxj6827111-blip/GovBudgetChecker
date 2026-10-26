from copy import deepcopy

import pytest

from src.services.analysis_fact_snapshot import check_publishable_fact_identity


def complete_pending_scope():
    return dict(recognition_status="unverified", org_name="甲市测试中心", org_scope=None,
                fiscal_year=2025, period="本年", fund_scope="all_funds", budget_stage="决算",
                table_code="FIN_02_income", instance_uid="one", indicator="total_actual",
                subject_code="208", normalized_value="100.00",
                extra_dims={"org_scope_pending": True, "evidence_refs": [dict(
                    file_hash="a" * 64, physical_page=2, source_cell_id="one:r2c3")]})


def test_publishable_pending_scope_preserves_null_without_waiving_other_dimensions():
    fact = complete_pending_scope()
    before = deepcopy(fact)
    check_publishable_fact_identity(fact, "a" * 64)
    assert fact == before


@pytest.mark.parametrize("field", ["org_name", "fiscal_year", "fund_scope", "budget_stage", "instance_uid", "period"])
def test_scope_marker_does_not_authorize_other_missing_identity(field):
    fact = complete_pending_scope()
    fact[field] = None
    with pytest.raises(ValueError, match="incomplete fiscal identity/source"):
        check_publishable_fact_identity(fact, "a" * 64)


def test_publication_still_rejects_unknown_source_and_unmarked_missing_scope():
    fact = complete_pending_scope()
    with pytest.raises(ValueError):
        check_publishable_fact_identity(fact, "b" * 64)
    fact["extra_dims"]["org_scope_pending"] = False
    with pytest.raises(ValueError):
        check_publishable_fact_identity(fact, "a" * 64)
