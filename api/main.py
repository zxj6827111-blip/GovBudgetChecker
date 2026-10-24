# api/main.py
import os

from dotenv import load_dotenv

load_dotenv()

import json
import time
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

import pdfplumber
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import sys as _sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from src.engine.check_obligations import (
    attach_obligation_ids,
    build_check_receipts,
    build_obligation_ledger,
)
from src.engine.rule_policy import PORTABLE_RULE_POLICY_VERSION
from src.engine.rule_registry import RULE_REGISTRY_VERSION
from src.engine.pipeline import (
    apply_readability_gate,
    build_document,
    build_issues_payload,
)
from src.engine.rule_outcome import STATUS_INSUFFICIENT_DATA
from src.services.document_profile_resolver import resolve_document_profile
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
from src.schemas.review_report import build_review_report
from api import runtime
from api.job_queue import DurableJobQueue
from api.queue_runtime import (
    compute_queue_workers,
    env_flag,
    get_queue_role,
    queue_resume_on_start,
    should_start_local_queue,
)
from api.routes import register_routes

from src.services.analyze_dual import DualModeAnalyzer
from src.services.ocr_pipeline import maybe_ocr_scanned_pages
from src.services.analysis_result_store import (
    persist_analysis_job_snapshot,
    sync_pending_analysis_snapshots,
)
from src.db.connection import DatabaseConnection
from src.services.issue_workflow_store import sync_workflow_recovery_state
from src.services.structured_ingest_runner import (
    build_ingest_metadata,
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
_job_list_warmup_task: asyncio.Task | None = None


async def _warm_job_list_caches() -> None:
    try:
        warmed_count = await asyncio.to_thread(runtime.warm_job_list_caches)
        logger.info("Job list caches warmed for %d jobs", warmed_count)
    except Exception:
        logger.exception("Job list cache warm-up failed")
    # 工作台与质量管理页还会读 /api/metrics：首次汇总要扫描全部任务目录，一并提前做掉。
    try:
        from src.services.metrics import collect_metrics_cached

        await asyncio.to_thread(collect_metrics_cached, runtime.UPLOAD_ROOT)
    except Exception:
        logger.exception("Metrics cache warm-up failed")


def _start_job_list_warmup() -> None:
    """后台预热任务列表缓存；只读文件，不阻塞启动，worker 进程与测试环境不做。"""
    global _job_list_warmup_task
    if get_queue_role() == "worker" or not env_flag("JOB_LIST_CACHE_WARMUP", not env_flag("TESTING", False)):
        return
    _job_list_warmup_task = asyncio.create_task(_warm_job_list_caches())


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
    global _workflow_mirror_task, _job_list_warmup_task
    if _job_list_warmup_task is not None:
        # 线程里的读文件无法中断，这里只是不再等待它的结果。
        _job_list_warmup_task.cancel()
        _job_list_warmup_task = None
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
    _start_job_list_warmup()
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
    backend_port = os.getenv("GOVBUDGET_BACKEND_PORT", "18743")
    origins += [
        f"https://{codespace}-3000.{gh_dom}",
        f"https://{codespace}-{backend_port}.{gh_dom}",
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
        for key in ("pipeline_budget", "ocr_progress"):
            if key in existing:
                merged_payload.setdefault(key, existing[key])

        job_status = str(payload.get("status") or "unknown")
        if job_status in runtime.TERMINAL_ANALYSIS_STATUSES or job_status == "cancelled":
            from src.services.check_progress import finish_incomplete
            finish_incomplete(job_dir.name, "cancelled" if job_status == "cancelled" else "failed")
        if isinstance(merged_payload.get("pipeline_budget"), dict):
            elapsed_budget = dict(merged_payload["pipeline_budget"])
            elapsed_budget["elapsed_seconds"] = round(max(0, time.time() - float(elapsed_budget.get("started_at") or time.time())), 2)
            if job_status in runtime.TERMINAL_ANALYSIS_STATUSES:
                elapsed_budget.setdefault("outcome", job_status)
            merged_payload["pipeline_budget"] = elapsed_budget
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
        # ZC-01：终态结果额外写入版本化快照 + 原子当前指针；
        # 重分析追加新版本目录，不删除旧快照。失败不影响主链路。
        runtime.write_analysis_revision_snapshot(job_dir, merged_payload)
    except Exception as e:
        (job_dir / "status_error.log").write_text(str(e), encoding="utf-8")
        if payload.get("status") in runtime.TERMINAL_ANALYSIS_STATUSES:
            raise

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
    # 统一使用 runtime 的 canonical-PDF 选择逻辑：多 PDF 任务必须由
    # status.json 的 filename/saved_path 唯一指向原件，不能把 annotated
    # 派生文件按字母序误当成输入。
    try:
        return runtime.find_first_pdf(job_dir)
    except FileNotFoundError as exc:
        raise FileNotFoundError("未在该 job 目录下找到 PDF 文件") from exc


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

    此函数只负责"检测 + 算原生文本/表格覆盖率"。OCR 候选单独保存在
    ParseArtifact；在置信度、表头口径和复识别校验完成前，不降低本门禁风险。

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
    obligation_coverage: Optional[Dict[str, Any]] = None,
    fact_overlay_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """任务级质量门禁：决定终态是 done / degraded / review_required。

    设计意图是把"分析跑完了"和"结论可信"分开：只有门禁全过才允许出 done，
    否则一律转 review_required + incomplete，避免"扫描件静默漏检却显示审核通过"。

    触发人工复核的条件（任一命中即转）：
      1. 页面文本覆盖率低于阈值；
      2. 存在疑似扫描页（OCR 未通过确定性使用校验，内容尚未参与规则）；
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
      14. ``check_obligations_incomplete``：存在"应执行但未完成"的检查义务
         （尚未实现 / 未执行 / 取数不足 / 解析歧义 / 执行异常 / 画像未确认）。
         这是本项目"漏查不得被当成通过"的核心闸门：规则注册数、规则执行数
         都不参与这里的判定，分母只由义务清单里"应检查的事项"决定，
         因此删规则、跳过输入、把缺失当零都无法让完成率变绿。

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

    reading = ((structured_ingest or {}).get("reading_import") or {}).get("reading_quality")
    if not isinstance(reading, dict):
        reading = page_assessment.get("table_reading") or {}
    reading_gaps = reading.get("gaps") or []
    unbound = reading.get("unbound_money_cells") or []
    fiscal_coverage = reading.get("fiscal_coverage") or {}
    mapping_tables = reading.get("mapping_incomplete_tables") or []
    if reading_gaps or unbound or mapping_tables:
        named = "、".join(f"第{t.get('page')}页《{t.get('title') or t.get('table_code')}》" for t in mapping_tables[:5])
        review_reasons.append({
            "code": "table_reading_incomplete",
            "message": (f"还有 {len(reading_gaps)} 处表格读取缺口、{len(unbound)} 个金额格待复核"
                        f"（缺少事实 {fiscal_coverage.get('fact_missing_count', len(unbound))}，"
                        f"列身份未确认 {fiscal_coverage.get('column_identity_unresolved_count', 0)}，"
                        f"合计与分项系统性对不上 {fiscal_coverage.get('column_arithmetic_conflict_count', 0)}，"
                        f"事实冲突 {fiscal_coverage.get('fact_conflict_count', 0)}，"
                        f"规则准入阻断 {fiscal_coverage.get('rule_blocked_count', 0)}）"
                        + (f"；映射不完整的表 {len(mapping_tables)} 张：{named}" if mapping_tables else "")
                        + "，相关检查不能视为已完成"),
            "pages": sorted({g.get("page") for g in [*reading_gaps, *unbound, *mapping_tables]
                             if type(g.get("page")) is int}),
            "reading_gap_count": len(reading_gaps), "unbound_money_cell_count": len(unbound),
            "mapping_incomplete_tables": mapping_tables,
            "fiscal_coverage": fiscal_coverage,
            "recovery": reading.get("recovery") or {},
        })
    identity_gaps = {k: v for k, v in (reading.get("fact_identity_gaps") or {}).items() if v}
    if identity_gaps:
        review_reasons.append({
            "code": "fact_identity_incomplete",
            "message": (f"已入库的财政数据身份不完整（主体为空 {identity_gaps.get('organization_missing', 0)} 条，"
                        f"科目名称只有编码 {identity_gaps.get('subject_name_is_code', 0)} 条），"
                        "这些数据不能计为已完成入库"),
            "fact_identity_gaps": identity_gaps,
        })

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
                    "OCR结果尚未通过置信度、口径绑定及复识别校验，内容未参与确定性规则检查"
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

    # ---- RC-10：人工校正未完全生效时，结论必须亮出来而不是静默通过 ----
    if isinstance(fact_overlay_report, dict):
        overlay_status = str(fact_overlay_report.get("status") or "")
        unresolved_overlays = [
            item
            for item in (fact_overlay_report.get("unresolved") or [])
            if isinstance(item, dict)
        ]
        blocking_unsupported = [
            item
            for item in (fact_overlay_report.get("unsupported") or [])
            if isinstance(item, dict) and item.get("blocking")
        ]
        if overlay_status in {"failed", "partial"} or blocking_unsupported:
            parts: List[str] = []
            if unresolved_overlays:
                parts.append(
                    f"{len(unresolved_overlays)} 条校正目标无法映射到当前事实版本"
                )
            if blocking_unsupported:
                fields = sorted({str(item.get("field") or "unknown") for item in blocking_unsupported})
                parts.append(
                    f"{len(blocking_unsupported)} 条校正字段未自动生效（{', '.join(fields)}）"
                )
            review_reasons.append(
                {
                    "code": "correction_overlay_incomplete",
                    "message": (
                        "人工校正未完全生效："
                        + "；".join(parts)
                        + "。相关差异按未校正数据计算，请人工确认校正目标后重新分析"
                    ),
                }
            )

    # ---- 结构化入库信号（此前被丢弃，P0 §3.6） ----
    if isinstance(structured_ingest, dict) and (
        structured_ingest.get("status") == "error"
        or (structured_ingest.get("unified_storage") or {}).get("status") == "error"
    ):
        review_reasons.append({
            "code": "structured_ingest_failed",
            "message": "本次结构化事实或证据未成功保存，需修复保存失败后重新分析。",
            "failed_stage": structured_ingest.get("ingest_stage"),
        })
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

    # ---- 检查义务台账：应执行而未完成，一律不得算通过 ----
    # 与上面的规则执行摘要刻意分开：摘要回答"调度出去的规则跑成什么样"，
    # 台账回答"应该检查的事情完成了多少"。前者按实现方视角计数，后者按
    # 业务要求计数，因此"规则不存在"这类漏查只有台账能发现。
    obligation_blocking = 0
    if isinstance(obligation_coverage, dict):
        try:
            obligation_blocking = int(obligation_coverage.get("blocking_total") or 0)
        except (TypeError, ValueError):
            obligation_blocking = 0
        if obligation_blocking > 0:
            by_reason = obligation_coverage.get("by_reason")
            by_reason = by_reason if isinstance(by_reason, dict) else {}
            labels = obligation_coverage.get("by_reason_labels")
            labels = labels if isinstance(labels, dict) else {}
            parts = [
                f"{labels.get(code, code)} {count} 项"
                for code, count in sorted(by_reason.items(), key=lambda kv: -kv[1])
            ]
            review_reasons.append(
                {
                    "code": "check_obligations_incomplete",
                    "message": (
                        f"应检查事项中有 {obligation_blocking} 项未完成"
                        f"（{'、'.join(parts)}），本次结论只覆盖已完成的检查范围，"
                        "不能视为已完整审核"
                    ),
                    "blocking_obligation_ids": (
                        obligation_coverage.get("blocking_obligation_ids") or []
                    )[:40],
                    "catalog_version": obligation_coverage.get("catalog_version"),
                }
            )

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
        # "未发现问题"必须限定为"在本次已完成的检查范围内"（plan §3）。
        # 结论为 incomplete 时同样需要给出范围说明，否则前端只能显示
        # 一句"需人工复核"，读者无法判断到底哪些检查没做完。
        conclusion_scope = "incomplete_scope"
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
        conclusion_scope = (
            "findings_within_covered_checks"
            if issue_total > 0
            else "no_findings_within_covered_checks"
        )

    coverage_snapshot: Optional[Dict[str, Any]] = None
    if isinstance(obligation_coverage, dict):
        coverage_snapshot = {
            key: obligation_coverage.get(key)
            for key in (
                "catalog_version",
                "applicable_total",
                "completed_total",
                "not_applicable_total",
                "unresolved_total",
                "blocking_total",
                "coverage_rate",
                "auto_completion_rate",
                "by_reason",
                "by_reason_labels",
                "by_group",
                "notes",
            )
        }

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
        # 结论的作用域标签与覆盖摘要：页面对外展示"这是检查范围"，而不是
        # 让"未发现问题"看起来像"材料没问题"。
        "conclusion_scope": conclusion_scope,
        "obligation_coverage": coverage_snapshot,
        "obligation_blocking_total": obligation_blocking,
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
        or (isinstance(structured_ingest, dict) and structured_ingest.get("status") == "error")
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


def _resolve_pipeline_budget(job_dir: Path) -> Dict[str, Any]:
    """The worker's total budget is independent from an HTTP wait deadline.

    Reserve the existing 600 seconds for native parse/rules/ingest and, when OCR
    is enabled, one 600-second soft page allowance per physical
    page. The hard wall-clock ceiling stays in place even on oversized inputs.
    """
    from src.services.ocr_client import ocr_enabled
    from src.services.ocr_tiles import PAGE_SOFT_BUDGET

    pages = 0
    try:
        pages = runtime.get_pdf_page_count(runtime.find_first_pdf(job_dir))
    except Exception:
        pass
    explicit = os.getenv("PIPELINE_TIMEOUT_SEC", "").strip()
    ai_budget_seconds = 0
    status = runtime.read_json_file(job_dir / "status.json", default={})
    if status.get("use_ai_assist") and status.get("mode") == "dual":
        from src.services.ai_model_settings import resolve_job_runtime, audit_time_budget
        try:
            ai_config = resolve_job_runtime(job_dir.name, status.get("ai_profile_id"))
            ai_budget_seconds = audit_time_budget(ai_config["timeout_s"] if ai_config else 90)
        except ValueError:
            pass
    default_seconds = 600 + ai_budget_seconds + (int(PAGE_SOFT_BUDGET) * pages if ocr_enabled() else 0)
    try:
        seconds = int(explicit) if explicit else default_seconds
        if seconds <= 0:
            raise ValueError("non-positive budget")
    except (TypeError, ValueError):
        seconds = default_seconds
        explicit = ""
    return {
        "total_seconds": min(seconds, 7200),
        "hard_limit_seconds": 7200,
        "cleanup_grace_seconds": 7,
        "mode": "explicit" if explicit else "page_aware_default",
        "physical_page_count": pages,
        "native_stage_reserve_seconds": 600,
        "ai_budget_seconds": ai_budget_seconds,
        "ocr_page_seconds": PAGE_SOFT_BUDGET if ocr_enabled() else 0,
        "elapsed_seconds": 0,
        "started_at": time.time(),
        "cancellation_scope": "worker_and_client_requests; OCR service may finish the current bounded inference",
    }


async def _run_pipeline(job_dir: Path) -> None:
    """
    真正的解析管线：
    - 读取 job_dir 下的 PDF
    - 解析文本与表格，构建 Document
    - 调用 build_issues_payload 打包返回
    - 写入 status.json（result.summary / result.issues / result.meta）
    """
    from src.services.analysis_timing import ai_queue_wait_scope

    budget = _resolve_pipeline_budget(job_dir)
    timeout_seconds = budget["total_seconds"]
    started = time.monotonic()
    cancel_path = job_dir / ".cancel-request.json"
    existing = runtime.read_json_file(job_dir / "status.json", default={})
    cancel_request = runtime.read_json_file(cancel_path, default={})
    if float(cancel_request.get("ts") or 0) < float(existing.get("ts") or 0):
        cancel_path.unlink(missing_ok=True)
    runtime.write_json_file(job_dir / "status.json", {**existing, "pipeline_budget": budget})

    # 绑定 job_id 到日志上下文：本函数内（含被调用栈里任意模块）产生的每条日志
    # 都会自动带上 job_id，实现"全链路可按 job_id 检索"。
    # 用 contextvars 而非全局 LogRecordFactory，队列并发多任务时不会互相串字段。
    with log_context(job_id=job_dir.name), ai_queue_wait_scope() as queue_wait:
        def timing_fields():
            elapsed = time.monotonic() - started
            return {
                "elapsed_seconds": round(elapsed, 2),
                "ai_queue_wait_seconds": round(queue_wait.elapsed, 2),
                "active_elapsed_seconds": round(max(0, elapsed - queue_wait.elapsed), 2),
            }

        try:
            task = asyncio.create_task(_run_pipeline_inner(job_dir))
            while not task.done():
                if cancel_path.exists():
                    raise asyncio.CancelledError("user_cancelled")
                elapsed = time.monotonic() - started
                # Default work budgets cover processing, while queue waiting
                # is charged only against the existing hard wall-clock cap.
                # Explicit operator deadlines remain strict wall-clock limits.
                chargeable = elapsed - queue_wait.elapsed if budget["mode"] == "page_aware_default" else elapsed
                remaining = min(timeout_seconds - chargeable, budget["hard_limit_seconds"] - elapsed)
                if remaining <= 0:
                    raise asyncio.TimeoutError
                await asyncio.wait({task}, timeout=min(1.0, remaining))
            await task
            final_payload = runtime.read_json_file(job_dir / "status.json", default={})
            outcome = ('pending' if final_payload.get('status') == 'queued' else
                       'error' if final_payload.get('status') == 'error' else 'completed')
            _safe_write(job_dir, {**final_payload, "pipeline_budget": {**budget, **timing_fields(), "outcome": outcome}})
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            logger.error(
                "pipeline timed out after %ss",
                timeout_seconds,
                extra=safe_log_extra(
                    {"stage": "pipeline_timeout", "timeout_seconds": timeout_seconds}
                ),
            )
            _safe_write(
                job_dir,
                {
                    "job_id": job_dir.name,
                    "status": "error",
                    "error": f"pipeline_timeout_{timeout_seconds}s",
                    "pipeline_budget": {**budget, **timing_fields(), "outcome": "timeout"},
                    "quality_status": AnalysisQualityStatus.REVIEW_REQUIRED.value,
                    "analysis_conclusion": AnalysisConclusion.ANALYSIS_ERROR.value,
                    "ts": time.time(),
                },
            )
            await persist_analysis_job_snapshot(
                runtime.read_json_file(job_dir / "status.json", default={}),
                include_results=True,
            )
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            _safe_write(job_dir, {
                "job_id": job_dir.name, "status": "error", "error": "pipeline_cancelled",
                "ts": time.time(), "cancel_requested": True,
                "pipeline_budget": {**budget, **timing_fields(), "outcome": "cancelled"},
                "quality_status": AnalysisQualityStatus.REVIEW_REQUIRED.value,
                "analysis_conclusion": AnalysisConclusion.ANALYSIS_ERROR.value,
            })
            await persist_analysis_job_snapshot(runtime.read_json_file(job_dir / "status.json", default={}), include_results=True)
            if not cancel_path.exists():
                raise


async def _run_pipeline_inner(job_dir: Path) -> None:
    """Pipeline body isolated for timeout wrapping.

    这里再绑一次 job_id 上下文：`_run_pipeline` 已经绑过，但测试与 worker 恢复
    路径会直接调用本函数，重复绑定是幂等的（同名同值），能保证任何入口都有 job_id。
    """
    with log_context(job_id=job_dir.name):
        await _run_pipeline_body(job_dir)


async def _run_pipeline_body(job_dir: Path, *, candidate_only: bool = False) -> None:
    """真正的流水线主体。"""
    from copy import deepcopy
    from src.services.analysis_fact_snapshot import make_snapshot, write_snapshot
    from src.services.analysis_result_store import persist_analysis_job_snapshot as commit_result
    async def persist_analysis_job_snapshot(payload, *, include_results=False):
        if not candidate_only:
            return await commit_result(payload,include_results=include_results)
        if include_results:
            if not payload.get('analysis_revision')or not (payload.get('structured_ingest')or{}).get('fact_snapshot'):
                return False
            from src.services.historical_rebuild import stage_candidate_result
            return await stage_candidate_result(payload)
        return True
    analysis_revision = "rev-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    raw_fact_context_obj = None
    # 提前初始化 provider_stats，确保处理中/失败态也能返回该字段
    provider_stats: List[Dict[str, Any]] = []
    structured_ingest_summary: Dict[str, Any] = {}
    analysis_quality_status = "complete"
    # 解析前先给出"零覆盖"默认值：若在解析阶段就失败，错误态也能诚实报出覆盖率 0
    page_assessment: Dict[str, Any] = _assess_page_extraction([], [])
    try:
        # 读取检测模式配置
        expected_fact_revision = None
        if (os.getenv('DATABASE_URL') or '').strip():
            from src.services.analysis_fact_snapshot import current_revision
            expected_fact_revision = await current_revision(job_dir.name)
        status_file = job_dir / "status.json"
        use_local_rules = True
        # ZC-00：默认关闭 AI 分析——上传链路未显式请求 AI 时不得调用模型服务。
        use_ai_assist = False
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
                # ZC-00：缺省视为不请求 AI，旧任务显式写 true 才会请求
                use_ai_assist = status_data.get("use_ai_assist", False)
                mode = status_data.get("mode", "legacy")
                fiscal_year = runtime.parse_report_year(status_data.get("fiscal_year"))
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
            # ZC-01：线程池路径同样在一次 pdfplumber.open 内产出统一解析对象
            p_texts = []
            p_tables = []
            f_size = pdf_path.stat().st_size
            import hashlib

            from src.services.pdf_parser import PDFParser as _CellParser

            digest = hashlib.sha256()
            with pdf_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            file_hash = digest.hexdigest()
            cell_parser = _CellParser(None)
            artifact_payload = None
            with pdfplumber.open(str(pdf_path)) as pdf:
                for p in pdf.pages:
                    p_texts.append(_extract_visible_text_from_page(p))
                    p_tables.append(_extract_tables_from_page(p))
                # ZC-01：同一句柄额外产出统一解析对象。产物失败不阻断主链
                # 解析——结构化入库会回退到独立解析（与旧行为一致）。
                try:
                    artifact = cell_parser.build_parse_artifact_from_handle(
                        pdf, file_hash=file_hash
                    )
                    artifact_payload = artifact.to_dict()
                except Exception:
                    logger.warning(
                        "unified parse artifact build failed; ingest will parse independently",
                        extra=safe_log_extra({"job_id": job_dir.name}),
                    )
            return p_texts, p_tables, f_size, artifact_payload

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
            parse_artifact_payload = parsed.get("parse_artifact")
        else:
            page_texts, page_tables, filesize, parse_artifact_payload = (
                await loop.run_in_executor(None, _sync_parse_pdf)
            )
        # 统一解析对象（ZC-01）：结构化入库消费它，不再重开 PDF 第二遍
        parse_artifact_obj = None
        if isinstance(parse_artifact_payload, dict):
            try:
                from src.schemas.parse_artifact import ParseArtifact as _ParseArtifact

                parse_artifact_obj = _ParseArtifact.from_dict(parse_artifact_payload)
            except Exception:
                logger.warning(
                    "parse artifact deserialization failed; ingest will parse independently",
                    extra=safe_log_extra({"job_id": job_dir.name}),
                )
                parse_artifact_obj = None


        # 扫描页/低文本页检测：结果先落状态供前端与后续门禁使用
        page_assessment = _assess_page_extraction(page_texts, page_tables)
        from src.services.table_reading_assessment import enrich_page_assessment
        page_assessment = enrich_page_assessment(page_assessment, parse_artifact_obj)
        # OCR（ZC-02 / SYS-10）：默认关闭；显式启用且存在扫描页时按页补识别。
        # 结果只登记进统一解析对象与 meta，原生文本层不被覆盖（冲突保留）。
        async def _ocr_progress(progress):
            current = runtime.read_json_file(job_dir / "status.json", default={})
            _safe_write(job_dir, {**current, "status": "processing", "stage": "解析PDF内容", "ocr_progress": progress, "ts": time.time()})

        ocr_summary = await maybe_ocr_scanned_pages(
            str(pdf_path), page_assessment, parse_artifact_obj,
            progress_callback=_ocr_progress, checkpoint_path=job_dir / "ocr_checkpoint.json",
        )
        if ocr_summary and ocr_summary.get('status') in {'pending','service_unavailable'}:
            # 软预算只让出执行槽位；未调度区域留在检查点，由持久队列续跑。
            _safe_write(job_dir, {'job_id':job_dir.name, 'status':'queued',
                'stage':'扫描页等待续跑', 'ocr_pending':ocr_summary,
                'retry_not_before':time.time()+(30 if ocr_summary.get('status')=='service_unavailable'else 5), 'quality_status':'incomplete',
                'analysis_conclusion':'insufficient_evidence', 'ts':time.time()})
            return
        if parse_artifact_obj is not None and parse_artifact_obj.reading_assessment and ocr_summary:
            parse_artifact_obj.reading_assessment["recovery"] = {
                key: ocr_summary.get(key) for key in ("enabled", "status", "requested_pages", "failed_pages")
            }
        if parse_artifact_obj is None and (os.getenv('DATABASE_URL')or'').strip():
            raise RuntimeError('trusted_analysis_requires_source_bound_parse_artifact: pending_retry')
        profile_page_texts = list(page_texts)
        rule_page_texts = list(page_texts)
        rule_page_tables = [list(tables) for tables in page_tables]
        from src.services.structure_view import build_structure_view

        structure_view = build_structure_view(page_texts, parse_artifact_obj, page_assessment)
        if parse_artifact_obj is not None and ocr_summary:
            for index in range(len(profile_page_texts)):
                qualified_identity_text = "\n".join(block.text for block in parse_artifact_obj.text_blocks
                    if block.page_number == index+1 and block.extraction_method == "ocr"
                    and block.confidence is not None and block.confidence >= .98)
                profile_page_texts[index] += "\n" + qualified_identity_text
        operand_profile = resolve_document_profile(filename=pdf_path.name,page_texts=profile_page_texts,
            explicit_report_kind=report_kind if any(text.strip() for text in page_texts) else None)
        identity_conflicts = {item.get("field") for item in operand_profile.conflicts}
        if not organization_name and "organization_name" not in identity_conflicts:
            organization_name = operand_profile.organization_name.value
            if not organization_name and parse_artifact_obj is not None:
                from src.services.ocr_qualification import qualified_ocr_cover_owner
                organization_name = qualified_ocr_cover_owner(parse_artifact_obj)
        if not (fiscal_year or report_year) and not {"fiscal_year","report_year"} & identity_conflicts:
            report_year = operand_profile.year
        if operand_profile.kind in {"budget","final"}:
            report_kind = operand_profile.kind
        # 检查主体与归档关联分开：关联到市/区级上级节点、封面是其下属部门时，
        # 规则以封面单位为本单位；organization_name 仍是归档关联，入库不变。
        from src.services.check_subject import resolve_check_subject

        check_subject = resolve_check_subject(
            organization_id=organization_id,
            organization_name=organization_name,
            organization_level=runtime.resolve_organization_level(organization_id),
            cover_name=operand_profile.organization_name.value,
            cover_conflicted="organization_name" in identity_conflicts,
            subject_level=operand_profile.subject_level.value,
        )
        check_subject_name = check_subject["name"]

        # ZC-02（AC-07）：OCR 候选表经列身份/关键数字/置信度复验后晋升为
        # 可检查事实——与原生表共用同一事实物化路径；未通过者保持候选
        # （usable_for_rules=false），继续待人工复核。
        ocr_promotion_summary = None
        if (
            parse_artifact_obj is not None
            and isinstance(ocr_summary, dict)
            and (ocr_summary.get("table_candidates") or 0) > 0
        ):
            try:
                from src.services.ocr_fact_promotion import promote_ocr_candidates

                ocr_promotion_summary = promote_ocr_candidates(
                    parse_artifact_obj, file_hash=parse_artifact_obj.source_file_hash,
                    organization=check_subject_name, year=fiscal_year or report_year,
                    report_kind=report_kind,
                )
                ocr_summary["promotion"] = ocr_promotion_summary
                if ocr_promotion_summary.get("usable_for_rules"):
                    ocr_summary["usable_for_rules"] = True
                    for fragment in parse_artifact_obj.table_fragments:
                        if fragment.extraction_method != "ocr" or fragment.confidence < .98:
                            continue
                        index = fragment.page_number-1
                        if 0 <= index < len(page_texts) and not page_texts[index].strip():
                            rule_page_tables[index].append(fragment.rows)
                            rule_page_texts[index] = profile_page_texts[index]
            except Exception as exc:
                logger.warning(
                    "OCR candidate promotion failed; candidates stay review-only",
                    extra=safe_log_extra({"job_id": job_dir.name, "error": str(exc)}),
                )
        page_assessment = enrich_page_assessment(page_assessment, parse_artifact_obj)
        # ZC-01（AC-01）：规则执行前生成统一财政事实。与结构化入库共用同一
        # 物化实现（fact_context），规则、AI、证据、入库消费同一版本；
        # 不依赖数据库——无库环境同样完成事实审校。
        fact_context_obj = None
        fact_context_status = "not_built"
        if parse_artifact_obj is not None:
            try:
                from src.engine.fact_context import build_fact_context_from_artifact

                fact_context_obj = build_fact_context_from_artifact(
                    parse_artifact_obj,
                    org_name=check_subject_name,
                    fiscal_year=fiscal_year or report_year,
                    report_kind=report_kind,
                    doc_type=doc_type,
                    require_complete_identity=True,
                )
                fact_context_status = (
                    "built" if fact_context_obj is not None else "no_facts"
                )
                if ocr_promotion_summary is not None and fact_context_obj is not None:
                    from src.engine.fact_context import OPERAND_STATUSES
                    ocr_promotion_summary["operand_count"] = sum(
                        fact.recognition_status in OPERAND_STATUSES and
                        bool(fact.extra_dims.get("ocr_provenance")) for fact in fact_context_obj.facts)
                    ocr_promotion_summary["usable_for_rules"] = ocr_promotion_summary["operand_count"] > 0
                    ocr_summary["usable_for_rules"] = ocr_promotion_summary["usable_for_rules"]
            except Exception as exc:
                logger.warning(
                    "fact context build failed; rules will run on legacy document",
                    extra=safe_log_extra({"job_id": job_dir.name, "error": str(exc)}),
                )
                fact_context_status = "build_failed"
        if parse_artifact_obj is not None:
            from src.services.table_import_receipt import build_table_import_receipt
            page_assessment["table_reading"] = build_table_import_receipt(
                parse_artifact_obj, fact_context_obj.facts if fact_context_obj else [],
                org_name=check_subject_name,
            )["reading_quality"]
        # ZC-05（AC-10）：同任务的人工校正覆盖层（重分析场景）在此应用到
        # 事实——校正不再只是留痕，重跑后规则消费的是校正后的事实版本。
        # RC-10：目标 UID 失效时先做跨解析身份重映射；身份字段类校正记为
        # "未生效"并转待复核——不能静默丢弃，也不能用未校正事实冒充成功。
        raw_fact_context_obj = deepcopy(fact_context_obj)
        fact_overlay_applied = 0
        fact_overlay_report: Dict[str, Any] = {"status": "not_requested"}
        if fact_context_obj is not None or parse_artifact_obj is not None:
            try:
                from src.engine.fact_context import (
                    apply_table_column_mapping_overlays,
                    apply_fact_overlays_with_remap,
                    collect_unsupported_changes,
                    parse_overlays_from_records,
                    build_fact_context_from_artifact,
                )
                from src.services.correction_overlay_store import read_jsonl_records

                overlay_records = read_jsonl_records(job_dir / "corrections.jsonl")
                # 人工补录格先写回表格，再应用人工列映射；两者都改变解析输入，事实须整体重建。
                from src.services.manual_cell_overlay import apply_table_cell_overlays
                cell_value_report = apply_table_cell_overlays(parse_artifact_obj, overlay_records)
                column_mapping_report = apply_table_column_mapping_overlays(
                    parse_artifact_obj, overlay_records
                )
                if column_mapping_report.get("applied") or cell_value_report.get("applied"):
                    from src.services.analysis_fact_snapshot import bind_raw_interpretation
                    bind_raw_interpretation(parse_artifact_obj, raw_fact_context_obj, {
                        **column_mapping_report,
                        "applied": column_mapping_report.get("applied", 0) + cell_value_report.get("applied", 0),
                        "cell_values": cell_value_report,
                    })
                    fact_context_obj = build_fact_context_from_artifact(
                        parse_artifact_obj,
                        org_name=check_subject_name,
                        fiscal_year=fiscal_year or report_year,
                        report_kind=report_kind,
                        doc_type=doc_type,
                        require_complete_identity=True,
                    )
                    page_assessment["table_reading"] = build_table_import_receipt(
                        parse_artifact_obj, fact_context_obj.facts if fact_context_obj else [],
                        org_name=check_subject_name,
                    )["reading_quality"]
                    if fact_context_obj is None:
                        column_mapping_report.setdefault("unresolved", []).append({
                            "reason": "应用列映射后仍未生成财政事实",
                        })
                has_cell_value_changes = any(
                    isinstance(change, dict) and str(change.get("field") or "") == "cell_value"
                    for record in overlay_records
                    for change in (record.get("changes") if isinstance(record, dict) and isinstance(record.get("changes"), list) else [])
                )
                has_column_mapping_changes = any(
                    isinstance(change, dict)
                    and str(change.get("field") or "") == "column_mapping"
                    for record in overlay_records
                    for change in (record.get("changes") if isinstance(record, dict) and isinstance(record.get("changes"), list) else [])
                )
                overlays = parse_overlays_from_records(overlay_records)
                unsupported_changes = collect_unsupported_changes(overlay_records)
                previous_facts = runtime.read_previous_fact_facts(job_dir)
                if fact_context_obj is not None and (overlays or unsupported_changes or has_column_mapping_changes
                                                     or has_cell_value_changes):
                    (
                        fact_context_obj,
                        fact_overlay_report,
                    ) = apply_fact_overlays_with_remap(
                        fact_context_obj,
                        overlays,
                        previous_facts=previous_facts,
                    )
                    fact_overlay_applied = int(
                        fact_overlay_report.get("applied") or 0
                    )
                elif overlays and fact_context_obj is None:
                    fact_overlay_report = {
                        "status": "failed",
                        "applied": 0,
                        "unresolved": [{"reason": "校正目标无法应用：当前解析未生成事实"}],
                    }
                if unsupported_changes:
                    fact_overlay_report["unsupported"] = unsupported_changes
                if has_column_mapping_changes:
                    fact_overlay_report["column_mapping_overlay"] = column_mapping_report
                    if column_mapping_report.get("unresolved"):
                        fact_overlay_report["status"] = (
                            "partial" if fact_overlay_applied or column_mapping_report.get("applied") else "failed"
                        )
                    elif column_mapping_report.get("applied") and fact_overlay_report.get("status") == "not_requested":
                        fact_overlay_report["status"] = "applied"
                if has_cell_value_changes:
                    fact_overlay_report["cell_value_overlay"] = cell_value_report
                    if cell_value_report.get("unresolved"):
                        fact_overlay_report.setdefault("unresolved", []).extend(
                            {**item, "reason": f"补录未生效：{item['reason']}"} for item in cell_value_report["unresolved"])
                        fact_overlay_report["status"] = "partial" if (
                            fact_overlay_applied or cell_value_report.get("applied") or column_mapping_report.get("applied")
                        ) else "failed"
                    elif cell_value_report.get("applied") and fact_overlay_report.get("status") == "not_requested":
                        fact_overlay_report["status"] = "applied"
                if unsupported_changes and any(item.get("blocking") for item in unsupported_changes):
                    fact_overlay_report["status"] = (
                        "partial" if fact_overlay_applied else "failed"
                    )
            except ValueError as exc:
                # 校正指向未知事实且无法重映射：重分析不能带着失效覆盖层
                # 继续装成功——如实转待复核，由人工确认校正目标。
                fact_overlay_applied = -1
                fact_overlay_report = {
                    "status": "failed",
                    "applied": 0,
                    "unresolved": [{"reason": str(exc)}],
                }
                logger.warning(
                    "fact overlay rejected: %s", exc,
                    extra=safe_log_extra({"job_id": job_dir.name}),
                )
                fact_context_status = "overlay_rejected_unknown_fact"
            except Exception as exc:
                fact_overlay_report = {"status": "failed", "applied": 0}
                logger.warning(
                    "fact overlay application failed",
                    extra=safe_log_extra({"job_id": job_dir.name, "error": str(exc)}),
                )
        # RC-10：事实快照（含已应用覆盖层）落盘——下一轮重分析据此做跨
        # 解析重映射；随分析版本一起归档，保证"该版本当时的事实"可回放。
        runtime.write_fact_context_snapshot(job_dir, fact_context_obj)
        if parse_artifact_obj is not None:
            immutable_fact_snapshot = make_snapshot(job_uuid=job_dir.name,
                revision_id=analysis_revision, artifact=parse_artifact_obj,
                raw_context=raw_fact_context_obj, effective_context=fact_context_obj)
            write_snapshot(job_dir / "fact_snapshots" / (analysis_revision + ".json"), immutable_fact_snapshot)
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

        # 报告画像：后续规则执行、AI、入库与导出统一消费这一个画像。
        # 此前文种在 8 处各判一次且兜底口径互相矛盾，同一 PDF 在不同环节
        # 可能被换成不同文种的检查配置，而结果里看不到任何异常。
        # 这里把已识别的文种作为显式候选传入（来源优先级最高），
        # 与画像判定不一致时由画像的 conflicts 留痕，不做静默覆盖。
        document_profile = resolve_document_profile(
            explicit_report_kind=report_kind,
            doc_type=str(doc_type) if doc_type is not None else None,
            filename=pdf_path.name,
            page_texts=profile_page_texts,
            page_assessment=page_assessment,
        )
        if document_profile.kind in {"budget", "final"}:
            report_kind = document_profile.kind

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
                "report_kind": report_kind,
                "profile_status": document_profile.profile_status,
            },
        )

        def _sync_build_document():
            document = build_document(
                path=str(pdf_path),
                page_texts=rule_page_texts,
                page_tables=rule_page_tables,
                filesize=filesize,
            )
            # ZC-01（AC-01）：统一事实挂上 Document——规则消费与入库同一版本
            document.fact_context = fact_context_obj
            document.parse_artifact = parse_artifact_obj
            document.document_profile = document_profile
            document.structure_view = structure_view
            return document

        doc = await loop.run_in_executor(None, _sync_build_document)

        fact_receipts: List[Dict[str, Any]] = []
        structured_consumption: List[Dict[str, Any]] = []
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
                fact_context=fact_context_obj.to_dict() if fact_context_obj is not None else None,
                parse_artifact=parse_artifact_obj.to_dict() if parse_artifact_obj is not None else None,
                meta={
                    "started_at": started,
                    "report_kind": report_kind,
                    "report_year": report_year,
                    "fiscal_year": fiscal_year,
                    "doc_type": doc_type,
                    "organization_id": organization_id,
                    "organization_name": organization_name,
                    "document_profile": document_profile.model_dump(),
                    "structure_view": structure_view,
                },
            )

            analysis_config = AnalysisConfig(
                dual_mode=True,
                ai_enabled=use_ai_assist,
                ai_profile_id=status_data.get("ai_profile_id"),
                rule_enabled=use_local_rules,
                merge_enabled=True,
                enable_ai_analysis=use_ai_assist,
                enable_ai_locator=use_ai_assist,
                ai_fallback_on_error=True,
            )

            # 执行双模式分析
            dual_result = await dual_analyzer.analyze(job_context, analysis_config)
            fact_receipts = list(dual_result.meta.get("fact_receipts") or [])
            structured_consumption = list(dual_result.meta.get("structured_consumption") or [])
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
            from src.services.ai_execution import summarize_ai_diagnostics
            ai_diagnostics = summarize_ai_diagnostics(dual_result.meta)
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
                    **ai_diagnostics,
                    "ai_configuration": status_data.get("ai_configuration"),
                    "rule_execution_summary": dual_result.meta.get(
                        "rule_execution_summary"
                    )
                    or {},
                    "parser_quality": _assess_parser_quality(
                        page_assessment, None
                    ),
                    # ZC-01（AC-01）：统一事实版本留痕——事实构建状态、
                    # 版本令牌与校正覆盖应用数如实上报。
                    "fact_context": (
                        fact_context_obj.summary()
                        if fact_context_obj is not None
                        else {"status": fact_context_status}
                    ),
                    "fact_overlay_applied": fact_overlay_applied,
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

            # ZC-03（AC-04）：规则写下的关系级子检查回执进入结果元数据，
            # 供 build_check_receipts 与质量门消费。
            fact_receipts = []
            # 使用线程池为规则检查设置超时，避免在95%阶段长时间卡住
            provider_stats = []
            try:
                RULES_TIMEOUT_SEC = int(os.getenv("RULES_TIMEOUT_SEC", "150"))
            except Exception:
                RULES_TIMEOUT_SEC = 150

            try:
                from src.services.check_progress import update_progress
                update_progress(job_dir.name, "local", state="running", current_step="正在执行本地规则检查", total=None)
                payload_issues = await run_rules_in_process(
                    doc,
                    use_ai_assist,
                    report_kind,
                    RULES_TIMEOUT_SEC,
                )
                update_progress(job_dir.name, "local", state="completed", current_step="本地规则检查完成")
                fact_receipts = list(payload_issues.get("fact_receipts") or [])
                structured_consumption = list(payload_issues.get("structured_consumption") or [])
                # MR-3（2026-09-27）：文档级可读性闸门——扫描件/低覆盖材料的
                # error 级假阳性整体转人工，防止"扫描件静默漏检却报高危矛盾"
                apply_readability_gate(payload_issues, page_assessment)
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
                    "fact_context": (
                        fact_context_obj.summary()
                        if fact_context_obj is not None
                        else {"status": fact_context_status}
                    ),
                    "fact_overlay_applied": fact_overlay_applied,
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

        # 画像随元数据一起交给结构化入库：槽位归属要靠它识别文种冲突、
        # 任务年度与材料实际年度不一致、以及材料口径。只传最终选中的文种会把
        # 冲突信息丢在门口——本应停在"待人工确认"的材料会被当成已确认。
        # 传的是上面主分析链路已经算好的同一个画像，不重新解析 PDF。
        if parse_artifact_obj is not None:
            from src.services.analysis_fact_snapshot import verify_consumed_snapshot
            verify_consumed_snapshot(immutable_fact_snapshot, artifact=parse_artifact_obj,
                raw_context=raw_fact_context_obj, effective_context=fact_context_obj)
        structured_metadata = build_ingest_metadata(
            organization_id=organization_id,
            organization_name=organization_name,
            fiscal_year=fiscal_year,
            doc_type=doc_type,
            report_year=report_year,
            report_kind=report_kind,
            checksum=status_data.get("checksum")
            if "status_data" in locals() and isinstance(status_data, dict)
            else None,
            document_profile=document_profile,
            check_subject_name=check_subject_name,
        )
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
            if latest_ingest.get("is_latest") or candidate_only:
                structured_ingest_summary = await run_structured_ingest(
                    job_id=job_dir.name,
                    pdf_path=pdf_path,
                    metadata=structured_metadata,
                    parse_artifact=parse_artifact_obj,
                    raw_fact_context=raw_fact_context_obj,
                    effective_fact_context=fact_context_obj,
                    analysis_revision=analysis_revision,
                    candidate_only=candidate_only,
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
                parse_artifact=parse_artifact_obj,
                raw_fact_context=raw_fact_context_obj,
                effective_fact_context=fact_context_obj,
                analysis_revision=analysis_revision,
                candidate_only=candidate_only,
            )
        runtime.write_structured_ingest_payload(job_dir, structured_ingest_summary)
        result["meta"]["structured_ingest"] = structured_ingest_summary
        result["meta"]["fact_snapshot"] = structured_ingest_summary.get("fact_snapshot")
        if parse_artifact_obj is not None:
            from src.services.recognition_metrics import recognition_state
            result['meta']['recognition_state']=recognition_state(parse_artifact_obj,structured_ingest_summary.get('fact_snapshot'),fact_context_obj)
        if os.getenv("DATABASE_URL") and structured_ingest_summary.get("status") in {"error", "skipped"}:
            raise RuntimeError("trusted_ingest_not_committed: " + str(structured_ingest_summary.get("error") or structured_ingest_summary.get("reason")))
        if ocr_summary is not None:
            # ZC-02：OCR 摘要（页级状态、低分块计数、原生/OCR 冲突）如实落账
            result["meta"]["ocr"] = ocr_summary
        # 解析质量以结构化入库结果为准重新评估（含缺表/歧义/facts 信号）
        result["meta"]["parser_quality"] = _assess_parser_quality(
            page_assessment, structured_ingest_summary
        )

        # 证据链完整性校验（P0-07）：落库前逐条校验证据，
        # 缺证据的 AI 问题就地降级为待复核，规则问题只记录告警。
        # ZC-00（SYS-07）：同时传入真实页数与逐页文本层——证据门据 此
        # 核验原件哈希、来源身份、页码与原文引用；无法认证的条目如实降级。
        source_file_hash = (
            str(parse_artifact_obj.source_file_hash or "").strip().lower()
            if parse_artifact_obj is not None
            else ""
        )
        if not source_file_hash:
            import hashlib

            source_digest = hashlib.sha256()
            with pdf_path.open("rb") as source_handle:
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    source_digest.update(chunk)
            source_file_hash = source_digest.hexdigest()
        from src.services.rule_source_binding import (
            bind_ai_literal_sources,
            bind_rule_sources,
            verify_bound_ai_locations,
            verify_bound_rule_locations,
        )

        result["meta"]["rule_source_binding"] = bind_rule_sources(
            result, page_texts=page_texts or [], source_file_hash=source_file_hash,
            artifact=parse_artifact_obj, fact_context=fact_context_obj,
            consumption=structured_consumption,
        )
        # Binding replaces calculated summaries with the original operands.
        # Authenticate those new per-cell coordinates against the PDF before
        # evidence completeness and persisted annotation targets are computed.
        result["meta"]["rule_source_locations"] = verify_bound_rule_locations(
            result, source_pdf_path=str(pdf_path),
        )
        # AI quotes have no trusted native coordinates when emitted by the
        # model. Bind only a unique literal quote found on its claimed page,
        # then run the same PDF content/geometry gate used for rule evidence.
        # A failed/ambiguous quote remains unlocated and is downgraded below.
        result["meta"]["ai_source_binding"] = bind_ai_literal_sources(
            result, page_texts=page_texts or [], source_file_hash=source_file_hash,
        )
        result["meta"]["ai_source_locations"] = verify_bound_ai_locations(
            result, source_pdf_path=str(pdf_path),
        )
        evidence_completeness = apply_evidence_completeness(
            result,
            document_page_count=len(page_texts) if page_texts else None,
            page_texts=page_texts if page_texts else None,
            source_file_hash=source_file_hash,
            source_pdf_path=str(pdf_path),
        )
        result["meta"]["evidence_completeness"] = evidence_completeness
        from src.services.text_source_relations import add_text_source_relations
        add_text_source_relations(result, page_texts=page_texts or [], source_file_hash=source_file_hash)
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

        # 检查义务台账：把"应检查的事情"逐项落账。
        # 关键顺序：必须写在证据校验之前，这样记录的证据状态才是最终状态；
        # 也必须写在质量门之前，门禁才能据"应执行未完成"保留 review_required。
        # 台账只做判定与记账，不新增/删除任何 finding——删规则、跳过输入、
        # 把缺失当零都无法让它变绿，因为分母来自义务清单而非规则注册数。
        attach_obligation_ids(result)
        obligation_coverage = build_obligation_ledger(
            document_profile,
            report_kind=report_kind,
            rule_execution_summary=result["meta"].get("rule_execution_summary"),
            ai_execution=result["meta"].get("ai_execution"),
            ai_required=_ai_assist_required(),
            structured_ingest=structured_ingest_summary,
        )
        result["meta"]["obligation_coverage"] = obligation_coverage
        result["meta"]["fact_receipts"] = fact_receipts
        result["meta"]["structured_consumption"] = structured_consumption
        result["meta"]["fact_projection_consumers"] = sorted({row["rule_id"] for row in structured_consumption if row.get("rule_id")})
        result["meta"]["check_receipts"] = build_check_receipts(
            result,
            report_kind=report_kind,
            rule_execution_summary=result["meta"].get("rule_execution_summary"),
            obligation_coverage=obligation_coverage,
            fact_receipts=fact_receipts,
        )
        result["meta"]["rule_registry_version"] = RULE_REGISTRY_VERSION
        result["meta"]["rule_policy_version"] = PORTABLE_RULE_POLICY_VERSION
        result["meta"]["document_profile"] = document_profile.to_dict()
        result["meta"]["check_subject"] = check_subject
        result["meta"]["structure_view"] = structure_view
        logger.info(
            "job %s check coverage: applicable=%s completed=%s unresolved=%s blocking=%s",
            job_dir.name,
            obligation_coverage["applicable_total"],
            obligation_coverage["completed_total"],
            obligation_coverage["unresolved_total"],
            obligation_coverage["blocking_total"],
            extra=safe_log_extra(
                {
                    "stage": "obligation_ledger",
                    "coverage_rate": obligation_coverage["coverage_rate"],
                    "auto_completion_rate": obligation_coverage[
                        "auto_completion_rate"
                    ],
                    "obligation_applicable_total": obligation_coverage[
                        "applicable_total"
                    ],
                    "obligation_unresolved_total": obligation_coverage[
                        "unresolved_total"
                    ],
                    "obligation_blocking_total": obligation_coverage["blocking_total"],
                    "obligation_catalog_version": obligation_coverage[
                        "catalog_version"
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
            obligation_coverage=obligation_coverage,
            fact_overlay_report=fact_overlay_report,
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

        # RC-12：版本 ID 在构建统一报告**之前**分配——报告的
        # analysis_revision 绑定、版本快照目录、analysis_basis_token
        # 都指向同一个 ID；先建报告后分配会产生 revision=null 的报告。
        payload = {
            **runtime.extract_job_status_context(runtime.read_json_file(job_dir / 'status.json', default={})),
            "job_id": job_dir.name,
            "analysis_revision": analysis_revision,
            "expected_current_fact_revision_id": expected_fact_revision,
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
            "fact_overlay": fact_overlay_report,
            "quality_status": quality_gate["quality_status"],
            "analysis_conclusion": quality_gate["analysis_conclusion"],
            "review_reasons": quality_gate["review_reasons"],
            "page_coverage": page_assessment["page_coverage"],
            "scanned_page_count": page_assessment["scanned_page_count"],
            # 结论作用域与检查覆盖：页面对外必须能回答
            # "这是什么报告、哪些已检查、哪些未完成、为什么"。
            "conclusion_scope": quality_gate["conclusion_scope"],
            "check_coverage": obligation_coverage,
            "document_profile": document_profile.to_dict(),
            "profile_status": document_profile.profile_status,
            "stage": stage_text,
        }
        # ZC-05（AC-10）：仅投影与当前问题身份仍一致的版本绑定决定。跨版本
        # 继承必须同时匹配显式关系、事实ID和证据ID；缺身份或任何一项变化都
        # 使旧决定失效，防止重算后把旧问题状态贴到新问题上。
        try:
            from src.services.issue_workflow_store import project_job_issue_decisions

            payload["human_decisions"] = project_job_issue_decisions(job_dir.name, payload)
        except Exception:
            logger.warning(
                "human decisions projection skipped",
                extra=safe_log_extra({"job_id": job_dir.name}),
            )
        payload["review_report"] = build_review_report(payload)
        persisted = await persist_analysis_job_snapshot(payload, include_results=True)
        if (os.getenv("DATABASE_URL") or "").strip() and not persisted:
            raise RuntimeError("trusted_analysis_result_not_committed: pending_retry")
        if not candidate_only and (os.getenv("DATABASE_URL") or "").strip() and structured_ingest_summary.get('fact_snapshot'):
            from src.services.analysis_fact_snapshot import synchronize_published_mirror
            await synchronize_published_mirror(job_dir.name, job_dir)
        _safe_write(job_dir, payload)

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
