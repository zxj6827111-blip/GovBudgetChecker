"""统一序列化边界的边界测试（F-01 Decimal 提交阻断）。

只覆盖这次修复真正依赖的契约，不复制实现细节：
- 真实失败形态：嵌套 Decimal（location.diff）可写入且精度不变；
- 普通结果兼容：没有 Decimal 时与旧 ``json.dumps`` 字节一致；
- 未知对象与 NaN/Infinity 按契约拒绝，而不是 default=str 掩盖；
- 内容指纹在 Decimal 存在时与旧 ``default=str`` 结果一致（历史指纹不变）。
"""

from decimal import Decimal
import json

import pytest

from src.services.analysis_result_store import compute_analysis_result_fingerprint
from src.services.json_canonical import (
    NonFiniteNumberError,
    UnsupportedJsonValueError,
    dumps_canonical,
    to_jsonable,
)


def test_nested_decimal_becomes_exact_string_without_mutating_source():
    payload = {
        "result": {
            "issues": {
                "error": [
                    {"rule_id": "V33-241", "location": {"diff": Decimal("3596.54")}},
                    {"rule_id": "V33-240", "location": {"diff": Decimal("62771.74999999999")}},
                ]
            }
        }
    }

    encoded = json.loads(dumps_canonical(payload))

    assert encoded["result"]["issues"]["error"][0]["location"]["diff"] == "3596.54"
    assert encoded["result"]["issues"]["error"][1]["location"]["diff"] == "62771.74999999999"
    # 源对象保持 Decimal，序列化不改写不可变结果
    assert isinstance(payload["result"]["issues"]["error"][0]["location"]["diff"], Decimal)


def test_decimal_arithmetic_precision_is_preserved():
    payload = {"amount": Decimal("0.1") + Decimal("0.2")}
    assert json.loads(dumps_canonical(payload))["amount"] == "0.3"


def test_plain_payload_matches_legacy_dumps_bytes():
    payload = {
        "issues": {"all": [{"page": 3, "amount": 12.5, "text": "三公经费"}]},
        "meta": {"ok": True, "nothing": None},
    }
    assert dumps_canonical(payload) == json.dumps(payload, ensure_ascii=False)


def test_sort_keys_and_indent_are_honoured():
    payload = {"b": Decimal("1.10"), "a": 1}
    encoded = dumps_canonical(payload, sort_keys=True, indent=2)
    assert encoded.index('"a"') < encoded.index('"b"')
    assert "\n" in encoded


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_float_is_rejected(value):
    with pytest.raises(NonFiniteNumberError):
        to_jsonable({"amount": value})


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_non_finite_decimal_is_rejected(value):
    with pytest.raises(NonFiniteNumberError):
        to_jsonable({"amount": value})


def test_unknown_object_is_rejected_with_path():
    with pytest.raises(UnsupportedJsonValueError) as excinfo:
        to_jsonable({"result": {"issues": [object()]}})
    assert "issues[0]" in str(excinfo.value)


def test_fingerprint_is_unchanged_for_decimal_payloads():
    """旧实现用 default=str 把 Decimal 转成 str；新边界必须给出同一字节结果。"""
    payload = {
        "result": {
            "rule_findings": [{"location": {"diff": Decimal("3596.54")}}],
            "merged": None,
            "ai_findings": [],
            "meta": {"obligation_coverage": {"done": 1}},
        }
    }
    content = {
        "ai_findings": [],
        "rule_findings": [{"location": {"diff": Decimal("3596.54")}}],
        "merged": None,
        "obligation_coverage": {"done": 1},
    }
    legacy = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
    import hashlib

    assert compute_analysis_result_fingerprint(payload) == hashlib.sha256(
        legacy.encode("utf-8")
    ).hexdigest()
