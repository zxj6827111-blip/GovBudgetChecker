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
import multiprocessing
import os
import time
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
    """在子进程内设置地址空间上限。返回是否真的生效（Windows 上恒为 False）。"""
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

        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            if page_count > max_pages:
                connection.send(
                    ("limit", f"page_limit_exceeded:{page_count}>{max_pages}")
                )
                return

            for page in pdf.pages:
                text = extract_visible_text_from_page(page)
                tables = extract_tables_from_page(page)
                text_chars += len(text)
                table_cells += _count_table_cells(tables)
                if text_chars > max_text_chars:
                    connection.send(
                        ("limit", f"text_limit_exceeded:{text_chars}>{max_text_chars}")
                    )
                    return
                if table_cells > max_table_cells:
                    connection.send(
                        ("limit", f"cell_limit_exceeded:{table_cells}>{max_table_cells}")
                    )
                    return
                page_texts.append(text)
                page_tables.append(tables)

        connection.send(
            (
                "ok",
                {
                    "page_texts": page_texts,
                    "page_tables": page_tables,
                    "filesize": file_size,
                    "page_count": len(page_texts),
                    "text_chars": text_chars,
                    "table_cells": table_cells,
                    "memory_limit_applied": memory_applied,
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
) -> Dict[str, Any]:
    effective = dict(resolve_limits())
    if limits:
        effective.update(limits)

    timeout_seconds = float(effective.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
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
        remaining = max(0.0, deadline - time.monotonic())
        if not parent_connection.poll(remaining):
            _terminate(process)
            raise PdfParseTimeout(
                f"pdf parsing exceeded {timeout_seconds:g} seconds"
            )

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
    return await asyncio.to_thread(_parse_pdf_sync, pdf_path, limits)


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
