"""在可终止的独立进程中解析 PDF，并施加资源上限（缺口 P2-05 / B-09）。

整改前的问题
------------
`api/main.py` 的 `_sync_parse_pdf` 经 `loop.run_in_executor(None, ...)` 跑在
**API/worker 主进程的线程池**里，只受整体 `PIPELINE_TIMEOUT_SEC` 约束：

- 线程无法被强制终止，`asyncio.wait_for` 超时只是放弃等待，解析线程仍在烧 CPU；
- 没有内存上限，一份恶意构造或超大的 PDF 可以把整个 worker 进程打爆，
  连带拖死同一进程里其它任务。

本模块的做法（写法对齐 `src/services/rule_process.py` 的既有先例）
--------------------------------------------------------------
- 用 `multiprocessing` 起独立进程，通过 `Pipe` 回传结果；
- 超时后 `terminate()` → `kill()`，进程被真正杀掉，资源立刻释放；
- start method 沿用 `rule_process` 的判断方式：Windows 用 spawn、POSIX 用 fork，
  并支持 `PDF_PARSE_PROCESS_START_METHOD` 覆盖。

资源上限与平台差异（如实说明，不粉饰）
------------------------------------
- **超时**：跨平台有效。
- **页数上限**：跨平台有效，解析前先读页数，超限直接拒绝，不去读内容。
- **内存上限**：用 `resource.setrlimit(RLIMIT_AS)`，**只有 POSIX 有效**。
  Windows 没有 rlimit，等价能力需要 Job Object，本轮未实现。
  生产部署在 Linux 容器里，除本限制外还有 cgroup 内存上限兜底；
  Windows 本机开发只有"超时 + 页数上限 + 输出体积上限"三道。
- **输出体积上限**：跨平台有效。抽出的字符数/单元格数超限即中止，
  防止"内存没爆但结果大到把 Pipe 和 status.json 拖垮"。
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)
import multiprocessing
import os
import time
import threading
import sys
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class PdfParseTimeout(TimeoutError):
    """解析超过挂钟时限。"""


class PdfParseError(RuntimeError):
    """隔离进程内解析失败（含进程异常退出）。"""


class PdfParseLimitExceeded(RuntimeError):
    """触发资源上限（页数 / 内存 / 输出体积）。"""


#: 默认值集中在这里，便于文档与测试引用
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_PAGES = 800
DEFAULT_MEMORY_MB = 1024
DEFAULT_MAX_TEXT_CHARS = 20_000_000
DEFAULT_MAX_TABLE_CELLS = 4_000_000


def _env_int(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_float(name: str, default: float) -> float:
    try:
        value = float(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _testing_mode() -> bool:
    return os.getenv("TESTING", "").strip().lower() in {"1", "true", "yes"}


def isolation_enabled() -> bool:
    """解析隔离是否启用。

    生产默认**开启**；`TESTING=true` 下默认关闭，与仓库既有约定一致
    （既有流水线测试用 monkeypatch 替换进程内的抽取函数，
    子进程里的 monkeypatch 不生效）。针对隔离本身的测试会显式打开。
    """
    return _env_flag("PDF_PARSE_ISOLATION_ENABLED", not _testing_mode())


def resolve_limits() -> Dict[str, Any]:
    """解析当前生效的资源上限（纯函数，便于测试与排障输出）。"""
    return {
        "timeout_seconds": _env_float("PDF_PARSE_TIMEOUT_SEC", DEFAULT_TIMEOUT_SECONDS),
        # 页数上限默认跟随上传门槛，避免"上传能过、解析被拒"这种自相矛盾
        "max_pages": _env_int(
            "PDF_PARSE_MAX_PAGES", _env_int("MAX_UPLOAD_PAGES", DEFAULT_MAX_PAGES)
        ),
        "memory_mb": _env_int("PDF_PARSE_MEMORY_MB", DEFAULT_MEMORY_MB),
        "max_text_chars": _env_int("PDF_PARSE_MAX_TEXT_CHARS", DEFAULT_MAX_TEXT_CHARS),
        "max_table_cells": _env_int("PDF_PARSE_MAX_TABLE_CELLS", DEFAULT_MAX_TABLE_CELLS),
        "memory_limit_supported": os.name != "nt",
        "start_method": _resolve_start_method(),
    }


def _resolve_start_method() -> str:
    start_method = os.getenv("PDF_PARSE_PROCESS_START_METHOD", "").strip()
    if start_method:
        return start_method
    return "spawn" if os.name == "nt" else "fork"


def _apply_memory_limit(memory_mb: int) -> bool:
    """在子进程内设置地址空间上限。返回是否设置成功（Windows 上恒为 False）。

    AC-16：setrlimit 成功 ≠ 内核真的强制执行——macOS/Darwin 接受
    RLIMIT_AS 设置但 malloc 不受其约束（Linux 容器内则真实生效）。
    设置成功与否与"是否可强制执行"分别由
    :func:`probe_memory_limit_enforceable` 探测并如实上报。
    """
    if os.name == "nt" or memory_mb <= 0:
        return False
    try:
        import resource
    except ImportError:  # pragma: no cover - POSIX 上一定存在
        return False
    limit_bytes = memory_mb * 1024 * 1024
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        # 不抬高既有硬上限（容器里可能已经设得更严），只在其之下收紧
        if hard != resource.RLIM_INFINITY:
            limit_bytes = min(limit_bytes, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, hard))
        return True
    except (ValueError, OSError):
        return False


def probe_memory_limit_enforceable(
    limit_mb: int = 128, cancel_event: Optional[threading.Event] = None
) -> bool:
    """探测当前平台/环境下 RLIMIT_AS 是否真的强制执行（AC-16）。

    fork 一个探针子进程：设置与解析子进程相同的上限后尝试超限分配——
    抛 MemoryError 说明内核强制执行（Linux 容器路径）；分配成功说明
    该平台不强制（macOS/Darwin）。探测失败按"不可强制"处理（fail-open
    到部署层资源上限，如 Docker memory limit）。
    """
    if os.name == "nt":
        return False
    import multiprocessing as _mp

    ctx = _mp.get_context("fork" if os.name != "nt" else "spawn")

    def _probe_child(connection: Any, mb: int) -> None:
        try:
            if not _apply_memory_limit(mb):
                connection.send(False)
                return
            try:
                blob = bytearray((mb + 64) * 1024 * 1024)
                blob[0] = 1
            except MemoryError:
                connection.send(True)
                return
            connection.send(False)
        except Exception:
            connection.send(False)
        finally:
            connection.close()

    parent_conn, child_conn = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_probe_child, args=(child_conn, int(limit_mb)), daemon=True)
    process.start()
    child_conn.close()
    try:
        deadline = time.monotonic() + 2.0
        result = False
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                break
            if parent_conn.poll(0.05):
                result = parent_conn.recv()
                break
    except (EOFError, OSError):
        result = False
    finally:
        parent_conn.close()
        process.join(timeout=0.2)
        if process.is_alive():
            process.terminate()
            process.join(timeout=0.5)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(timeout=0.5)
    return bool(result)


def _count_table_cells(page_tables: Any) -> int:
    if not isinstance(page_tables, (list, tuple)):
        return 0
    total = 0
    for table in page_tables:
        if not isinstance(table, (list, tuple)):
            continue
        for row in table:
            if isinstance(row, (list, tuple)):
                total += len(row)
    return total


def _pdf_parse_worker(
    connection: Connection,
    pdf_path: str,
    limits: Dict[str, Any],
) -> None:
    """子进程入口：解析 PDF 并把结果或错误码回传父进程。

    只回传结构化结果，不打印任何 PDF 正文（与 Task 10 的日志红线一致）。
    """
    try:
        # 测试钩子：确定性地卡住子进程，便于在任意平台验证超时终止。
        # 生产环境不设置该变量，等价于 no-op。
        delay = os.getenv("PDF_PARSE_TEST_DELAY_SECONDS", "").strip()
        if delay:
            try:
                time.sleep(float(delay))
            except ValueError:
                pass

        memory_applied = _apply_memory_limit(int(limits.get("memory_mb") or 0))
        # The capability probe must finish in the non-daemon parent. A daemon
        # parser cannot spawn its own process; nesting made Linux parsing fail.
        memory_enforced = memory_applied and bool(limits.get("memory_limit_enforceable", False))

        # 测试钩子：确定性地申请超过上限的内存，验证内存超限被拦。
        burn_mb = os.getenv("PDF_PARSE_TEST_ALLOCATE_MB", "").strip()
        if burn_mb:
            try:
                blob = bytearray(int(burn_mb) * 1024 * 1024)
                # 避免被优化掉
                blob[0] = 1
            except (MemoryError, ValueError, OverflowError) as exc:
                connection.send(("limit", f"memory_exceeded:{type(exc).__name__}"))
                return

        import pdfplumber

        from src.services.pdf_page_extract import (
            extract_tables_from_page,
            extract_visible_text_from_page,
        )

        max_pages = int(limits.get("max_pages") or DEFAULT_MAX_PAGES)
        max_text_chars = int(limits.get("max_text_chars") or DEFAULT_MAX_TEXT_CHARS)
        max_table_cells = int(limits.get("max_table_cells") or DEFAULT_MAX_TABLE_CELLS)

        file_size = Path(pdf_path).stat().st_size
        page_texts: List[str] = []
        page_tables: List[Any] = []
        text_chars = 0
        table_cells = 0

        # ZC-01（SYS-01）：同一次 pdfplumber.open 同时产出规则用的
        # page_texts/page_tables 与统一解析对象（单元格级），后续结构化入库
        # 只消费该对象，不再第二次打开 PDF。
        import hashlib

        from src.schemas.parse_artifact import ParseArtifact
        from src.services.pdf_parser import PARSER_FINGERPRINT, PDFParser

        cell_parser = PDFParser(None)
        digest = hashlib.sha256()
        with Path(pdf_path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        file_hash = digest.hexdigest()

        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            if page_count > max_pages:
                connection.send(("limit", f"page_limit_exceeded:{page_count}>{max_pages}"))
                return

            for page in pdf.pages:
                text = extract_visible_text_from_page(page)
                tables = extract_tables_from_page(page)
                text_chars += len(text)
                table_cells += _count_table_cells(tables)
                if text_chars > max_text_chars:
                    connection.send(("limit", f"text_limit_exceeded:{text_chars}>{max_text_chars}"))
                    return
                if table_cells > max_table_cells:
                    connection.send(
                        ("limit", f"cell_limit_exceeded:{table_cells}>{max_table_cells}")
                    )
                    return
                page_texts.append(text)
                page_tables.append(tables)

            try:
                parse_artifact = cell_parser.build_parse_artifact_from_handle(
                    pdf,
                    file_hash=file_hash,
                )
            except BaseException:
                # 产物构建失败不否定文本/表格抽取本身；结构化入库回退独立解析
                logger.warning(
                    "in-process parse artifact build failed; ingest will parse independently"
                )
                parse_artifact = None

        connection.send(
            (
                "ok",
                {
                    "page_texts": page_texts,
                    "page_tables": page_tables,
                    "parse_artifact": parse_artifact.to_dict() if parse_artifact else None,
                    "parse_artifact_fingerprint": PARSER_FINGERPRINT,
                    "filesize": file_size,
                    "page_count": len(page_texts),
                    "text_chars": text_chars,
                    "table_cells": table_cells,
                    "memory_limit_applied": memory_applied,
                    "memory_limit_enforced": memory_enforced,
                },
            )
        )
    except MemoryError:
        # RLIMIT_AS 生效时，超限分配会以 MemoryError 形式出现在这里
        try:
            connection.send(("limit", "memory_exceeded:MemoryError"))
        except Exception:
            pass
    except BaseException as exc:
        try:
            connection.send(("error", f"{type(exc).__name__}: {exc}"))
        except Exception:
            pass
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _parse_pdf_sync(
    pdf_path: Path,
    limits: Optional[Dict[str, Any]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    effective = dict(resolve_limits())
    if limits:
        effective.update(limits)

    timeout_seconds = float(effective.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    effective["memory_limit_enforceable"] = (
        probe_memory_limit_enforceable(int(effective.get("memory_mb") or 0), cancel_event)
        if sys.platform.startswith("linux")
        else False
    )
    if cancel_event is not None and cancel_event.is_set():
        raise PdfParseError("pdf_parse_cancelled")
    context = multiprocessing.get_context(str(effective.get("start_method") or "spawn"))
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_pdf_parse_worker,
        args=(child_connection, str(pdf_path), effective),
        name="govbudget-pdf-parser",
        daemon=True,
    )
    process.start()
    child_connection.close()
    deadline = time.monotonic() + max(0.01, timeout_seconds)

    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise PdfParseError("pdf_parse_cancelled")
            remaining = max(0.0, deadline - time.monotonic())
            if parent_connection.poll(min(0.05, remaining)):
                break
            if remaining <= 0:
                raise PdfParseTimeout(f"pdf parsing exceeded {timeout_seconds:g} seconds")

        status, payload = parent_connection.recv()
        process.join(timeout=5)
        if status == "limit":
            raise PdfParseLimitExceeded(str(payload))
        if status != "ok":
            raise PdfParseError(str(payload))
        if not isinstance(payload, dict):
            raise PdfParseError("pdf parser returned a non-object payload")
        return payload
    except EOFError as exc:
        # 子进程被 OOM killer 干掉时就是这个路径：管道断开、没有任何结果。
        # 必须报错而不是当成"解析出 0 页"，否则就是新的静默失败。
        raise PdfParseError(
            f"pdf parser exited without a result (exitcode={process.exitcode})"
        ) from exc
    finally:
        try:
            parent_connection.close()
        except Exception:
            pass
        if process.is_alive():
            _terminate(process)


def _terminate(process: Any) -> None:
    process.terminate()
    process.join(timeout=5)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=2)


async def parse_pdf_in_process(
    pdf_path: Path,
    limits: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """在独立进程中解析 PDF，返回 page_texts / page_tables / filesize 等。"""
    cancel_event = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(_parse_pdf_sync, pdf_path, limits, cancel_event))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Cancelling a to_thread future cannot stop its thread or child process.
        # Signal the owner and wait until its finally has terminated/reaped the
        # child before the job publishes a cancellation/timeout terminal state.
        cancel_event.set()
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise


def parse_error_code(exc: BaseException) -> Tuple[str, str]:
    """把解析异常映射成 (终态错误码, 说明)。

    终态一律是 `error`（`analysis_conclusion=analysis_error`），理由：
    这三类失败下**一页都没解析出来**，没有任何可供人工复核的结论。
    M1 的 `review_required` 语义是"分析跑完了但结论不完整"，
    把"完全没解析"标成待复核，等于制造一种新的虚假成功。
    """
    if isinstance(exc, PdfParseTimeout):
        return "pdf_parse_timeout", str(exc)
    if isinstance(exc, PdfParseLimitExceeded):
        return "pdf_parse_limit_exceeded", str(exc)
    return "pdf_parse_failed", str(exc)
