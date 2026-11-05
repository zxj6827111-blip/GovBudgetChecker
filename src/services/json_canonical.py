"""分析结果统一序列化边界。

背景（2026-11-04 真实材料诊断）：第 2 份华东政法大学 2025 决算在
``analysis_fact_snapshot.publish_snapshot`` 的 ``json.dumps(payload)`` 处
反复抛 ``TypeError: Object of type Decimal is not JSON serializable``。
真实 payload 中 Decimal 来自规则把 ``classify_amount_diff()`` 的 Decimal
差额写进 ``location["diff"]``（V33-240/V33-241），以及 ``column_shift``
里的逐列差额。

契约（只允许这一条数值边界，不在各调用点各写一套）：

- ``Decimal`` → 精确十进制字符串，与 ``str(Decimal)`` 逐字一致。这是
  唯一被允许的“非 JSON 原生数值”，因此沿用旧 ``default=str`` 对 Decimal
  的字节结果，历史内容指纹不变；
- ``float`` 必须是有限值；``NaN`` / ``±Infinity`` 在 JSON 中无表示，拒绝；
- 其它未知对象一律拒绝，不用 ``default=str`` 掩盖——把不认识的对象
  悄悄变成字符串，会让错误数据一路写进不可变 revision；
- 不把金额转成 ``float``：二进制浮点会丢精度（真实案例里已经出现
  ``46315.39999999999`` 这样的浮点尾差）。

调用方按需决定 ``sort_keys`` / ``indent``；编码统一 UTF-8 友好
（``ensure_ascii=False``）。
"""

from __future__ import annotations

from decimal import Decimal
import json
import math
from typing import Any


class UnsupportedJsonValueError(TypeError):
    """payload 中出现 JSON 无法表示、且契约不允许转换的对象。"""


class NonFiniteNumberError(ValueError):
    """payload 中出现 NaN / Infinity。"""


def to_jsonable(value: Any, *, path: str = "$") -> Any:
    """把 payload 转成 JSON 原生结构；未知对象按路径报错，不静默降级。

    返回新结构，不修改调用方传入的对象（不可变结果不能被序列化改写）。
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        # 精确十进制文本；不经过 float。非有限 Decimal 在 JSON 中无表示。
        if not value.is_finite():
            raise NonFiniteNumberError(f"{path}: 非有限十进制数 {value!r} 不能写入分析结果")
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NonFiniteNumberError(f"{path}: 非有限浮点数 {value!r} 不能写入分析结果")
        return value
    if isinstance(value, dict):
        return {
            key: to_jsonable(item, path=f"{path}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            to_jsonable(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise UnsupportedJsonValueError(
        f"{path}: 不支持写入分析结果的对象类型 {type(value).__name__}"
        "（只允许 None/bool/int/str/Decimal/有限 float/dict/list/tuple）"
    )


def dumps_canonical(
    payload: Any,
    *,
    sort_keys: bool = False,
    indent: int | None = None,
) -> str:
    """按统一边界序列化分析结果。

    与 ``json.dumps(payload, ensure_ascii=False, default=str)`` 的差别只在
    “拒绝未知对象与非有限浮点”；对没有 Decimal 的普通 payload，输出字节
    完全一致。
    """
    return json.dumps(
        to_jsonable(payload),
        ensure_ascii=False,
        sort_keys=sort_keys,
        indent=indent,
        allow_nan=False,
    )
