# api/main.py
import os

from dotenv import load_dotenv

load_dotenv()

import json
import time
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, List

import pdfplumber
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import sys as _sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from src.engine.pipeline import build_document, build_issues_payload
from src.services.evidence_guard import (
    apply_evidence_completeness,
    count_formal_findings,
)
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
    """
    status_file = job_dir / "status.json"
    try:
        merged_payload = dict(payload)
        existing = runtime.read_json_file(status_file, default={})
        for key, value in runtime.extract_job_status_context(existing).items():
            merged_payload.setdefault(key, value)
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
) -> Dict[str, Any]:
    """任务级质量门禁：决定终态是 done / degraded / review_required。

    设计意图是把"分析跑完了"和"结论可信"分开：只有门禁全过才允许出 done，
    否则一律转 review_required + incomplete，避免"扫描件静默漏检却显示审核通过"。

    触发人工复核的条件（任一命中即转）：
      1. 页面文本覆盖率低于阈值；
      2. 存在疑似扫描页（本轮不做 OCR，必然漏检）；
      3. 报告类型无法识别（unknown 只跑了通用规则，专项规则未覆盖）；
      4. 年度无法识别（同比/口径类判断失去基准）；
      5. AI 被配置为必需能力却失败；
      6. 存在因缺证据被降级的问题项（P0-07）。

    `degraded` 保留原语义："部分能力降级但结论仍然有效"。

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
    if ai_requested and ai_degraded and _ai_assist_required():
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

        # 检查是否启用双模式
        dual_mode_enabled = settings.get("dual_mode.enabled", False) or mode == "dual"

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
            ai_requested=bool(use_ai_assist),
            ai_degraded=analysis_quality_status == "degraded",
            # 计数口径与证据校验保持一致：降级项不算正式问题
            issue_total=_count_result_findings(result),
            evidence_degraded_count=evidence_completeness["degraded_count"],
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

