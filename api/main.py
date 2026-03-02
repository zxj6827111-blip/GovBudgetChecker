# api/main.py
import os

from dotenv import load_dotenv

load_dotenv()

import json
import time
import asyncio
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
from api import runtime
from api.routes import register_routes

from src.services.analyze_dual import DualModeAnalyzer
from config.settings import get_settings
from concurrent.futures import ThreadPoolExecutor

try:
    from src.security import SecurityMiddleware
except ImportError:
    SecurityMiddleware = None

import logging

logger = logging.getLogger(__name__)

# ----------------------------- 基础配置 -----------------------------
app = FastAPI(title=runtime.APP_TITLE)

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
    try:
        (job_dir / "status.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
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


async def _run_pipeline(job_dir: Path) -> None:
    """
    真正的解析管线：
    - 读取 job_dir 下的 PDF
    - 解析文本与表格，构建 Document
    - 调用 build_issues_payload 打包返回
    - 写入 status.json（result.summary / result.issues / result.meta）
    """
    # 提前初始化 provider_stats，确保处理中/失败态也能返回该字段
    provider_stats: List[Dict[str, Any]] = []
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

        if status_file.exists():
            try:
                status_data = json.loads(status_file.read_text(encoding="utf-8"))
                use_local_rules = status_data.get("use_local_rules", True)
                use_ai_assist = status_data.get("use_ai_assist", True)
                mode = status_data.get("mode", "legacy")
                fiscal_year = status_data.get("fiscal_year")
                doc_type = status_data.get("doc_type")
                report_year = runtime.parse_report_year(
                    status_data.get("report_year") or fiscal_year
                )
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

        pdf_path = _find_first_pdf(job_dir)
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
                    p_texts.append(p.extract_text() or "")
                    p_tables.append(_extract_tables_from_page(p))
            return p_texts, p_tables, f_size

        loop = asyncio.get_running_loop()
        page_texts, page_tables, filesize = await loop.run_in_executor(
            None, _sync_parse_pdf
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
            },
        )

        doc = build_document(
            path=str(pdf_path),
            page_texts=page_texts,
            page_tables=page_tables,
            filesize=filesize,
        )

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

            job_context = JobContext(
                job_id=job_dir.name,
                pdf_path=str(pdf_path),
                page_texts=page_texts,
                page_tables=page_tables,
                filesize=filesize,
                meta={"started_at": started},
            )

            # 执行双模式分析
            dual_result = await dual_analyzer.analyze(job_context)

            # 组装最终返回体（双模式结构）
            result = {
                "summary": "",
                "ai_findings": [item.dict() for item in dual_result.ai_findings],
                "rule_findings": [item.dict() for item in dual_result.rule_findings],
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

            def _run_build_issues():
                return build_issues_payload(doc, use_ai_assist, report_kind=report_kind)

            payload_issues = None
            try:
                loop = asyncio.get_running_loop()
                # Run the synchronous build task in a separate thread, properly awaited
                with ThreadPoolExecutor(max_workers=1) as ex:
                    payload_issues = await asyncio.wait_for(
                        loop.run_in_executor(ex, _run_build_issues),
                        timeout=RULES_TIMEOUT_SEC,
                    )
            except asyncio.TimeoutError:
                # 超时：返回空结果并记录回退信息
                payload_issues = {
                    "issues": {"error": [], "warn": [], "info": [], "all": []}
                }
                provider_stats.append(
                    {
                        "fell_back": True,
                        "provider_used": "ai_extractor",
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
            except Exception as e:
                # 规则执行异常：返回空结果并记录
                payload_issues = {
                    "issues": {"error": [], "warn": [], "info": [], "all": []}
                }
                provider_stats.append(
                    {
                        "fell_back": True,
                        "provider_used": "engine",
                        "error": f"rules_error:{e}",
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
                },
            }

        payload = {
            "job_id": job_dir.name,
            "status": "done",
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
            "stage": "完成",
        }
        _safe_write(job_dir, payload)

    except Exception as e:
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
            },
        )


runtime.set_pipeline_runner(_run_pipeline)
register_routes(app)
