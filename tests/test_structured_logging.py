"""Task 10 / 缺口 P2-01 + B-05：结构化日志接线。

整改前的事实：`src/utils/logging_config.py` 有完整的 StructuredFormatter /
LogContext / log_job_stage，但全仓没有任何 `setup_logging(...)` 调用点，
整套设施是死代码，线上跑的是 Python 默认日志。

断言意图（每组都有正反对照）：
1. 环境变量解析：LOG_JSON/LOG_LEVEL 显式配置生效；未配置时按 stdout 是否 TTY 决定。
2. job_id 关联：进入 `log_context` 后每条记录都带 job_id；
   **反例**：离开上下文后记录不得再带 job_id（证明字段来自上下文而非硬编码）。
3. 并发隔离：两个 asyncio 任务各自的 job_id 不互相串（LogContext 的全局
   LogRecordFactory 做不到这一点，这也是改用 contextvars 的原因）。
4. 敏感内容不入日志（硬要求）：证据原文 / PDF 正文 / API key 一律不出现在
   最终日志输出里，只保留长度与哈希；**反例**是直接断言原文子串不存在。
5. 端到端：跑一次真实流水线，断言每条 job.orchestrator 记录都带 job_id + stage，
   且 PDF 正文片段在所有日志输出中都找不到。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from api import main as pipeline_mod
from api import runtime
from src.utils.logging_config import (
    StructuredFormatter,
    configure_logging_from_env,
    current_log_context,
    is_sensitive_log_key,
    log_context,
    log_job_stage,
    redact_log_fields,
    resolve_logging_options,
    safe_log_extra,
)

SECRET_EVIDENCE = "合计35.20万元其中因公出国境费10.00万元"
SECRET_KEY = "gbk-live-0123456789abcdef"


# ---------------------------------------------------------------------------
# 1. 环境变量解析
# ---------------------------------------------------------------------------
def test_resolve_logging_options_honours_explicit_env(monkeypatch):
    monkeypatch.setenv("LOG_JSON", "true")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("LOG_FILE", "logs/app.log")

    options = resolve_logging_options()

    assert options == {
        "level": "DEBUG",
        "json_format": True,
        "log_file": "logs/app.log",
    }


def test_resolve_logging_options_explicit_false_disables_json(monkeypatch):
    """对照：显式 LOG_JSON=false 时即使在容器里也用可读格式。"""
    monkeypatch.setenv("LOG_JSON", "false")
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_FILE", raising=False)

    options = resolve_logging_options()

    assert options["json_format"] is False
    assert options["level"] == "INFO"
    assert options["log_file"] is None


def test_resolve_logging_options_defaults_to_json_when_not_tty(monkeypatch):
    """未配置 LOG_JSON 时：非 TTY（容器/管道）默认 JSON，TTY（本地终端）默认可读。"""
    monkeypatch.delenv("LOG_JSON", raising=False)

    class _Stream:
        def __init__(self, tty: bool) -> None:
            self._tty = tty

        def isatty(self) -> bool:
            return self._tty

    monkeypatch.setattr("src.utils.logging_config.sys.stdout", _Stream(False))
    assert resolve_logging_options()["json_format"] is True

    monkeypatch.setattr("src.utils.logging_config.sys.stdout", _Stream(True))
    assert resolve_logging_options()["json_format"] is False


def test_invalid_log_level_falls_back_to_info(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "not-a-level")
    assert resolve_logging_options()["level"] == "INFO"


def test_configure_logging_skipped_under_testing(monkeypatch):
    """测试环境默认跳过装配：setup_logging 会清空 root handler，会打断 pytest 采集。"""
    monkeypatch.setenv("TESTING", "true")
    assert configure_logging_from_env("api") is None


def test_configure_logging_force_applies_json_formatter(monkeypatch):
    """force=True 时真正装配；结束后恢复原 handler，避免影响其它测试。"""
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.setenv("LOG_JSON", "true")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.delenv("LOG_FILE", raising=False)

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        options = configure_logging_from_env("api", force=True)
        assert options is not None
        assert options["json_format"] is True
        assert root.handlers, "setup_logging 必须至少装配一个 handler"
        assert isinstance(root.handlers[0].formatter, StructuredFormatter)
    finally:
        root.handlers.clear()
        root.handlers.extend(saved_handlers)
        root.setLevel(saved_level)


# ---------------------------------------------------------------------------
# 2. job_id 关联（正反对照）
# ---------------------------------------------------------------------------
def test_log_context_attaches_job_id(caplog):
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("test.context")

    with log_context(job_id="job-abc", stage="解析PDF内容"):
        logger.info("inside")

    record = caplog.records[-1]
    assert record.job_id == "job-abc"
    assert record.stage == "解析PDF内容"


def test_records_outside_context_have_no_job_id(caplog):
    """反例：不在上下文里的记录不能带 job_id，否则说明字段是硬塞的。"""
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("test.context")

    with log_context(job_id="job-abc"):
        logger.info("inside")
    logger.info("outside")

    inside, outside = caplog.records[-2], caplog.records[-1]
    assert inside.job_id == "job-abc"
    assert not hasattr(outside, "job_id")
    assert current_log_context() == {}


def test_safe_log_extra_avoids_context_key_collision(caplog):
    """`logging.makeRecord` 会拒绝覆盖工厂已注入的属性并抛 KeyError。

    正例：同名同值时剔除，上下文值保留；
    反例：同名不同值时改名为 `event_<key>`，既不炸也不丢信息。
    """
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("test.context")

    with log_context(job_id="from-context"):
        extra = safe_log_extra({"job_id": "from-context", "stage": "s1"})
        assert extra == {"stage": "s1"}
        logger.info("same-value", extra=extra)

        conflicting = safe_log_extra({"job_id": "other-job"})
        assert conflicting == {"event_job_id": "other-job"}
        logger.info("different-value", extra=conflicting)

    same, different = caplog.records[-2], caplog.records[-1]
    assert same.job_id == "from-context"
    assert different.job_id == "from-context"
    assert different.event_job_id == "other-job"


def test_raw_extra_collision_would_raise(caplog):
    """反例证明上面的保护不是多余的：直接传同名 extra 确实会抛 KeyError。"""
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("test.context")

    with log_context(job_id="from-context"):
        with pytest.raises(KeyError):
            logger.info("boom", extra={"job_id": "from-extra"})


@pytest.mark.asyncio
async def test_log_context_is_isolated_between_concurrent_tasks(caplog):
    """并发隔离：两个任务交替执行，各自只看到自己的 job_id。

    用事件强制交替，避免"恰好没有交叉"导致的假通过（不依赖时序运气）。
    """
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("test.concurrent")
    first_entered = asyncio.Event()
    second_logged = asyncio.Event()

    async def first() -> None:
        with log_context(job_id="job-1"):
            first_entered.set()
            await second_logged.wait()
            logger.info("first-after-second")

    async def second() -> None:
        await first_entered.wait()
        with log_context(job_id="job-2"):
            logger.info("second-inside")
        second_logged.set()

    await asyncio.gather(first(), second())

    by_message = {record.message: record for record in caplog.records}
    assert by_message["second-inside"].job_id == "job-2"
    # 关键：job-1 的上下文在 job-2 记录之后依然是 job-1，没有被覆盖
    assert by_message["first-after-second"].job_id == "job-1"


# ---------------------------------------------------------------------------
# 3. 敏感内容不入日志（硬要求，全部为反例断言）
# ---------------------------------------------------------------------------
def test_sensitive_key_detection():
    assert is_sensitive_log_key("api_key") is True
    assert is_sensitive_log_key("EVIDENCE_TEXT") is True
    assert is_sensitive_log_key("admin_password") is True
    assert is_sensitive_log_key("page_texts") is True
    # 对照：这些是必须保留的排障字段，不能被误当成敏感字段丢掉
    assert is_sensitive_log_key("job_id") is False
    assert is_sensitive_log_key("page_coverage") is False
    assert is_sensitive_log_key("stage") is False


def test_redact_log_fields_replaces_value_with_length_and_hash():
    redacted = redact_log_fields(
        {
            "job_id": "job-1",
            "evidence_text": SECRET_EVIDENCE,
            "api_key": SECRET_KEY,
        }
    )

    assert redacted["job_id"] == "job-1"
    assert "evidence_text" not in redacted
    assert "api_key" not in redacted
    assert redacted["evidence_text_len"] == len(SECRET_EVIDENCE)
    assert len(redacted["evidence_text_sha256"]) == 12
    serialized = json.dumps(redacted, ensure_ascii=False)
    assert SECRET_EVIDENCE not in serialized
    assert SECRET_KEY not in serialized


def test_redact_log_fields_handles_nested_structures():
    redacted = redact_log_fields(
        {
            "finding": {"rule_id": "C-001", "text_snippet": SECRET_EVIDENCE},
            "findings": [{"evidence_text": SECRET_EVIDENCE}, {"rule_id": "C-002"}],
        }
    )

    serialized = json.dumps(redacted, ensure_ascii=False)
    assert SECRET_EVIDENCE not in serialized
    assert redacted["finding"]["rule_id"] == "C-001"
    assert redacted["findings"][1]["rule_id"] == "C-002"


def test_structured_formatter_redacts_sensitive_extras():
    """兜底防线：即使调用点忘了脱敏，JSON 输出里也不能出现原文。"""
    formatter = StructuredFormatter()
    record = logging.LogRecord(
        name="test.formatter",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="parsed page",
        args=(),
        exc_info=None,
    )
    record.job_id = "job-xyz"
    record.evidence_text = SECRET_EVIDENCE
    record.api_key = SECRET_KEY

    output = formatter.format(record)

    assert SECRET_EVIDENCE not in output
    assert SECRET_KEY not in output
    payload = json.loads(output)
    assert payload["job_id"] == "job-xyz"
    assert payload["evidence_text_len"] == len(SECRET_EVIDENCE)


def test_log_job_stage_emits_structured_fields_and_redacts(caplog):
    caplog.set_level(logging.INFO)

    log_job_stage(
        job_id="job-str-id",
        stage="执行规则检查",
        status="processing",
        details={"progress": 95, "evidence_text": SECRET_EVIDENCE},
    )

    record = caplog.records[-1]
    assert record.job_id == "job-str-id"
    assert record.stage == "执行规则检查"
    assert record.job_status == "processing"
    assert record.progress == 95
    assert not hasattr(record, "evidence_text")
    assert record.evidence_text_len == len(SECRET_EVIDENCE)
    assert SECRET_EVIDENCE not in StructuredFormatter().format(record)


def test_log_job_stage_drops_reserved_keys(caplog):
    """`message` 之类保留字段若原样传入 extra，logging 会直接抛 KeyError。"""
    caplog.set_level(logging.INFO)
    log_job_stage(
        job_id="job-1",
        stage="s",
        status="processing",
        details={"message": "would break logging", "progress": 1},
    )
    assert caplog.records[-1].progress == 1


# ---------------------------------------------------------------------------
# 4. 端到端：真实流水线的日志覆盖
# ---------------------------------------------------------------------------
class _FakePdf:
    def __init__(self, page_count: int) -> None:
        self.pages = [object()] * page_count

    def __enter__(self) -> "_FakePdf":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


def _prepare_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page_text: str) -> Path:
    job_dir = tmp_path / "job-logging"
    job_dir.mkdir()
    (job_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "status": "queued",
            "mode": "legacy",
            "use_local_rules": True,
            "use_ai_assist": False,
            "report_year": 2025,
            "report_kind": "budget",
        },
    )

    monkeypatch.setattr(pipeline_mod.pdfplumber, "open", lambda _path: _FakePdf(1))
    monkeypatch.setattr(
        pipeline_mod, "_extract_visible_text_from_page", lambda _page: page_text
    )
    monkeypatch.setattr(pipeline_mod, "_extract_tables_from_page", lambda _page: [])
    monkeypatch.setattr(
        pipeline_mod,
        "run_rules_in_process",
        AsyncMock(
            return_value={
                "issues": {
                    "all": [
                        {
                            "id": "C-001-1",
                            "source": "rule",
                            "severity": "error",
                            "location": {"page": 1},
                            "evidence": [{"page": 1, "text": SECRET_EVIDENCE}],
                            "text_snippet": SECRET_EVIDENCE,
                        }
                    ],
                    "error": [],
                    "warn": [],
                    "info": [],
                }
            }
        ),
    )
    monkeypatch.setattr(
        pipeline_mod, "persist_analysis_job_snapshot", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        pipeline_mod,
        "run_structured_ingest",
        AsyncMock(
            return_value={"status": "skipped", "review_item_count": 0, "review_items": []}
        ),
    )
    monkeypatch.setattr(pipeline_mod.settings, "get", lambda *_args: False)
    return job_dir


@pytest.mark.asyncio
async def test_pipeline_logs_carry_job_id_and_stage(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    page_text = "一般公共预算财政拨款支出预算表" * 10 + SECRET_EVIDENCE
    job_dir = _prepare_job(tmp_path, monkeypatch, page_text)

    await pipeline_mod._run_pipeline_inner(job_dir)

    stage_records = [
        record for record in caplog.records if record.name == "job.orchestrator"
    ]
    assert stage_records, "流水线必须产出阶段日志"
    # 全链路可按 job_id 检索：每条阶段日志都要有 job_id + stage
    assert all(getattr(record, "job_id", None) == job_dir.name for record in stage_records)
    assert all(str(getattr(record, "stage", "")) for record in stage_records)

    stages = [record.stage for record in stage_records]
    assert "解析PDF内容" in stages
    assert "执行规则检查" in stages
    assert "完成" in stages


@pytest.mark.asyncio
async def test_pipeline_logs_never_contain_document_text(tmp_path, monkeypatch, caplog):
    """反例断言：PDF 正文与证据原文不得出现在任何一条日志输出中。"""
    caplog.set_level(logging.DEBUG)
    page_text = "一般公共预算财政拨款支出预算表" * 10 + SECRET_EVIDENCE
    job_dir = _prepare_job(tmp_path, monkeypatch, page_text)

    await pipeline_mod._run_pipeline_inner(job_dir)

    formatter = StructuredFormatter()
    rendered: List[str] = [formatter.format(record) for record in caplog.records]
    assert rendered
    for line in rendered:
        assert SECRET_EVIDENCE not in line
        assert "一般公共预算财政拨款支出预算表一般公共预算" not in line

    # 对照：证据原文确实进了结果文件（说明测试数据里真的存在这段原文），
    # 只是没有进日志——否则上面的断言会因为"根本没有这段内容"而恒真。
    payload: Dict[str, Any] = runtime.read_json_file(job_dir / "status.json", default={})
    assert SECRET_EVIDENCE in json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_pipeline_error_is_logged_with_job_id(tmp_path, monkeypatch, caplog):
    """失败路径也要能按 job_id 检索，且不能泄漏原文。"""
    caplog.set_level(logging.ERROR)
    job_dir = _prepare_job(tmp_path, monkeypatch, "文本" * 200)
    monkeypatch.setattr(
        pipeline_mod,
        "run_rules_in_process",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    await pipeline_mod._run_pipeline_inner(job_dir)

    error_records = [
        record for record in caplog.records if record.levelno >= logging.ERROR
    ]
    assert error_records
    assert all(getattr(record, "job_id", None) == job_dir.name for record in error_records)
    payload = runtime.read_json_file(job_dir / "status.json", default={})
    assert payload["status"] == "error"
