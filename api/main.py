# api/main.py
import os

from dotenv import load_dotenv

load_dotenv()

import json
import time
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, List, Optional

import pdfplumber
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import sys as _sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from src.engine.pipeline import build_document, build_issues_payload
from src.engine.rule_outcome import STATUS_INSUFFICIENT_DATA
from src.services.evidence_guard import (
    apply_evidence_completeness,
    count_formal_findings,
)
from src.services.pipeline_stages import resolve_stage_progress, stage_progress_to_dict
from src.utils.provenance import summarize_finding_versions
from src.utils.logging_config import (
    configure_logging_from_env,
    log_context,
    log_job_stage,
    safe_log_extra,
)
from src.schemas.issues import (
    AnalysisConclusion,
    AnalysisQualityStatus,
    JobStatus,
)
from api import runtime
from api.job_queue import DurableJobQueue
from api.queue_runtime import (
    compute_queue_workers,
    get_queue_role,
    queue_resume_on_start,
    should_start_local_queue,
)
from api.routes import register_routes

from src.services.analyze_dual import DualModeAnalyzer
from src.services.analysis_result_store import (
    persist_analysis_job_snapshot,
    sync_pending_analysis_snapshots,
)
from src.db.connection import DatabaseConnection
from src.services.issue_workflow_store import sync_workflow_recovery_state
from src.services.structured_ingest_runner import (
    close_structured_ingest_resources,
    run_structured_ingest,
)
from src.services.ai_execution import (
    build_ai_execution,
    quality_gate_ai_reasons,
)
from config.settings import get_settings
from src.services.rule_process import (
    RuleExecutionError,
    RuleExecutionTimeout,
    run_rules_in_process,
)
from src.services.pdf_page_extract import (
    extract_tables_from_page,
    extract_visible_text_from_page,
    is_visible_char,
)
from src.services.pdf_parse_process import (
    PdfParseError,
    PdfParseLimitExceeded,
    PdfParseTimeout,
    isolation_enabled as pdf_parse_isolation_enabled,
    parse_error_code,
    parse_pdf_in_process,
    resolve_limits as resolve_pdf_parse_limits,
)

try:
    from src.security import SecurityHeadersMiddleware, SecurityMiddleware
except ImportError:
    SecurityMiddleware = None
    SecurityHeadersMiddleware = None

import logging

logger = logging.getLogger(__name__)
_workflow_mirror_task: asyncio.Task | None = None


async def _sync_persistence_state_on_startup() -> None:
    try:
        await asyncio.wait_for(sync_pending_analysis_snapshots(), timeout=20)
        await asyncio.wait_for(sync_workflow_recovery_state(), timeout=5)
    except asyncio.TimeoutError:
        logger.warning("Persistence recovery retry timed out during startup")
    except Exception:
        logger.exception("Persistence recovery retry failed during startup")

async def _startup_job_queue() -> None:
    global _workflow_mirror_task
    _workflow_mirror_task = asyncio.create_task(_sync_persistence_state_on_startup())
    if not should_start_local_queue():
        logger.info(
            "Local job queue startup skipped (enabled=%s, role=%s)",
            os.getenv("JOB_QUEUE_ENABLED"),
            get_queue_role(),
        )
        return

    runner = runtime.get_pipeline_runner()
    if runner is None:
        logger.error("Pipeline runner is not configured, skip queue startup")
        return

    max_workers, ai_sequential_mode = compute_queue_workers()
    if ai_sequential_mode and max_workers < 10:
        logger.warning(
            "AI_SEQUENTIAL_MODE is enabled but JOB_QUEUE_WORKERS=%d; "
            "batch local-stage throughput may be limited (recommend >=10).",
            max_workers,
        )

    queue = DurableJobQueue(
        runner,
        max_workers=max_workers,
        resume_on_start=queue_resume_on_start(),
    )
    await queue.start()
    runtime.set_job_queue(queue)


async def _shutdown_job_queue() -> None:
    global _workflow_mirror_task
    if _workflow_mirror_task is not None:
        _workflow_mirror_task.cancel()
        try:
            await _workflow_mirror_task
        except asyncio.CancelledError:
            pass
        _workflow_mirror_task = None
    queue = runtime.get_job_queue()
    if queue is not None:
        await queue.stop()
        runtime.set_job_queue(None)
    await close_structured_ingest_resources()
    # ``close_structured_ingest_resources`` normally owns this pool, but keep
    # the lifecycle explicit for queue-disabled/TestClient startups too.
    if DatabaseConnection.is_initialized():
        await DatabaseConnection.close()


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    # 结构化日志接线点（缺口 P2-01 / B-05）：此前 setup_logging 全仓无调用点，
    # 线上实际用的是 Python 默认日志。放在 lifespan 而不是模块导入处，
    # 是因为导入期装配会清掉 pytest 的日志 handler。
    configure_logging_from_env("api")
    await _startup_job_queue()
    try:
        yield
    finally:
        await _shutdown_job_queue()


# ----------------------------- 鍩虹閰嶇疆 -----------------------------
app = FastAPI(title=runtime.APP_TITLE, lifespan=_app_lifespan)

# 新增：双模式配置
settings = get_settings()
dual_analyzer = DualModeAnalyzer()

# ----------------------------- CORS -----------------------------
# 本地 & Codespaces
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
codespace = os.getenv("CODESPACE_NAME")
gh_dom = os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
if codespace and gh_dom:
    origins += [
        f"https://{codespace}-3000.{gh_dom}",
        f"https://{codespace}-8000.{gh_dom}",
    ]

extra = os.getenv("ALLOW_ORIGINS", "").strip()
if extra:
    origins += [o.strip() for o in extra.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"https://.*\.app\.github\.dev",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

if SecurityMiddleware and runtime.security_config and runtime.security_config.enabled:
    app.add_middleware(SecurityMiddleware, config=runtime.security_config)
    logger.info("Security middleware enabled with rate limiting")

# 安全响应头（缺口 B-08）。放在最后 add，Starlette 会把它插到最外层，
# 因此连免认证端点（/health、/ready、/docs）与被鉴权中间件短路的响应也带上安全头。
# 与鉴权/限流互不影响：本中间件只加响应头，不拦请求。
if SecurityHeadersMiddleware is not None:
    app.add_middleware(SecurityHeadersMiddleware)
    logger.info("Security headers middleware enabled")


# ----------------------------- 工具函数 -----------------------------
def _safe_write(job_dir: Path, payload: Dict[str, Any]) -> None:
    """将状态写入 status.json（带异常保护），并同步产出结构化阶段日志。

    状态流转本来就集中收敛在这里，所以埋点也放在这里：一处接线覆盖全部阶段，
    不必在十几个调用点各写一遍 logger 调用，也不会漏埋。

    Task 3 新增职责（per-job 阶段进度 + 失败阶段归因）：
    - 每次写入都会用 `resolve_stage_progress()` 把本次 `payload["stage"]`（若有）
      解析成规范阶段 + 阶段内完成度，写入 `stage_progress` 字段，供
      `GET /api/jobs/{job_id}/status` 直接暴露给前端；
    - 失败写入（`payload["status"] == "error"` 且这次调用本身没有携带新的 `stage`，
      即"由 pipeline 主体的 except 分支直接落错误态"这种典型失败路径）时，
      从写入前的既有 status.json 里取出**上一次成功记录的阶段**，写入
      `stage_failed_at` 字段——这就是"失败发生在哪个阶段"的归因依据。
      之所以从"写入前的既有状态"里取而不是要求调用方显式传参，是因为
      `_run_pipeline_body` 的 `except Exception` 分支本身并不知道自己是在哪个
      阶段抛的异常（异常会打断当时的调用栈），但 status.json 在异常发生前
      的最后一次正常写入已经忠实记录了那一刻的阶段，这是本系统里
      "最后确认发生的事" 天然就有的证据，不需要额外埋点去追踪当前阶段变量。
    - 严禁伪造：`resolve_stage_progress()` 对空/未知 stage 文本返回
      `percent=None`，本函数原样透传，绝不在这里补一个猜测值。
    """
    status_file = job_dir / "status.json"
    existing: Dict[str, Any] = {}
    try:
        merged_payload = dict(payload)
        existing = runtime.read_json_file(status_file, default={})
        for key, value in runtime.extract_job_status_context(existing).items():
            merged_payload.setdefault(key, value)

        job_status = str(payload.get("status") or "unknown")
        stage_text = payload.get("stage")
        if stage_text is not None:
            # 本次写入自带阶段名：正常推进路径，直接解析当前阶段进度。
            merged_payload["stage_progress"] = stage_progress_to_dict(
                resolve_stage_progress(str(stage_text))
            )
        else:
            # 本次写入没带阶段名（典型场景：pipeline 主体 except 分支落错误态时
            # 只传了 status/error，没有重新声明 stage）。这种情况下继续沿用
            # 已经写入过的 stage_progress，不能因为这次调用没传就把它清空成
            # "未知"——上一阶段确实推进到了那里，这是真实发生过的事实。
            existing_stage_progress = existing.get("stage_progress")
            if isinstance(existing_stage_progress, dict):
                merged_payload.setdefault("stage_progress", existing_stage_progress)

        if job_status == "error" and stage_text is None:
            # 失败阶段归因：取"写入前"记录的最后一个阶段，即失败发生前的最后已知阶段。
            # 只在"这次调用没有显式声明新阶段"时才这样做——如果调用方确实带了
            # stage（例如某个阶段内部显式捕获异常后仍想标记当前阶段），
            # 不应该被这里的兜底逻辑覆盖。
            last_known_stage = existing.get("stage")
            if last_known_stage:
                merged_payload["stage_failed_at"] = stage_progress_to_dict(
                    resolve_stage_progress(str(last_known_stage))
                )

        runtime.write_json_file(status_file, merged_payload)
    except Exception as e:
        (job_dir / "status_error.log").write_text(str(e), encoding="utf-8")

    # 只写结构化元数据（进度、页覆盖率、错误码），不写 PDF 正文与证据原文
    job_status = str(payload.get("status") or "unknown")
    log_job_stage(
        job_id=str(payload.get("job_id") or job_dir.name),
        stage=str(payload.get("stage") or job_status),
        status=job_status,
        details={
            "progress": payload.get("progress"),
            "page_coverage": payload.get("page_coverage"),
            "scanned_page_count": payload.get("scanned_page_count"),
            "quality_status": payload.get("quality_status"),
            "analysis_conclusion": payload.get("analysis_conclusion"),
            "error": payload.get("error"),
        },
        level=logging.ERROR if job_status == "error" else logging.INFO,
    )


def _find_first_pdf(job_dir: Path) -> Path:
    pdfs = sorted(job_dir.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError("未在该 job 目录下找到 PDF 文件")
    return pdfs[0]


def _extract_tables_from_page(page) -> List[List[List[str]]]:
    """读取单页表格。实现下沉到 `src/services/pdf_page_extract`，
    这样解析隔离子进程可以直接导入它，而不必导入整个 FastAPI 应用。"""
    return extract_tables_from_page(page)


def _is_visible_char(obj: Dict[str, Any], page_height: float) -> bool:
    return is_visible_char(obj, page_height)


def _extract_visible_text_from_page(page) -> str:
    return extract_visible_text_from_page(page)


def _scanned_page_min_chars() -> int:
    """低文本页判定阈值（每页去空白后的字符数）。

    预决算材料的正文页通常有数百字符，纯扫描页 pdfplumber 抽出来是空串。
    阈值取 50 是为了同时挡住"只抽到页眉页脚/水印"这类残缺文本层。
    走环境变量以便不同来源的材料现场调参。
    """
    raw = os.getenv("SCANNED_PAGE_MIN_CHARS", "50")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return 50
    return value if value > 0 else 50


def _count_non_empty_table_cells(page_table: Any) -> int:
    """统计单页抽到的非空表格单元格数量（结构容错，任何异常形状都按 0 计）。"""
    if not isinstance(page_table, (list, tuple)):
        return 0
    total = 0
    for table in page_table:
        if not isinstance(table, (list, tuple)):
            continue
        for row in table:
            if not isinstance(row, (list, tuple)):
                continue
            total += sum(1 for cell in row if str(cell if cell is not None else "").strip())
    return total


def _assess_page_extraction(
    page_texts: Any,
    page_tables: Any = None,
) -> Dict[str, Any]:
    """评估每页文本抽取质量，识别疑似扫描页与低文本页。

    本轮不做自动 OCR：这里只负责"检测 + 算覆盖率"，
    是否转人工复核由任务级质量门禁（Task 3）决定。

    判定口径：
      - 去空白字符数低于阈值、且未抽到任何非空表格单元格 -> 低文本页；
      - 其中字符数为 0 且无表格单元格的，再计一笔"疑似扫描页"。

    表格单元格参与判定属于误报控制：纯表格页正文字符本来就少，
    但只要能抽到单元格就说明 PDF 有文本层，不该误判成扫描件。
    """
    texts: List[str] = []
    if isinstance(page_texts, (list, tuple)):
        texts = [str(item if item is not None else "") for item in page_texts]

    tables: List[Any] = []
    if isinstance(page_tables, (list, tuple)):
        tables = list(page_tables)

    min_chars = _scanned_page_min_chars()
    low_text_pages: List[int] = []
    scanned_pages: List[int] = []
    total_text_chars = 0

    for index, text in enumerate(texts):
        char_count = len("".join(str(text).split()))
        total_text_chars += char_count
        cell_count = _count_non_empty_table_cells(tables[index] if index < len(tables) else None)
        page_number = index + 1  # 对外一律用 1-based 页码，便于直接给人工定位
        if char_count >= min_chars or cell_count > 0:
            continue
        low_text_pages.append(page_number)
        if char_count == 0 and cell_count == 0:
            scanned_pages.append(page_number)

    page_count = len(texts)
    if page_count > 0:
        page_coverage = round((page_count - len(low_text_pages)) / page_count, 4)
    else:
        # 0 页（解析失败或空 PDF）一律按覆盖率 0 处理，让门禁把它拦成待复核，
        # 而不是因为"没有低文本页"反而算成覆盖完整。
        page_coverage = 0.0

    return {
        "page_count": page_count,
        "text_page_count": page_count - len(low_text_pages),
        "low_text_pages": low_text_pages,
        "low_text_page_count": len(low_text_pages),
        "scanned_pages": scanned_pages,
        "scanned_page_count": len(scanned_pages),
        "page_coverage": page_coverage,
        "total_text_chars": total_text_chars,
        "min_chars_threshold": min_chars,
        "ocr_applied": False,
        "detector_version": "page-extraction-v1",
    }


def _page_coverage_min_ratio() -> float:
    """质量门禁要求的最低页面文本覆盖率，低于该比例判定分析不完整。"""
    raw = os.getenv("PAGE_COVERAGE_MIN_RATIO", "0.8")
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return 0.8
    if not 0.0 < value <= 1.0:
        return 0.8
    return value


def _ai_assist_required() -> bool:
    """AI 辅助是否为"必需能力"。必需时 AI 失败不能算降级完成，必须转人工复核。"""
    return str(os.getenv("AI_ASSIST_REQUIRED", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _count_result_findings(result: Dict[str, Any]) -> int:
    """统计结果里的"正式问题"条数，兼容传统分桶结构与双模式结构。

    缺证据被降级为待复核的条目不计入（P0-07）：它们不是可交付的正式结论，
    而质量门禁正是用这个计数区分 findings_detected 与 no_findings，
    口径必须与"正式问题"一致，否则会出现"问题全被降级却仍报发现问题"。

    具体实现下沉到 `evidence_guard.count_formal_findings`，与离线回放脚本共用同一口径。
    """
    return count_formal_findings(result)


def _evaluate_quality_gate(
    page_assessment: Dict[str, Any],
    report_kind: Any,
    report_year: Any,
    ai_requested: bool,
    ai_degraded: bool,
    issue_total: int,
    evidence_degraded_count: int = 0,
    *,
    ai_execution: Optional[Dict[str, Any]] = None,
    structured_ingest: Optional[Dict[str, Any]] = None,
    evidence_completeness: Optional[Dict[str, Any]] = None,
    rule_execution_summary: Optional[Dict[str, Any]] = None,
    doc_type: Optional[str] = None,
) -> Dict[str, Any]:
    """任务级质量门禁：决定终态是 done / degraded / review_required。

    设计意图是把"分析跑完了"和"结论可信"分开：只有门禁全过才允许出 done，
    否则一律转 review_required + incomplete，避免"扫描件静默漏检却显示审核通过"。

    触发人工复核的条件（任一命中即转）：
      1. 页面文本覆盖率低于阈值；
      2. 存在疑似扫描页（本轮不做 OCR，必然漏检）；
      3. 报告类型无法识别（unknown 只跑了通用规则，专项规则未覆盖）；
      4. 年度无法识别（同比/口径类判断失去基准）；
      5. AI 被配置为必需能力却失败（legacy 参数路径）；
      6. 存在因缺证据被降级的问题项（P0-07）。

    P0 假完成修复新增（docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md §3.6）：
      7. ``ai_not_run`` / ``ai_failed``：请求 AI 后只有 succeeded 可以通过
         质量门（not_run、超时、空响应、token 截断、全部 provider 失败均转）；
      8. ``missing_required_table``：结构化入库发现核心表缺失；
      9. ``ambiguous_table_schema``：表结构歧义（未知表/低置信度表）；
     10. ``rule_evidence_incomplete``：规则证据不完整（此前只有降级数进门禁，
         不完整数被丢弃）；
     11. ``rule_execution_error``：规则执行异常/解析失败（不再伪装成 hint finding）；
     12. ``report_type_mismatch``：报告类型冲突（doc_type 与内容判定不一致，
         或 ps_sync 因类型未知跳过/写错）；
     13. ``rules_not_executed``：无发现时所有适用规则必须已执行，
         否则不允许出 no_findings。

    ``degraded`` 保留原语义："部分能力降级但结论仍然有效"。

    注意 `issue_total` 必须传入"正式问题数"（不含降级项），否则会出现
    "问题全被降级却仍报 findings_detected"的口径矛盾。
    """
    coverage_threshold = _page_coverage_min_ratio()
    page_coverage = float(page_assessment.get("page_coverage") or 0.0)
    scanned_page_count = int(page_assessment.get("scanned_page_count") or 0)
    low_text_pages = page_assessment.get("low_text_pages")
    low_text_pages = low_text_pages if isinstance(low_text_pages, list) else []

    review_reasons: List[Dict[str, Any]] = []

    if page_coverage < coverage_threshold:
        review_reasons.append(
            {
                "code": "low_page_coverage",
                "message": (
                    f"页面文本覆盖率 {page_coverage:.2%} 低于门禁阈值 "
                    f"{coverage_threshold:.2%}，可能存在未被审核的页面"
                ),
                "pages": low_text_pages[:50],
            }
        )
    if scanned_page_count > 0:
        review_reasons.append(
            {
                "code": "scanned_pages_detected",
                "message": (
                    f"检测到 {scanned_page_count} 页疑似扫描页/无文本层，"
                    "当前未启用 OCR，这些页面内容未参与审核"
                ),
                "pages": (page_assessment.get("scanned_pages") or [])[:50],
            }
        )
    if str(report_kind or "").strip().lower() in {"", "unknown"}:
        review_reasons.append(
            {
                "code": "unknown_report_kind",
                "message": "未能识别材料是预算公开还是决算公开，仅执行了通用规则，专项规则未覆盖",
            }
        )
    if report_year is None:
        review_reasons.append(
            {
                "code": "unknown_report_year",
                "message": "未能识别报告年度，同比与口径类判断缺少基准",
            }
        )
    if ai_execution is not None:
        # 新契约：AI 执行状态机判定，请求后只有 succeeded 可通过
        review_reasons.extend(quality_gate_ai_reasons(ai_execution))
    elif ai_requested and ai_degraded and _ai_assist_required():
        # legacy 兼容路径：未提供 ai_execution 时沿用 AI_ASSIST_REQUIRED 语义
        review_reasons.append(
            {
                "code": "ai_assist_required_but_failed",
                "message": "AI 辅助被配置为必需能力但本次执行失败，结论覆盖面不完整",
            }
        )
    degraded_findings = max(0, int(evidence_degraded_count or 0))
    if degraded_findings > 0:
        review_reasons.append(
            {
                "code": "evidence_incomplete_findings",
                "message": (
                    f"有 {degraded_findings} 条 AI 问题缺少可复核证据，已降级为待复核，"
                    "不计入正式问题；需人工核对原文后再定性"
                ),
            }
        )

    # ---- 结构化入库信号（此前被丢弃，P0 §3.6） ----
    ingest_review_items: List[Dict[str, Any]] = []
    if isinstance(structured_ingest, dict) and isinstance(
        structured_ingest.get("review_items"), list
    ):
        ingest_review_items = [
            item for item in structured_ingest["review_items"] if isinstance(item, dict)
        ]
    missing_core_tables = [
        str(item.get("table_code") or item.get("id") or "")
        for item in ingest_review_items
        if str(item.get("type") or "") == "missing_core_table"
    ]
    if missing_core_tables:
        review_reasons.append(
            {
                "code": "missing_required_table",
                "message": (
                    f"结构化入库未识别到 {len(missing_core_tables)} 张核心表，"
                    "对应检查项未实际覆盖，不能视为已完成审核"
                ),
                "table_codes": missing_core_tables[:20],
            }
        )
    ambiguous_tables = [
        str(item.get("table_code") or item.get("id") or "")
        for item in ingest_review_items
        if str(item.get("type") or "") in {"unknown_table", "low_confidence_table"}
    ]
    if ambiguous_tables:
        review_reasons.append(
            {
                "code": "ambiguous_table_schema",
                "message": (
                    f"有 {len(ambiguous_tables)} 张表结构识别存在歧义（未知表/低置信度），"
                    "基于这些表的勾稽结论不可信"
                ),
                "table_codes": ambiguous_tables[:20],
            }
        )
    # 已识别到表格却未生成任何结构化 facts：解析/列映射存在系统性问题，
    # 仅标 parser_quality=poor 不足以阻断"完成"态——必须转人工复核
    # （GPT5.6 P0-2：fact_materialization_empty 不得允许假绿完成）。
    facts_empty = any(
        str(item.get("type") or "") == "fact_materialization_empty"
        for item in ingest_review_items
    )
    if facts_empty:
        review_reasons.append(
            {
                "code": "fact_materialization_empty",
                "message": (
                    "已识别到表格但未成功生成结构化 facts（列映射或数值单元格"
                    "解析失败），基于表格的勾稽与入库未实际完成，需人工复核"
                ),
            }
        )

    # ---- 规则证据完整性（此前只有降级数进门禁，不完整数被丢弃） ----
    if isinstance(evidence_completeness, dict):
        incomplete_count = int(evidence_completeness.get("incomplete") or 0)
        if incomplete_count > 0:
            review_reasons.append(
                {
                    "code": "rule_evidence_incomplete",
                    "message": (
                        f"有 {incomplete_count} 条问题缺少页码等可定位证据，"
                        "证据完整率未达 100%，需人工复核"
                    ),
                }
            )

    # ---- 规则执行摘要（parse/execution 异常不再伪装成 hint finding） ----
    if isinstance(rule_execution_summary, dict) and rule_execution_summary:
        error_rules = int(rule_execution_summary.get("execution_error") or 0)
        parse_error_rules = int(rule_execution_summary.get("parse_error") or 0)
        # insufficient_data 同样属"未得出可信结论"：数据缺失的规则被
        # 静默记 pass 是假绿 no_findings 的来源之一（GPT5.6 P0-2）。
        insufficient_rules = int(
            rule_execution_summary.get("insufficient_data") or 0
        )
        if error_rules + parse_error_rules > 0:
            review_reasons.append(
                {
                    "code": "rule_execution_error",
                    "message": (
                        f"有 {error_rules} 条规则执行异常、{parse_error_rules} 条解析失败，"
                        "对应检查项未得出可信结论"
                    ),
                    "unresolved_rules": (
                        rule_execution_summary.get("unresolved_rules") or []
                    )[:20],
                }
            )
        if insufficient_rules > 0:
            review_reasons.append(
                {
                    "code": "rules_insufficient_data",
                    "message": (
                        f"有 {insufficient_rules} 条规则因数据缺失/解析歧义无法得出结论"
                        "（如表缺失、行宽无法对齐），这些检查项未实际覆盖，需人工复核"
                    ),
                    "unresolved_rules": [
                        item
                        for item in (
                            rule_execution_summary.get("unresolved_rules") or []
                        )
                        if isinstance(item, dict)
                        and str(item.get("status") or "")
                        == STATUS_INSUFFICIENT_DATA
                    ][:20],
                }
            )

    # ---- 报告类型一致性 ----
    report_type_reason = _report_type_mismatch_reason(
        doc_type=doc_type,
        report_kind=report_kind,
        structured_ingest=structured_ingest,
    )
    if report_type_reason is not None:
        review_reasons.append(report_type_reason)

    # ---- no_findings 门禁：无发现时要求规则全部执行 ----
    # 摘要缺失/为空同样是"证据不足"：规则执行摘要只要不是非空 dict，
    # 就无法证明规则真的执行过，不允许输出 no_findings（GPT5.6 P0-2）。
    if issue_total == 0:
        if not (isinstance(rule_execution_summary, dict) and rule_execution_summary):
            review_reasons.append(
                {
                    "code": "rules_not_executed",
                    "message": (
                        "无问题发现但缺少规则执行摘要，无法证明适用规则已执行，"
                        "不允许输出 no_findings"
                    ),
                }
            )
        else:
            total_rules = int(rule_execution_summary.get("total_rules") or 0)
            executed_rules = int(rule_execution_summary.get("executed") or 0)
            if total_rules > 0 and executed_rules < total_rules:
                review_reasons.append(
                    {
                        "code": "rules_not_executed",
                        "message": (
                            f"适用规则中仅 {executed_rules}/{total_rules} 条得出结论，"
                            "不允许在规则未执行完毕时输出 no_findings"
                        ),
                    }
                )

    if review_reasons:
        status = JobStatus.REVIEW_REQUIRED.value
        quality_status = AnalysisQualityStatus.REVIEW_REQUIRED.value
        analysis_conclusion = AnalysisConclusion.INCOMPLETE.value
    else:
        status = JobStatus.DEGRADED.value if ai_degraded else JobStatus.DONE.value
        quality_status = (
            AnalysisQualityStatus.DEGRADED.value
            if ai_degraded
            else AnalysisQualityStatus.COMPLETE.value
        )
        analysis_conclusion = (
            AnalysisConclusion.FINDINGS_DETECTED.value
            if issue_total > 0
            else AnalysisConclusion.NO_FINDINGS.value
        )

    return {
        "status": status,
        "quality_status": quality_status,
        "analysis_conclusion": analysis_conclusion,
        "review_reasons": review_reasons,
        "page_coverage": page_coverage,
        "scanned_page_count": scanned_page_count,
        "coverage_threshold": coverage_threshold,
        "issue_total": issue_total,
        "evidence_degraded_count": degraded_findings,
        "ai_degraded": bool(ai_degraded),
        "ai_required": _ai_assist_required(),
        "ai_execution": ai_execution,
    }


_DOC_TYPE_FINAL = {
    "dept_final",
    "unit_final",
    "department_final",
    "final",
    "settlement",
    "accounts",
}
_DOC_TYPE_BUDGET = {
    "dept_budget",
    "unit_budget",
    "department_budget",
    "budget",
}


def _report_type_mismatch_reason(
    doc_type: Any,
    report_kind: Any,
    structured_ingest: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """检查请求 doc_type、内容判定 report_kind、入库 report_type 三者是否一致。

    任何一处冲突（或入库因类型未知跳过）都返回 ``report_type_mismatch``
    原因，防止决算材料以预算身份入库后无人察觉。
    """

    doc_type_text = str(doc_type or "").strip().lower()
    kind_text = str(report_kind or "").strip().lower()
    doc_type_is_final = doc_type_text in _DOC_TYPE_FINAL
    doc_type_is_budget = doc_type_text in _DOC_TYPE_BUDGET

    if doc_type_is_final and kind_text and kind_text != "unknown" and kind_text != "final":
        return {
            "code": "report_type_mismatch",
            "message": (
                f"上传声明 doc_type={doc_type_text}（决算），但内容判定为 "
                f"report_kind={kind_text}，报告类型存在冲突，需人工确认"
            ),
        }
    if doc_type_is_budget and kind_text and kind_text != "unknown" and kind_text != "budget":
        return {
            "code": "report_type_mismatch",
            "message": (
                f"上传声明 doc_type={doc_type_text}（预算），但内容判定为 "
                f"report_kind={kind_text}，报告类型存在冲突，需人工确认"
            ),
        }

    if not isinstance(structured_ingest, dict):
        return None
    ps_sync = structured_ingest.get("ps_sync")
    if not isinstance(ps_sync, dict):
        return None
    ps_status = str(ps_sync.get("status") or "")
    if ps_status == "skipped" and str(ps_sync.get("reason") or "") == "unknown_report_type":
        return {
            "code": "report_type_mismatch",
            "message": (
                "结构化入库因报告类型无法识别而跳过，报告未以任何类型入库，需人工确认"
            ),
        }
    ps_report_type = str(ps_sync.get("report_type") or "").strip().upper()
    if doc_type_is_final and ps_report_type and ps_report_type != "FINAL":
        return {
            "code": "report_type_mismatch",
            "message": (
                f"doc_type={doc_type_text}（决算）但入库 report_type={ps_report_type}，"
                "报告类型归类错误，需人工确认"
            ),
        }
    if doc_type_is_budget and ps_report_type and ps_report_type != "BUDGET":
        return {
            "code": "report_type_mismatch",
            "message": (
                f"doc_type={doc_type_text}（预算）但入库 report_type={ps_report_type}，"
                "报告类型归类错误，需人工确认"
            ),
        }
    return None


def _assess_parser_quality(
    page_assessment: Dict[str, Any],
    structured_ingest: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """解析质量评估，输出 ``result.meta.parser_quality``（增量字段）。

    quality 三级：ok / degraded / poor。poor 必然伴随质量门转人工复核
    （扫描页、覆盖率低、facts 为空都会另外生成 review_reasons）。
    """
    coverage = float(page_assessment.get("page_coverage") or 0.0)
    scanned_page_count = int(page_assessment.get("scanned_page_count") or 0)
    review_items: List[Dict[str, Any]] = []
    if isinstance(structured_ingest, dict) and isinstance(
        structured_ingest.get("review_items"), list
    ):
        review_items = [
            item for item in structured_ingest["review_items"] if isinstance(item, dict)
        ]
    missing_core = sum(
        1 for item in review_items if str(item.get("type") or "") == "missing_core_table"
    )
    unknown_tables = sum(
        1 for item in review_items if str(item.get("type") or "") == "unknown_table"
    )
    low_confidence = sum(
        1
        for item in review_items
        if str(item.get("type") or "") == "low_confidence_table"
    )
    facts_empty = any(
        str(item.get("type") or "") == "fact_materialization_empty"
        for item in review_items
    )

    if (
        scanned_page_count > 0
        or facts_empty
        or coverage < _page_coverage_min_ratio()
    ):
        quality = "poor"
    elif missing_core or unknown_tables or low_confidence:
        quality = "degraded"
    else:
        quality = "ok"

    return {
        "quality": quality,
        "page_coverage": coverage,
        "scanned_page_count": scanned_page_count,
        "missing_core_tables": missing_core,
        "unknown_tables": unknown_tables,
        "low_confidence_tables": low_confidence,
        "facts_materialization_empty": facts_empty,
    }


async def _run_pipeline(job_dir: Path) -> None:
    """
    真正的解析管线：
    - 读取 job_dir 下的 PDF
    - 解析文本与表格，构建 Document
    - 调用 build_issues_payload 打包返回
    - 写入 status.json（result.summary / result.issues / result.meta）
    """
    try:
        PIPELINE_TIMEOUT_SEC = int(os.getenv("PIPELINE_TIMEOUT_SEC", "600"))
    except Exception:
        PIPELINE_TIMEOUT_SEC = 600

    # 绑定 job_id 到日志上下文：本函数内（含被调用栈里任意模块）产生的每条日志
    # 都会自动带上 job_id，实现"全链路可按 job_id 检索"。
    # 用 contextvars 而非全局 LogRecordFactory，队列并发多任务时不会互相串字段。
    with log_context(job_id=job_dir.name):
        try:
            await asyncio.wait_for(
                _run_pipeline_inner(job_dir),
                timeout=PIPELINE_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.error(
                "pipeline timed out after %ss",
                PIPELINE_TIMEOUT_SEC,
                extra=safe_log_extra(
                    {"stage": "pipeline_timeout", "timeout_seconds": PIPELINE_TIMEOUT_SEC}
                ),
            )
            _safe_write(
                job_dir,
                {
                    "job_id": job_dir.name,
                    "status": "error",
                    "error": f"pipeline_timeout_{PIPELINE_TIMEOUT_SEC}s",
                    "ts": time.time(),
                },
            )
            await persist_analysis_job_snapshot(
                runtime.read_json_file(job_dir / "status.json", default={}),
                include_results=True,
            )


async def _run_pipeline_inner(job_dir: Path) -> None:
    """Pipeline body isolated for timeout wrapping.

    这里再绑一次 job_id 上下文：`_run_pipeline` 已经绑过，但测试与 worker 恢复
    路径会直接调用本函数，重复绑定是幂等的（同名同值），能保证任何入口都有 job_id。
    """
    with log_context(job_id=job_dir.name):
        await _run_pipeline_body(job_dir)


async def _run_pipeline_body(job_dir: Path) -> None:
    """真正的流水线主体。"""
    # 提前初始化 provider_stats，确保处理中/失败态也能返回该字段
    provider_stats: List[Dict[str, Any]] = []
    structured_ingest_summary: Dict[str, Any] = {}
    analysis_quality_status = "complete"
    # 解析前先给出"零覆盖"默认值：若在解析阶段就失败，错误态也能诚实报出覆盖率 0
    page_assessment: Dict[str, Any] = _assess_page_extraction([], [])
    try:
        # 读取检测模式配置
        status_file = job_dir / "status.json"
        use_local_rules = True
        use_ai_assist = True
        mode = "legacy"  # 默认为旧模式
        fiscal_year = None
        doc_type = None
        report_year = None
        report_kind = "unknown"
        organization_id = None
        organization_name = None

        if status_file.exists():
            try:
                status_data = json.loads(status_file.read_text(encoding="utf-8"))
                use_local_rules = status_data.get("use_local_rules", True)
                use_ai_assist = status_data.get("use_ai_assist", True)
                mode = status_data.get("mode", "legacy")
                fiscal_year = status_data.get("fiscal_year")
                doc_type = status_data.get("doc_type")
                organization_id = status_data.get("organization_id")
                organization_name = status_data.get("organization_name")
                report_year = runtime.parse_report_year(
                    status_data.get("report_year") or fiscal_year
                )
                # 上传时 preflight 已持久化识别结果，直接复用；旧任务无此字段时走下方兜底推断。
                report_kind = str(status_data.get("report_kind") or "").strip()
            except:
                pass

        # 检查是否启用双模式。
        # 必须走真实 Settings 接口（is_dual_mode_enabled 读 config/app.yaml 的
        # dual_mode.enabled）；此前用 "dual_mode.enabled" 点号风格调用
        # settings.get(section, key, default) 永远取到默认值 False，被字典式
        # mock 的测试掩盖（docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md §3.5）。
        # 请求契约（P0）：legacy 是规则模式别名，不运行 AI；
        # 只有 dual + use_ai_assist=true 才请求 AI。旧任务 status.json 可能
        # 残留 legacy + use_ai_assist=true（旧默认值），此时 ai_requested 如实
        # 记录、状态记 not_run（error_code=legacy_mode_no_ai），由质量门转
        # 人工复核，绝不静默忽略。
        dual_config_enabled = settings.is_dual_mode_enabled()
        dual_mode_enabled = dual_config_enabled and mode == "dual"
        raw_use_ai_assist = bool(use_ai_assist)
        if mode == "dual":
            ai_requested = raw_use_ai_assist and dual_config_enabled
            _ai_not_run_code = "" if dual_config_enabled else "dual_mode_disabled"
        else:
            # legacy 路径不运行 AI。若旧任务残留 use_ai_assist=true（旧默认值），
            # 必须如实记 requested=True + not_run（error_code=legacy_mode_no_ai）
            # 转人工复核；新请求已在 API 层 422 拦截，不存在静默忽略。
            ai_requested = raw_use_ai_assist
            _ai_not_run_code = "legacy_mode_no_ai" if raw_use_ai_assist else ""
        # legacy 路径没有 AI 能力：强制按"未请求 AI"执行；原始请求留痕进 ai_execution
        use_ai_assist = raw_use_ai_assist and dual_mode_enabled

        # 标记 processing
        _safe_write(
            job_dir,
            {
                "job_id": job_dir.name,
                "status": "processing",
                "progress": 5,
                "ts": time.time(),
                "use_local_rules": use_local_rules,
                "use_ai_assist": use_ai_assist,
                "mode": mode,
                "dual_mode_enabled": dual_mode_enabled,
                "stage": "开始解析文档",
            },
        )

        await persist_analysis_job_snapshot(
            runtime.read_json_file(job_dir / "status.json", default={})
        )

        pdf_path = _find_first_pdf(job_dir)
        if not report_kind or report_kind == "unknown":
            report_kind = runtime.normalize_report_kind(
                str(doc_type) if doc_type is not None else None,
                pdf_path.name,
            )
        started = time.time()

        # 读取 PDF -> 文本/表格
        _safe_write(
            job_dir,
            {
                "job_id": job_dir.name,
                "status": "processing",
                "progress": 15,
                "ts": time.time(),
                "use_local_rules": use_local_rules,
                "use_ai_assist": use_ai_assist,
                "mode": mode,
                "dual_mode_enabled": dual_mode_enabled,
                "stage": "解析PDF内容",
            },
        )

        def _sync_parse_pdf():
            p_texts = []
            p_tables = []
            f_size = pdf_path.stat().st_size
            with pdfplumber.open(str(pdf_path)) as pdf:
                for p in pdf.pages:
                    p_texts.append(_extract_visible_text_from_page(p))
                    p_tables.append(_extract_tables_from_page(p))
            return p_texts, p_tables, f_size

        loop = asyncio.get_running_loop()
        loop = asyncio.get_running_loop()
        # 解析资源隔离（缺口 P2-05 / B-09）：默认走可终止的独立进程 + 资源上限，
        # 恶意或超大 PDF 打爆的是子进程，不会连带拖死 worker。
        # 线程池路径保留给显式关闭隔离的场景（`PDF_PARSE_ISOLATION_ENABLED=false`，
        # 测试环境默认走这条，因为子进程里 monkeypatch 不生效）。
        if pdf_parse_isolation_enabled():
            parse_limits = resolve_pdf_parse_limits()
            logger.info(
                "parsing pdf in isolated process",
                extra=safe_log_extra(
                    {
                        "stage": "解析PDF内容",
                        "parse_timeout_seconds": parse_limits["timeout_seconds"],
                        "parse_max_pages": parse_limits["max_pages"],
                        "parse_memory_mb": parse_limits["memory_mb"],
                        "parse_memory_limit_supported": parse_limits[
                            "memory_limit_supported"
                        ],
                        "parse_start_method": parse_limits["start_method"],
                    }
                ),
            )
            try:
                parsed = await parse_pdf_in_process(pdf_path, parse_limits)
            except (PdfParseTimeout, PdfParseLimitExceeded, PdfParseError) as exc:
                code, detail = parse_error_code(exc)
                logger.error(
                    "pdf parsing rejected: %s",
                    code,
                    extra=safe_log_extra(
                        {"stage": "解析PDF内容", "parse_error_code": code}
                    ),
                )
                # 终态是 error 而不是 review_required：一页都没解析出来，
                # 没有任何可供人工复核的结论，标成待复核就是新的虚假成功。
                raise RuntimeError(f"{code}:{detail}") from exc
            page_texts = parsed["page_texts"]
            page_tables = parsed["page_tables"]
            filesize = parsed["filesize"]
        else:
            page_texts, page_tables, filesize = await loop.run_in_executor(
                None, _sync_parse_pdf
            )

        # 扫描页/低文本页检测：本轮只检测不 OCR，结果先落状态供前端与后续门禁使用
        page_assessment = _assess_page_extraction(page_texts, page_tables)
        if page_assessment["low_text_page_count"]:
            logger.warning(
                "job %s low-text pages detected: coverage=%.4f scanned=%d pages=%s",
                job_dir.name,
                page_assessment["page_coverage"],
                page_assessment["scanned_page_count"],
                page_assessment["low_text_pages"][:20],
                extra=safe_log_extra(
                    {
                        "stage": "page_extraction_assessed",
                        "page_coverage": page_assessment["page_coverage"],
                        "scanned_page_count": page_assessment["scanned_page_count"],
                        "low_text_page_count": page_assessment["low_text_page_count"],
                    }
                ),
            )

        # 构建 Document
        _safe_write(
            job_dir,
            {
                "job_id": job_dir.name,
                "status": "processing",
                "progress": 25,
                "ts": time.time(),
                "use_local_rules": use_local_rules,
                "use_ai_assist": use_ai_assist,
                "mode": mode,
                "dual_mode_enabled": dual_mode_enabled,
                "stage": "构建文档对象",
                "page_coverage": page_assessment["page_coverage"],
                "scanned_page_count": page_assessment["scanned_page_count"],
            },
        )

        def _sync_build_document():
            return build_document(
                path=str(pdf_path),
                page_texts=page_texts,
                page_tables=page_tables,
                filesize=filesize,
            )

        doc = await loop.run_in_executor(None, _sync_build_document)

        # 双模式分析
        if dual_mode_enabled:
            _safe_write(
                job_dir,
                {
                    "job_id": job_dir.name,
                    "status": "processing",
                    "progress": 35,
                    "ts": time.time(),
                    "use_local_rules": use_local_rules,
                    "use_ai_assist": use_ai_assist,
                    "mode": mode,
                    "dual_mode_enabled": dual_mode_enabled,
                    "stage": "双模式分析",
                },
            )

            # 构建JobContext
            from src.schemas.issues import JobContext
            from src.schemas.issues import AnalysisConfig

            job_context = JobContext(
                job_id=job_dir.name,
                pdf_path=str(pdf_path),
                page_texts=page_texts,
                page_tables=page_tables,
                filesize=filesize,
                meta={
                    "started_at": started,
                    "report_kind": report_kind,
                    "report_year": report_year,
                    "fiscal_year": fiscal_year,
                    "doc_type": doc_type,
                    "organization_id": organization_id,
                    "organization_name": organization_name,
                },
            )

            analysis_config = AnalysisConfig(
                dual_mode=True,
                ai_enabled=use_ai_assist,
                rule_enabled=use_local_rules,
                merge_enabled=True,
                enable_ai_analysis=use_ai_assist,
                enable_ai_locator=use_ai_assist,
                ai_fallback_on_error=True,
            )

            # 执行双模式分析
            dual_result = await dual_analyzer.analyze(job_context, analysis_config)
            rule_error = str(dual_result.meta.get("rule_error") or "").strip()
            ai_error = str(dual_result.meta.get("ai_error") or "").strip()
            if use_local_rules and rule_error:
                raise RuntimeError(f"local_rules_failed:{rule_error}")
            if use_ai_assist and (ai_error or dual_result.meta.get("fallback")):
                analysis_quality_status = "degraded"

            # 组装最终返回体（双模式结构）
            dual_ai_findings = [item.dict() for item in dual_result.ai_findings]
            dual_rule_findings = [item.dict() for item in dual_result.rule_findings]
            # AI 执行状态机（P0）：从调用留痕推导，无留痕禁止呈现为已执行
            ai_execution = build_ai_execution(
                ai_requested,
                ai_error=str(dual_result.meta.get("ai_error") or ""),
                fallback=dual_result.meta.get("fallback"),
                call_ledger=dual_result.meta.get("ai_call_ledger") or [],
                provider_stats=dual_result.meta.get("provider_stats") or [],
                window_errors=dual_result.meta.get("ai_window_errors") or [],
            )
            result = {
                "summary": "",
                "ai_findings": dual_ai_findings,
                "rule_findings": dual_rule_findings,
                "merged": dual_result.merged.dict(),
                "meta": {
                    "pages": len(page_texts),
                    "filesize": filesize,
                    "job_id": job_dir.name,
                    "started_at": started,
                    "finished_at": time.time(),
                    "use_local_rules": use_local_rules,
                    "use_ai_assist": use_ai_assist,
                    "mode": mode,
                    "dual_mode_enabled": dual_mode_enabled,
                    "fiscal_year": fiscal_year,
                    "doc_type": doc_type,
                    "report_year": report_year,
                    "report_kind": report_kind,
                    "elapsed_ms": dual_result.meta.get("elapsed_ms", {}),
                    "tokens": dual_result.meta.get("tokens", {}),
                    "page_extraction": page_assessment,
                    "ai_execution": ai_execution,
                    "rule_execution_summary": dual_result.meta.get(
                        "rule_execution_summary"
                    )
                    or {},
                    "parser_quality": _assess_parser_quality(
                        page_assessment, None
                    ),
                    # 版本留痕汇总（P2-02）：从各条 finding 实际写入的版本反向汇总，
                    # 不额外拍一份"声明值"，避免汇总与逐条留痕不一致。
                    "versions": summarize_finding_versions(
                        [*dual_ai_findings, *dual_rule_findings]
                    ),
                },
            }
        else:
            # 传统模式分析
            # AI辅助检测阶段
            if use_ai_assist:
                _safe_write(
                    job_dir,
                    {
                        "job_id": job_dir.name,
                        "status": "processing",
                        "progress": 35,
                        "ts": time.time(),
                        "use_local_rules": use_local_rules,
                        "use_ai_assist": use_ai_assist,
                        "mode": mode,
                        "dual_mode_enabled": dual_mode_enabled,
                        "stage": "AI辅助状态",
                    },
                )

                _safe_write(
                    job_dir,
                    {
                        "job_id": job_dir.name,
                        "status": "processing",
                        "progress": 50,
                        "ts": time.time(),
                        "use_local_rules": use_local_rules,
                        "use_ai_assist": use_ai_assist,
                        "mode": mode,
                        "dual_mode_enabled": dual_mode_enabled,
                        "stage": "开始抽取",
                    },
                )

                # 这里会调用AI抽取服务，在build_issues_payload中处理
                _safe_write(
                    job_dir,
                    {
                        "job_id": job_dir.name,
                        "status": "processing",
                        "progress": 80,
                        "ts": time.time(),
                        "use_local_rules": use_local_rules,
                        "use_ai_assist": use_ai_assist,
                        "mode": mode,
                        "dual_mode_enabled": dual_mode_enabled,
                        "stage": "抽取完成",
                    },
                )

                _safe_write(
                    job_dir,
                    {
                        "job_id": job_dir.name,
                        "status": "processing",
                        "progress": 90,
                        "ts": time.time(),
                        "use_local_rules": use_local_rules,
                        "use_ai_assist": use_ai_assist,
                        "mode": mode,
                        "dual_mode_enabled": dual_mode_enabled,
                        "stage": "结果转换",
                    },
                )

            # 运行规则并打包统一结构（issues: {error/warn/info/all}）
            _safe_write(
                job_dir,
                {
                    "job_id": job_dir.name,
                    "status": "processing",
                    "progress": 95,
                    "ts": time.time(),
                    "use_local_rules": use_local_rules,
                    "use_ai_assist": use_ai_assist,
                    "mode": mode,
                    "dual_mode_enabled": dual_mode_enabled,
                    "stage": "执行规则检查",
                    "provider_stats": provider_stats,
                },
            )

            # 使用线程池为规则检查设置超时，避免在95%阶段长时间卡住
            provider_stats = []
            try:
                RULES_TIMEOUT_SEC = int(os.getenv("RULES_TIMEOUT_SEC", "150"))
            except Exception:
                RULES_TIMEOUT_SEC = 150

            try:
                payload_issues = await run_rules_in_process(
                    doc,
                    use_ai_assist,
                    report_kind,
                    RULES_TIMEOUT_SEC,
                )
            except RuleExecutionTimeout as exc:
                provider_stats.append(
                    {
                        "fell_back": True,
                        "provider_used": "engine",
                        "error": f"rules_timeout_{RULES_TIMEOUT_SEC}s",
                        "latency_ms": RULES_TIMEOUT_SEC * 1000,
                        "timestamp": time.time(),
                    }
                )
                # 及时写入处理中状态，便于前端读取 provider_stats
                _safe_write(
                    job_dir,
                    {
                        "job_id": job_dir.name,
                        "status": "processing",
                        "progress": 95,
                        "ts": time.time(),
                        "use_local_rules": use_local_rules,
                        "use_ai_assist": use_ai_assist,
                        "mode": mode,
                        "dual_mode_enabled": dual_mode_enabled,
                        "stage": "执行规则检查（超时回退）",
                        "provider_stats": provider_stats,
                    },
                )
                raise RuntimeError(f"local_rules_timeout:{exc}") from exc
            except RuleExecutionError as exc:
                provider_stats.append(
                    {
                        "fell_back": True,
                        "provider_used": "engine",
                        "error": f"rules_error:{exc}",
                        "timestamp": time.time(),
                    }
                )
                # 及时写入处理中状态，便于前端读取 provider_stats
                _safe_write(
                    job_dir,
                    {
                        "job_id": job_dir.name,
                        "status": "processing",
                        "progress": 95,
                        "ts": time.time(),
                        "use_local_rules": use_local_rules,
                        "use_ai_assist": use_ai_assist,
                        "mode": mode,
                        "dual_mode_enabled": dual_mode_enabled,
                        "stage": "执行规则检查（异常回退）",
                        "provider_stats": provider_stats,
                    },
                )
                raise RuntimeError(f"local_rules_failed:{exc}") from exc

            # 组装最终返回体（保持你之前的契约字段）
            # legacy 路径的 AI 执行状态：请求过但路径本身不运行 AI → not_run；
            # 新契约下 legacy 不再请求 AI（422 拦截），这里是旧任务兜底如实留痕。
            ai_execution = build_ai_execution(
                ai_requested,
                error_code=_ai_not_run_code,
            )
            result = {
                "summary": "",  # 现在没有汇总，可后续填充
                "issues": payload_issues["issues"],  # 统一分桶结构
                "meta": {
                    "pages": len(page_texts),
                    "filesize": filesize,
                    "job_id": job_dir.name,
                    "started_at": started,
                    "finished_at": time.time(),
                    "use_local_rules": use_local_rules,
                    "use_ai_assist": use_ai_assist,
                    "mode": mode,
                    "dual_mode_enabled": dual_mode_enabled,
                    "fiscal_year": fiscal_year,
                    "doc_type": doc_type,
                    "report_year": report_year,
                    "report_kind": report_kind,
                    "provider_stats": provider_stats,
                    "page_extraction": page_assessment,
                    "ai_execution": ai_execution,
                    "rule_execution_summary": payload_issues.get(
                        "rule_execution_summary"
                    )
                    or {},
                    "parser_quality": _assess_parser_quality(page_assessment, None),
                    "versions": summarize_finding_versions(
                        payload_issues["issues"].get("all") or []
                    ),
                },
            }

        if (os.getenv("DATABASE_URL") or "").strip():
            _safe_write(
                job_dir,
                {
                    "job_id": job_dir.name,
                    "status": "processing",
                    "progress": 98,
                    "ts": time.time(),
                    "use_local_rules": use_local_rules,
                    "use_ai_assist": use_ai_assist,
                    "mode": mode,
                    "dual_mode_enabled": dual_mode_enabled,
                    "stage": "结构化入库",
                    "provider_stats": provider_stats,
                },
            )

        structured_metadata = {
            "organization_id": organization_id,
            "organization_name": organization_name,
            "fiscal_year": fiscal_year,
            "doc_type": doc_type,
            "report_year": report_year,
            "report_kind": report_kind,
            "checksum": status_data.get("checksum")
            if "status_data" in locals() and isinstance(status_data, dict)
            else None,
        }
        if (os.getenv("DATABASE_URL") or "").strip():
            current_ingest_status = runtime.read_json_file(job_dir / "status.json", default={})
            latest_ingest = runtime.resolve_latest_structured_ingest_job(
                job_dir.name,
                organization_id=organization_id,
                organization_name=organization_name,
                fiscal_year=fiscal_year,
                report_year=report_year,
                doc_type=doc_type,
                report_kind=report_kind,
                filename=pdf_path.name,
                current_status_payload={
                    "job_id": job_dir.name,
                    "filename": pdf_path.name,
                    "organization_id": organization_id,
                    "organization_name": organization_name,
                    "fiscal_year": fiscal_year,
                    "doc_type": doc_type,
                    "report_year": report_year,
                    "report_kind": report_kind,
                    **runtime.extract_job_status_context(current_ingest_status),
                },
            )
            if latest_ingest.get("is_latest"):
                structured_ingest_summary = await run_structured_ingest(
                    job_id=job_dir.name,
                    pdf_path=pdf_path,
                    metadata=structured_metadata,
                )
            else:
                structured_ingest_summary = {
                    "job_id": job_dir.name,
                    "status": "skipped",
                    "reason": "not_latest_version",
                    "latest_job_id": latest_ingest.get("latest_job_id"),
                    "latest_filename": latest_ingest.get("latest_filename"),
                    "review_item_count": 0,
                    "review_items": [],
                }
        else:
            structured_ingest_summary = await run_structured_ingest(
                job_id=job_dir.name,
                pdf_path=pdf_path,
                metadata=structured_metadata,
            )
        runtime.write_structured_ingest_payload(job_dir, structured_ingest_summary)
        result["meta"]["structured_ingest"] = structured_ingest_summary
        # 解析质量以结构化入库结果为准重新评估（含缺表/歧义/facts 信号）
        result["meta"]["parser_quality"] = _assess_parser_quality(
            page_assessment, structured_ingest_summary
        )

        # 证据链完整性校验（P0-07）：落库前逐条校验证据，
        # 缺证据的 AI 问题就地降级为待复核，规则问题只记录告警。
        evidence_completeness = apply_evidence_completeness(result)
        result["meta"]["evidence_completeness"] = evidence_completeness
        if evidence_completeness["degraded_count"] or evidence_completeness["rule_warning_count"]:
            logger.warning(
                "job %s evidence check: rate=%.4f degraded=%d rule_warnings=%d",
                job_dir.name,
                evidence_completeness["completeness_rate"],
                evidence_completeness["degraded_count"],
                evidence_completeness["rule_warning_count"],
                extra=safe_log_extra(
                    {
                        "stage": "evidence_checked",
                        "evidence_completeness_rate": evidence_completeness[
                            "completeness_rate"
                        ],
                        "evidence_degraded_count": evidence_completeness["degraded_count"],
                        "evidence_rule_warning_count": evidence_completeness[
                            "rule_warning_count"
                        ],
                    }
                ),
            )

        # 任务级质量门禁：done 只在门禁全过时出现，否则转 review_required
        quality_gate = _evaluate_quality_gate(
            page_assessment=page_assessment,
            report_kind=report_kind,
            report_year=report_year,
            ai_requested=ai_requested,
            ai_degraded=analysis_quality_status == "degraded",
            # 计数口径与证据校验保持一致：降级项不算正式问题
            issue_total=_count_result_findings(result),
            evidence_degraded_count=evidence_completeness["degraded_count"],
            ai_execution=result["meta"].get("ai_execution"),
            structured_ingest=structured_ingest_summary,
            evidence_completeness=evidence_completeness,
            rule_execution_summary=result["meta"].get("rule_execution_summary"),
            doc_type=doc_type,
        )
        result["meta"]["quality_gate"] = quality_gate
        final_status = quality_gate["status"]
        if final_status == JobStatus.REVIEW_REQUIRED.value:
            stage_text = "完成（需人工复核）"
            logger.warning(
                "job %s gated to review_required: %s",
                job_dir.name,
                [reason["code"] for reason in quality_gate["review_reasons"]],
                extra=safe_log_extra(
                    {
                        "stage": "quality_gate",
                        "review_reason_codes": [
                            reason["code"] for reason in quality_gate["review_reasons"]
                        ],
                        "quality_status": quality_gate["quality_status"],
                        "analysis_conclusion": quality_gate["analysis_conclusion"],
                    }
                ),
            )
        elif final_status == JobStatus.DEGRADED.value:
            stage_text = "完成（部分能力降级）"
        else:
            stage_text = "完成"

        payload = {
            "job_id": job_dir.name,
            "status": final_status,
            "progress": 100,
            "result": result,
            "ts": time.time(),
            "use_local_rules": use_local_rules,
            "use_ai_assist": use_ai_assist,
            "mode": mode,
            "dual_mode_enabled": dual_mode_enabled,
            "fiscal_year": fiscal_year,
            "doc_type": doc_type,
            "report_year": report_year,
            "report_kind": report_kind,
            "structured_ingest": structured_ingest_summary,
            "quality_status": quality_gate["quality_status"],
            "analysis_conclusion": quality_gate["analysis_conclusion"],
            "review_reasons": quality_gate["review_reasons"],
            "page_coverage": page_assessment["page_coverage"],
            "scanned_page_count": page_assessment["scanned_page_count"],
            "stage": stage_text,
        }
        _safe_write(job_dir, payload)
        await persist_analysis_job_snapshot(payload, include_results=True)

    except Exception as e:
        # 只记异常类型与消息，不记 PDF 正文/证据原文
        logger.exception(
            "pipeline failed: %s",
            type(e).__name__,
            extra=safe_log_extra(
                {"stage": "pipeline_error", "error_type": type(e).__name__}
            ),
        )
        # 错误态显式覆盖质量字段，避免上一轮分析残留的 complete/done 结论被继承
        _safe_write(
            job_dir,
            {
                "job_id": job_dir.name,
                "status": "error",
                "error": str(e),
                "ts": time.time(),
                "fiscal_year": fiscal_year,
                "doc_type": doc_type,
                "report_year": report_year,
                "report_kind": report_kind,
                "provider_stats": provider_stats,
                "structured_ingest": structured_ingest_summary,
                "quality_status": AnalysisQualityStatus.REVIEW_REQUIRED.value,
                "analysis_conclusion": AnalysisConclusion.ANALYSIS_ERROR.value,
                "page_coverage": page_assessment.get("page_coverage", 0.0),
                "scanned_page_count": page_assessment.get("scanned_page_count", 0),
            },
        )
        await persist_analysis_job_snapshot(
            runtime.read_json_file(job_dir / "status.json", default={}),
            include_results=True,
        )


runtime.set_pipeline_runner(_run_pipeline)
register_routes(app)

