"""Offline AI candidate contract for ZC-06.

The contract intentionally has no severity, expected/actual amount, formula, or
tolerance field. Models may propose fact candidates and semantic uncertainty;
deterministic application code owns all arithmetic and rule conclusions.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Set

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError


class CandidateFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal["raw_value", "unit", "year", "org", "scope", "indicator"]
    value: StrictStr = Field(min_length=1, max_length=500)
    evidence_ids: List[StrictStr] = Field(min_length=1, max_length=20)


class AICandidateFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: StrictStr = Field(min_length=1, max_length=200)
    check_relation_id: Optional[StrictStr] = Field(default=None, max_length=200)
    fact_ids: List[StrictStr] = Field(default_factory=list, max_length=100)
    evidence_ids: List[StrictStr] = Field(min_length=1, max_length=50)
    fact_candidates: List[CandidateFact] = Field(default_factory=list, max_length=50)
    semantic_uncertainties: List[StrictStr] = Field(default_factory=list, max_length=50)


class AICandidateBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: List[AICandidateFinding] = Field(default_factory=list, max_length=200)
    uncertainties: List[StrictStr] = Field(default_factory=list, max_length=100)


def validate_ai_candidate_batch(
    payload: Any,
    *,
    allowed_fact_ids: Iterable[str],
    allowed_evidence_ids: Iterable[str],
) -> Dict[str, Any]:
    """Validate candidates independently so one malformed item cannot erase a batch."""
    if not isinstance(payload, Mapping):
        return {
            "accepted": [],
            "rejected": [{"index": None, "error": "batch must be an object"}],
            "batch_uncertainties": [],
        }
    unexpected_batch_fields = sorted(set(payload) - {"candidates", "uncertainties"})
    if unexpected_batch_fields:
        return {
            "accepted": [],
            "rejected": [
                {
                    "index": None,
                    "error": "unexpected_batch_fields",
                    "fields": unexpected_batch_fields,
                }
            ],
            "batch_uncertainties": [],
        }
    try:
        batch = AICandidateBatch.model_validate(
            {"candidates": [], "uncertainties": payload.get("uncertainties", [])}
        )
    except ValidationError as exc:
        return {
            "accepted": [],
            "rejected": [{"index": None, "error": _validation_error_codes(exc)}],
            "batch_uncertainties": [],
        }

    allowed_facts: Set[str] = {str(item) for item in allowed_fact_ids if str(item)}
    allowed_evidence: Set[str] = {str(item) for item in allowed_evidence_ids if str(item)}
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return {
            "accepted": [],
            "rejected": [{"index": None, "error": "candidates must be an array"}],
            "batch_uncertainties": batch.uncertainties,
        }

    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for index, raw in enumerate(candidates):
        try:
            candidate = AICandidateFinding.model_validate(raw)
            unknown_facts = sorted(set(candidate.fact_ids) - allowed_facts)
            evidence_ids = set(candidate.evidence_ids)
            for fact_candidate in candidate.fact_candidates:
                evidence_ids.update(fact_candidate.evidence_ids)
            unknown_evidence = sorted(evidence_ids - allowed_evidence)
            if unknown_facts or unknown_evidence:
                rejected.append(
                    {
                        "index": index,
                        "candidate_id": candidate.candidate_id,
                        "error": "unknown_source_reference",
                        "unknown_fact_ids": unknown_facts,
                        "unknown_evidence_ids": unknown_evidence,
                    }
                )
                continue
            accepted.append(candidate.model_dump())
        except ValidationError as exc:
            rejected.append(
                {
                    "index": index,
                    "error": _validation_error_codes(exc),
                }
            )

    return {
        "accepted": accepted,
        "rejected": rejected,
        "batch_uncertainties": batch.uncertainties,
    }


def _validation_error_codes(exc: ValidationError) -> List[Dict[str, str]]:
    return [
        {"field": ".".join(str(part) for part in item.get("loc", [])), "type": item.get("type", "invalid")}
        for item in exc.errors(include_input=False, include_url=False)
    ]


__all__ = ["AICandidateBatch", "AICandidateFinding", "CandidateFact", "validate_ai_candidate_batch"]
