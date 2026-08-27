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
    check_paths,
    check_source,
)
from src.engine.ai import extractor_client as extractor_mod
from src.schemas.issues import AnalysisConfig, IssueItem, JobContext
from src.services.ai_findings import AIFindingsService
from src.services.rule_findings import RuleFindingsService
from src.utils.logging_config import (
    StructuredFormatter,
    describe_exception,
    fingerprint_for_log,
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
