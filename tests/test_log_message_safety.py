"""Task C：日志 `message` 字段敏感信息防回归。

整改前的事实（M3 复核遗留 concern）：
`StructuredFormatter` 只对 `extras` 走 `redact_log_fields`，`record.getMessage()`
**不经过任何脱敏**直接进 JSON 的 `message` 字段。所以
`logger.warning(f"转换语义问题失败: {e}, issue={issue}")` 会把整条 finding（含
`evidence_text`，也就是 PDF 原文）落盘；`raise Exception(f"返回格式错误: {result}")`
则会让 AI 服务回传的材料原文顺着上游的 `{e}` 进 message。

断言意图（每组都有正反对照）：
1. 静态门禁 `scripts/check_log_message_safety.py`：
   正例——各种"整个对象进 message"的写法都必须被抓到；
   反例——只取安全子字段、走 `extra=`、`str(payload)` 之类错误串必须放过，
   否则门禁会因为误报被忽略。
2. 全仓扫描结果必须为 0（回归线）。
3. `describe_exception`：异常消息原文降级为长度+哈希，同时保留 `loc/type`；
   反例——直接 `str(exc)` 确实含原文（证明这段测试不是空转）。
4. 真实调用路径（AI 语义问题转换 / 规则结果转换 / 数值转换 / AI 服务错误响应）：
   渲染后的日志与异常消息里都找不到原文，但**定位字段仍在**
   （否则"什么都不记"也能通过测试）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

import pytest

from scripts.check_log_message_safety import (
    RISKY_OBJECT_NAMES,
    SAFE_LOG_NAMES,
    _SAFE_NAME_SUFFIXES,
    check_paths,
    check_source,
    classify_bare_name,
)
from src.engine.ai import extractor_client as extractor_mod
from src.schemas.issues import AnalysisConfig, IssueItem, JobContext
from src.services.ai_findings import AIFindingsService
from src.services.rule_findings import RuleFindingsService
from src.utils.logging_config import (
    StructuredFormatter,
    describe_exception,
    fingerprint_for_log,
    is_sensitive_log_key,
)
from src.utils.validation import safe_float, safe_int, validate_amount

#: 刻意用短片段：pydantic 会截断过长的 input_value，长片段反而看不出泄漏
SECRET_EVIDENCE = "合计35.20万元"
SECRET_BODY = "因公出国境费10.00万元公务接待费3.00万元"


def _render(records: List[logging.LogRecord]) -> List[str]:
    formatter = StructuredFormatter()
    return [formatter.format(record) for record in records]


def _messages(records: List[logging.LogRecord]) -> List[str]:
    return [record.getMessage() for record in records]


# ---------------------------------------------------------------------------
# 1. 静态门禁：正例必须抓到
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        # f-string 塞整个 dict
        'logger.warning(f"转换失败: {e}, issue={issue}")',
        # %-style 塞整个对象
        'logger.error("convert failed: %s", raw_issue)',
        # .format() 塞整个对象
        'logger.info("row={}".format(row))',
        # 字符串拼接
        'logger.info("value=" + str(value))',
        # self.logger 也算 logger
        'self.logger.debug(f"{payload}")',
        # logger.log(level, msg, *args)
        'logger.log(logging.INFO, "%s", content)',
        # 敏感属性 / 敏感下标
        'logger.info(f"{finding.evidence_text}")',
        'logger.info(f"{issue[\'snippet\']}")',
        # 完整异常栈进 message
        'logger.error(f"details: {traceback.format_exc()}")',
        'logger.error(traceback.format_exc())',
        # 异常消息含原文（会顺着上游的 {e} 落盘）
        'raise Exception(f"AI返回格式错误: {result}")',
        'raise ValueError(f"Cannot convert \'{value}\' to float")',
        'raise RuntimeError(f"bad body: {response.text}")',
        # 独立复核实际发现的漏报写法：hit 的必需字段含 budget_text/final_text/stmt_text。
        # 旧版门禁用名字黑名单，`hit` 不在名单里就放过了——这四条锁死回归。
        'logger.warning(f"跳过缺少必需字段的hit: {hit}")',
        'logger.warning(f"跳过span格式错误的hit: {hit}")',
        'logger.warning(f"转换hit失败: {e}, hit: {hit}")',
        'raise ValueError(f"bad hit: {hit}")',
        # fail-closed 的核心价值：**从没被想到过的名字**也必须报，
        # 这是黑名单原理上做不到的。
        'logger.warning(f"skip: {reason_span}")',
        'logger.info(f"payload dump: {mystery_object}")',
        'logger.info("dump %s", some_new_business_object)',
        'logger.info(f"{extracted_material}")',
    ],
)
def test_checker_flags_leaky_log_calls(source: str) -> None:
    violations = check_source(source, "probe.py")
    assert violations, f"未抓到泄漏写法：{source}"


# ---------------------------------------------------------------------------
# 1'. 静态门禁：反例必须放过（误报会让门禁失去可信度）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    [
        # 只取统计量或安全子字段
        'logger.info(f"rows={len(rows)}")',
        'logger.info(f"rule {issue[\'rule_id\']} failed")',
        'logger.info(f"finding {finding.rule_id} located")',
        # 走 extra=，由 redact_log_fields 兜底
        'logger.info("parsed", extra={"evidence_text": text})',
        # 异常对象本身允许（异常栈对排障必要）
        'logger.warning(f"rule {rule_id} failed: {e}")',
        # 子进程回传的错误串、表标识、错误码：不是材料原文
        'raise PdfParseError(str(payload))',
        'raise ValueError(f"Invalid table name: {table}")',
        'raise RuntimeError(f"{code}:{detail}")',
        # 非 logger 的同名方法不该被误判
        'metrics.info(f"{issue}")',
        # 经过指纹化的内容可以进 message
        'raise Exception(f"bad body: {fingerprint_for_log(response.text)}")',
        # fail-closed 的另一半：常规排障标量不能被误伤，否则门禁会被绕过。
        # 机械安全模式（*_id / *_count / *_ms / is_* / 全大写常量）覆盖这些。
        'logger.info(f"job {job_id} done in {elapsed_ms}ms")',
        'logger.info(f"parsed {table_count} tables, {cell_count} cells")',
        'logger.warning(f"retry {attempt}/{max_retries} after {delay}s")',
        'logger.info(f"queue role={role} status={status} stage={stage}")',
        'logger.info(f"ai_enabled={ai_enabled} is_ready={is_ready}")',
        'logger.warning(f"upload too large: limit={MAX_UPLOAD_MB}MB")',
        'logger.info(f"module {__name__} loaded")',
        # 修好后的真实写法：只记字段名清单 + 类型 + 指纹
        'logger.warning("跳过缺少必需字段的hit: missing=%s, %s", sorted(missing_fields), fingerprint_for_log(hit))',
        'logger.warning("span格式错误: field=%s, type=%s", span_field, type(span).__name__)',
        'logger.warning("转换hit失败: %s, %s", describe_exception(e), fingerprint_for_log(hit))',
    ],
)
def test_checker_allows_safe_log_calls(source: str) -> None:
    violations = check_source(source, "probe.py")
    assert violations == [], f"误报：{source} -> {[v.format_text() for v in violations]}"


def test_repository_has_no_log_message_leaks() -> None:
    """回归线：全仓扫描必须为 0；新增泄漏写法会让这条测试和 CI 一起变红。"""
    violations = check_paths(["api", "src", "scripts"])
    assert violations == [], "\n".join(item.format_text() for item in violations)


# ---------------------------------------------------------------------------
# 2. describe_exception / fingerprint_for_log
# ---------------------------------------------------------------------------
def _pydantic_error_with_evidence() -> Exception:
    """构造一个"异常消息里带材料原文"的真实异常（pydantic 会回显 input_value）。"""
    try:
        IssueItem(
            id="i",
            source="rule",
            severity="high",
            title="t",
            message="m",
            metrics=SECRET_EVIDENCE,
        )
    except Exception as exc:  # noqa: BLE001 - 就是要拿到这个异常对象
        return exc
    raise AssertionError("预期 IssueItem 校验失败")


def test_pydantic_exception_really_leaks_evidence() -> None:
    """反向对照：证明泄漏风险真实存在，后面的断言不是空转。"""
    exc = _pydantic_error_with_evidence()
    assert SECRET_EVIDENCE in str(exc)


def test_describe_exception_drops_original_text_but_keeps_locations() -> None:
    exc = _pydantic_error_with_evidence()
    described = describe_exception(exc)

    serialized = json.dumps(described, ensure_ascii=False)
    assert SECRET_EVIDENCE not in serialized
    # 仍然可排障：错误类型 + 字段路径 + 错误码 + 消息指纹
    assert described["error_type"] == "ValidationError"
    assert described["error_locations"][0]["loc"] == "metrics"
    assert described["error_locations"][0]["type"] == "dict_type"
    assert described["error_message_len"] == len(str(exc))
    assert len(described["error_message_sha256"]) == 12


def test_describe_exception_fingerprint_is_stable_and_discriminating() -> None:
    same_a = describe_exception(ValueError("boom"))
    same_b = describe_exception(ValueError("boom"))
    other = describe_exception(ValueError("boom!"))

    assert same_a["error_message_sha256"] == same_b["error_message_sha256"]
    assert same_a["error_message_sha256"] != other["error_message_sha256"]


def test_fingerprint_for_log_hides_content() -> None:
    marker = fingerprint_for_log(SECRET_BODY)
    assert SECRET_BODY not in marker
    assert marker.startswith(f"len={len(SECRET_BODY)},sha256=")
    # 对照：不同内容指纹不同
    assert marker != fingerprint_for_log(SECRET_BODY + "x")


# ---------------------------------------------------------------------------
# 3. 真实调用路径：AI 语义问题转换失败
# ---------------------------------------------------------------------------
def test_ai_semantic_conversion_failure_logs_no_document_text(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    service = AIFindingsService(AnalysisConfig())
    context = JobContext(job_id="job-log-c", pdf_path="", page_texts=[], meta={})

    # bbox 给 4 个非数字字符串 -> IssueItem 校验失败，pydantic 回显 input_value
    semantic_issues: List[Dict[str, Any]] = [
        {
            "rule_id": "AI-SEM-01",
            "title": "标题",
            "message": "说明",
            "quote": SECRET_EVIDENCE,
            "context": SECRET_EVIDENCE,
            "original": SECRET_EVIDENCE,
            "span": [0, 10],
            "bbox": [SECRET_EVIDENCE, SECRET_EVIDENCE, SECRET_EVIDENCE, SECRET_EVIDENCE],
        }
    ]

    issues = service._convert_semantic_issues_to_items(semantic_issues, context)

    assert issues == [], "这条输入本来就该转换失败，否则测不到失败日志"
    assert caplog.records, "转换失败必须留日志"
    for line in _render(caplog.records):
        assert SECRET_EVIDENCE not in line
    # 正向断言：定位信息仍在，不是"什么都不记"
    record = next(r for r in caplog.records if r.getMessage() == "转换语义问题失败")
    assert record.issue_index == 0
    assert record.issue_rule_id == "AI-SEM-01"
    assert record.error_type == "ValidationError"


# ---------------------------------------------------------------------------
# 4. 真实调用路径：规则结果转换失败
# ---------------------------------------------------------------------------
def test_rule_conversion_failure_logs_no_document_text(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    service = RuleFindingsService(AnalysisConfig())
    context = JobContext(job_id="job-log-c2", pdf_path="", page_texts=[], meta={})

    # metrics 传字符串 -> IssueItem 校验失败并回显该字符串
    rule_results = [
        {
            "rule_id": "C-001",
            "title": "三公经费合计不等于分项之和",
            "message": "合计与分项不一致",
            "evidence": SECRET_EVIDENCE,
            "metrics": SECRET_EVIDENCE,
        }
    ]

    issues = service._convert_to_issues(rule_results, context)

    assert issues == []
    assert caplog.records
    for line in _render(caplog.records):
        assert SECRET_EVIDENCE not in line
    record = next(r for r in caplog.records if r.getMessage() == "转换单个规则结果失败")
    assert record.issue_rule_id == "C-001"
    assert record.error_type == "ValidationError"


# ---------------------------------------------------------------------------
# 5. 真实调用路径：数值转换与金额校验
# ---------------------------------------------------------------------------
def test_safe_float_does_not_log_or_raise_cell_value(caplog) -> None:
    caplog.set_level(logging.DEBUG)

    assert safe_float(SECRET_EVIDENCE) is None
    assert safe_int(SECRET_EVIDENCE, default=-1) == -1

    rendered = _render(caplog.records)
    assert rendered, "转换失败必须留日志"
    for line in rendered:
        assert SECRET_EVIDENCE not in line
    # 正向断言：仍能看出是哪种类型、内容多长
    record = caplog.records[0]
    assert record.value_type == "str"
    assert record.value_text_len == len(SECRET_EVIDENCE)

    # 异常路径：raise_on_error 时异常消息也不能带原值
    with pytest.raises(ValueError) as excinfo:
        safe_float(SECRET_EVIDENCE, raise_on_error=True)
    assert SECRET_EVIDENCE not in str(excinfo.value)


def test_validate_amount_keeps_amount_out_of_message_and_exception(caplog) -> None:
    caplog.set_level(logging.DEBUG)

    with pytest.raises(ValueError) as excinfo:
        validate_amount(-1234.56, table_code="T-01", row_order=7)

    assert "-1234.56" not in str(excinfo.value)
    assert "T-01" in str(excinfo.value)
    for message in _messages(caplog.records):
        assert "1234.56" not in message
    record = caplog.records[-1]
    assert record.table_code == "T-01"
    assert record.row_order == 7
    assert record.reason == "negative_amount"


# ---------------------------------------------------------------------------
# 6. 真实调用路径：AI 服务错误响应不得把响应体写进异常消息
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    @property
    def text(self) -> str:
        return self._body if isinstance(self._body, str) else json.dumps(self._body)

    def json(self) -> Any:
        return self._body


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def post(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        return self._response


def _patch_http(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> None:
    monkeypatch.setattr(
        extractor_mod.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(response),
    )


@pytest.mark.asyncio
async def test_extractor_http_error_message_excludes_response_body(monkeypatch) -> None:
    _patch_http(monkeypatch, _FakeResponse(500, SECRET_BODY))
    client = extractor_mod.ExtractorClient()

    with pytest.raises(Exception) as excinfo:
        await client._single_call("section", "hash")

    message = str(excinfo.value)
    assert SECRET_BODY not in message
    # 仍可定位：状态码 + 响应体指纹
    assert "500" in message
    assert f"len={len(SECRET_BODY)}" in message


@pytest.mark.asyncio
async def test_extractor_bad_payload_message_excludes_response_body(monkeypatch) -> None:
    body = {"unexpected": SECRET_BODY}
    _patch_http(monkeypatch, _FakeResponse(200, body))
    client = extractor_mod.ExtractorClient()

    with pytest.raises(Exception) as excinfo:
        await client._single_call("section", "hash")

    message = str(excinfo.value)
    assert SECRET_BODY not in message
    assert "hits" in message
    assert "sha256=" in message


# ---------------------------------------------------------------------------
# 7. fail-closed 契约与白名单一致性
#
# 这一节针对的是"门禁自身被绕过"的风险。独立复核发现 4 处真实泄漏，根因不是漏改
# 代码，而是**门禁原理不对**：名字黑名单对没被想到的名字天然漏报。所以这里锁死
# 三件事：默认拒绝、白名单不能覆盖红线、白名单不能悄悄放宽。
# ---------------------------------------------------------------------------
def test_unlisted_name_is_rejected_by_default() -> None:
    """fail-closed 的定义：没登记过就是违规。"""
    reason = classify_bare_name("some_object_nobody_thought_of", fail_closed=True)
    assert reason is not None
    assert "fail-closed" in reason


def test_hit_is_rejected_on_both_paths() -> None:
    """`hit` 承载 budget_text/final_text/stmt_text，logger 与 raise 两侧都要拦。"""
    assert classify_bare_name("hit", fail_closed=True) is not None
    assert classify_bare_name("hit", fail_closed=False) is not None
    assert classify_bare_name("hits", fail_closed=False) is not None


def test_safe_names_never_include_sensitive_or_risky() -> None:
    """一致性不变量：白名单不得与敏感口径/危险名单重叠。

    没有这条，后人只要往 SAFE_LOG_NAMES 里加一个 `text` 或 `hit` 就能让门禁静音，
    而 CI 依旧全绿——门禁形同虚设。
    """
    overlap = SAFE_LOG_NAMES & RISKY_OBJECT_NAMES
    assert overlap == set(), f"白名单混入了危险名字: {sorted(overlap)}"
    sensitive = {name for name in SAFE_LOG_NAMES if is_sensitive_log_key(name)}
    assert sensitive == set(), f"白名单混入了敏感字段名: {sorted(sensitive)}"


def test_safe_suffixes_do_not_whitelist_credentials() -> None:
    """机械安全后缀不得开出凭据后门。

    `_keys` 就是典型陷阱：加上它，`api_keys` 会被顺带放过。这条测试把当初的判断
    钉死，防止有人"顺手补全"后缀表。
    """
    assert "_keys" not in _SAFE_NAME_SUFFIXES
    assert "_key" not in _SAFE_NAME_SUFFIXES
    for name in ("api_keys", "secret_key", "auth_keys"):
        assert classify_bare_name(name, fail_closed=True) is not None, name


def test_raise_path_stays_narrow_on_purpose() -> None:
    """raise 侧刻意仍是黑名单：实测那里几乎全是配置值与 SQL 标识符。

    这条测试的作用是让这个**取舍显式可见**——若将来要把 raise 也改成 fail-closed，
    会先在这里变红，迫使改动者重新评估噪声代价，而不是无意识地改掉。
    """
    assert classify_bare_name("table", fail_closed=False) is None
    assert classify_bare_name("payload", fail_closed=False) is None
    # 但同样的名字在 logger message 侧一律拦住
    assert classify_bare_name("table", fail_closed=True) is not None
    assert classify_bare_name("payload", fail_closed=True) is not None


# ---------------------------------------------------------------------------
# 8. 真实调用路径：AI 命中转换的 4 条失败分支都不得把送检原文写进 message
#
# 这是对 P1 的行为级验证——不是断言源码长什么样（那种测试与实现互为镜像），
# 而是真的跑一遍转换、抓 LogRecord，检查渲染后的 message 里有没有原文。
# ---------------------------------------------------------------------------
def _hit_with_text(**overrides: Any) -> Dict[str, Any]:
    """构造一条带材料原文的 AI 命中。"""
    hit: Dict[str, Any] = {
        "budget_text": f"2025年部门预算表 {SECRET_EVIDENCE}",
        "budget_span": [0, 10],
        "final_text": f"决算数 {SECRET_BODY}",
        "final_span": [0, 10],
        "stmt_text": f"三公经费说明 {SECRET_EVIDENCE}",
        "stmt_span": [0, 10],
        "clip": {"page": 12},
    }
    hit.update(overrides)
    return hit


def _convert(hits: List[Any]) -> List[Dict[str, Any]]:
    client = extractor_mod.ExtractorClient()
    return client._convert_hits_to_internal_format(hits)


def test_convert_hits_missing_field_logs_no_document_text(caplog) -> None:
    hit = _hit_with_text()
    hit.pop("clip")  # 触发"缺少必需字段"分支

    with caplog.at_level(logging.WARNING):
        assert _convert([hit]) == []

    blob = "\n".join(_messages(caplog.records) + _render(caplog.records))
    assert blob, "预期至少一条告警日志"
    assert SECRET_EVIDENCE not in blob
    assert SECRET_BODY not in blob
    # 仍可排障：缺哪个字段 + 整条 hit 的指纹
    assert "clip" in blob
    assert "sha256=" in blob


def test_convert_hits_bad_span_logs_no_document_text(caplog) -> None:
    hit = _hit_with_text(final_span="0-10")  # 不是两元素列表

    with caplog.at_level(logging.WARNING):
        _convert([hit])

    blob = "\n".join(_messages(caplog.records) + _render(caplog.records))
    assert blob, "预期至少一条告警日志"
    assert SECRET_EVIDENCE not in blob
    assert SECRET_BODY not in blob
    # 仍可排障：哪个 span 字段 + 实际类型
    assert "final_span" in blob
    assert "str" in blob


def test_convert_hits_bad_reason_span_logs_no_content(caplog) -> None:
    # reason_span 形状不对时可能携带任意内容，这里就塞原文
    hit = _hit_with_text(reason_span=f"原因：{SECRET_EVIDENCE}")

    with caplog.at_level(logging.WARNING):
        converted = _convert([hit])

    blob = "\n".join(_messages(caplog.records) + _render(caplog.records))
    assert blob, "预期至少一条告警日志"
    assert SECRET_EVIDENCE not in blob
    assert converted and converted[0]["reason_span"] is None
    assert "sha256=" in blob


def test_convert_hits_exception_path_logs_no_document_text(caplog) -> None:
    """异常分支：hit 不是 dict 时 `hit[span_field]` 抛 TypeError。"""
    required = [
        "budget_text",
        "budget_span",
        "final_text",
        "final_span",
        "stmt_text",
        "stmt_span",
        "clip",
    ]
    # list 里包含所有必需字段名，`field in hit` 成立，随后下标访问抛 TypeError
    weird_hit: List[Any] = [*required, SECRET_BODY]

    with caplog.at_level(logging.WARNING):
        assert _convert([weird_hit]) == []

    blob = "\n".join(_messages(caplog.records) + _render(caplog.records))
    assert blob, "预期至少一条告警日志"
    assert SECRET_BODY not in blob
    # 仍可排障：异常类型 + 指纹
    assert "TypeError" in blob
    assert "sha256=" in blob


def test_convert_hits_keeps_valid_hit_intact() -> None:
    """反向对照：脱敏改造不能顺手改变正常路径的行为。"""
    hit = _hit_with_text()
    converted = _convert([hit])
    assert len(converted) == 1
    assert converted[0]["budget_text"] == hit["budget_text"]
    assert converted[0]["clip"] == {"page": 12}


def test_old_style_hit_logging_really_leaks(caplog) -> None:
    """反向对照：证明上面四条 `SECRET not in blob` 不是空转。

    这里刻意复现被复核抓到的旧写法。它确实会把 `budget_text` 原文写进 message，
    连 `StructuredFormatter` 也拦不住——`record.getMessage()` 不经过
    `redact_log_fields`，这正是整条门禁存在的理由。

    注：本文件在 `tests/` 下，不在门禁扫描范围（api/src/scripts）内，所以这条
    故意写坏的示例不会让门禁自相矛盾。
    """
    hit = _hit_with_text()
    probe = logging.getLogger("probe.leak")

    with caplog.at_level(logging.WARNING, logger="probe.leak"):
        probe.warning(f"跳过缺少必需字段的hit: {hit}")  # 旧写法，仅用于对照

    blob = "\n".join(_messages(caplog.records) + _render(caplog.records))
    assert SECRET_EVIDENCE in blob, "对照失败：旧写法本应泄漏原文"
    # 连结构化渲染后也还在——脱敏只覆盖 extras，不覆盖 message
    assert any(SECRET_EVIDENCE in line for line in _render(caplog.records))
