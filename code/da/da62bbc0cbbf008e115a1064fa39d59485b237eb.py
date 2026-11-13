"""JSONB 数字正规化不能改变原始几何、OCR 证据及密封摘要。"""

from copy import deepcopy
from decimal import Decimal
import json
import math

import pytest

from src.services.lossless_json import pack_jsonb, unpack_jsonb, canonical_json
from src.services.parse_artifact_store import _restore_snapshot, artifact_payload_hash
from src.schemas.parse_artifact import ParseArtifact, FiscalFact
from src.engine.fact_context import FactContext


def test_jsonb_envelope_preserves_negative_zero_exponents_and_decimal_strings():
    source = {"geometry": [-0.0, 1e20, 1e-20], "amount": "9999999999999999.99"}
    stored = json.loads(json.dumps(pack_jsonb(source)))
    restored = unpack_jsonb(stored)
    assert canonical_json(restored) == canonical_json(source)
    assert math.copysign(1, restored["geometry"][0]) == -1
    assert restored["amount"] == source["amount"]


def test_original_parse_hash_and_fact_context_digest_survive_storage_envelope():
    artifact = ParseArtifact("a" * 64, coordinate_transforms={"source": {"a":-0.0,"b":1e-20}})
    payload = artifact.to_dict()
    run = {**payload, "artifact_snapshot": pack_jsonb(payload, query_fields=("input_versions",)),
           "artifact_snapshot_hash": artifact_payload_hash(payload)}
    assert artifact_payload_hash(_restore_snapshot(run).to_dict()) == run["artifact_snapshot_hash"]
    fact = FiscalFact("cell", "actual", "12.34", "万元", Decimal("12.34"),
                      extra_dims={"source_geometry": [-0.0, 1e20]})
    context = FactContext([fact], file_hash="a" * 64)
    assert FactContext.from_dict(pack_jsonb(context.to_dict())).version_token == context.version_token
    assert unpack_jsonb(context.to_dict()) == context.to_dict()  # 历史对象只读。


@pytest.mark.parametrize("field,value", [("sha256","0"*64), ("uncompressed_bytes",1),
                                         ("payload","invalid!"), ("encoding","unknown")])
def test_tampered_storage_is_rejected(field,value):
    packed = pack_jsonb({"original":"12.34"})
    packed[field] = value
    with pytest.raises((ValueError, TypeError)):
        unpack_jsonb(packed)


def test_query_projection_is_not_allowed_to_disagree_with_original():
    packed = pack_jsonb({"input_versions":{"input":"fiscal-input-v2"}}, query_fields=("input_versions",))
    packed = deepcopy(packed)
    packed["input_versions"]["input"] = "legacy"
    with pytest.raises(ValueError, match="projection mismatch"):
        unpack_jsonb(packed)
