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
from src.utils.provenance import summarize_finding_versions
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

try:
    from src.security import SecurityMiddleware
except ImportError:
    SecurityMiddleware = None

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


# ----------------------------- 工具函数 -----------------------------
def _safe_write(job_dir: Path, payload: Dict[str, Any]) -> None:
    """将状态写入 status.json（带异常保护）"""
    status_file = job_dir / "status.json"
    try:
        merged_payload = dict(payload)
        existing = runtime.read_json_file(status_file, default={})
        for key, value in runtime.extract_job_status_context(existing).items():
            merged_payload.setdefault(key, value)
        runtime.write_json_file(status_file, merged_payload)
    except Exception as e:
        (job_dir / "status_error.log").write_text(str(e), encoding="utf-8")


def _find_first_pdf(job_dir: Path) -> Path:
    pdfs = sorted(job_dir.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError("未在该 job 目录下找到 PDF 文件")
    return pdfs[0]


def _extract_tables_from_page(page) -> List[List[List[str]]]:
    """
    读取单页表格，返回：该页的多张表；每张表是 2D 数组（行→列）
    （和引擎里的逻辑一致，先用线策略，再退回默认）
    """
    tables: List[List[List[str]]] = []
    try:
        t1 = (
            page.extract_tables(
                table_settings={
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "intersection_tolerance": 3,
                    "min_words_vertical": 1,
                    "min_words_horizontal": 1,
                }
            )
            or []
        )
        tables += t1
    except Exception:
        pass
    try:
        if not tables:
            t2 = page.extract_tables() or []
            tables += t2
    except Exception:
        pass

    norm_tables: List[List[List[str]]] = []
    for tb in tables:
        norm_tables.append(
            [[("" if c is None else str(c)).strip() for c in row] for row in (tb or [])]
        )
    return norm_tables


def _is_visible_char(obj: Dict[str, Any], page_height: float) -> bool:
    if obj.get("object_type") != "char":
        return True
    top = obj.get("top")
    bottom = obj.get("bottom")
    if top is None or bottom is None:
        return True
    try:
        top_v = float(top)
        bottom_v = float(bottom)
    except Exception:
        return True
    return top_v >= 0 and bottom_v <= page_height


def _extract_visible_text_from_page(page) -> str:
    raw_text = page.extract_text() or ""
    try:
        page_height = float(page.height)
        filtered_page = page.filter(
            lambda obj, h=page_height: _is_visible_char(obj, h)
        )
        filtered_text = filtered_page.extract_text() or ""
        if filtered_text.strip():
            return filtered_text
    except Exception:
        pass
    return raw_text


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
    """统计结果里的问题条数，兼容传统分桶结构与双模式结构。"""
    if not isinstance(result, dict):
        return 0
    issues = result.get("issues")
    if isinstance(issues, dict):
        all_items = issues.get("all")
        if isinstance(all_items, list):
            return len(all_items)
        return sum(
            len(issues[key])
            for key in ("error", "warn", "info")
            if isinstance(issues.get(key), list)
        )
    if isinstance(issues, list):
        return len(issues)
    total = 0
    for key in ("rule_findings", "ai_findings"):
        items = result.get(key)
        if isinstance(items, list):
            total += len(items)
    return total


def _evaluate_quality_gate(
    page_assessment: Dict[str, Any],
    report_kind: Any,
    report_year: Any,
    ai_requested: bool,
    ai_degraded: bool,
    issue_total: int,
) -> Dict[str, Any]:
    """任务级质量门禁：决定终态是 done / degraded / review_required。

    设计意图是把"分析跑完了"和"结论可信"分开：只有门禁全过才允许出 done，
    否则一律转 review_required + incomplete，避免"扫描件静默漏检却显示审核通过"。

    触发人工复核的条件（任一命中即转）：
      1. 页面文本覆盖率低于阈值；
      2. 存在疑似扫描页（本轮不做 OCR，必然漏检）；
      3. 报告类型无法识别（unknown 只跑了通用规则，专项规则未覆盖）；
      4. 年度无法识别（同比/口径类判断失去基准）；
      5. AI 被配置为必需能力却失败。

    `degraded` 保留原语义："部分能力降级但结论仍然有效"。
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

    try:
        await asyncio.wait_for(
            _run_pipeline_inner(job_dir),
            timeout=PIPELINE_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
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
    """Pipeline body isolated for timeout wrapping."""
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

        # 任务级质量门禁：done 只在门禁全过时出现，否则转 review_required
        quality_gate = _evaluate_quality_gate(
            page_assessment=page_assessment,
            report_kind=report_kind,
            report_year=report_year,
            ai_requested=bool(use_ai_assist),
            ai_degraded=analysis_quality_status == "degraded",
            issue_total=_count_result_findings(result),
        )
        result["meta"]["quality_gate"] = quality_gate
        final_status = quality_gate["status"]
        if final_status == JobStatus.REVIEW_REQUIRED.value:
            stage_text = "完成（需人工复核）"
            logger.warning(
                "job %s gated to review_required: %s",
                job_dir.name,
                [reason["code"] for reason in quality_gate["review_reasons"]],
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

