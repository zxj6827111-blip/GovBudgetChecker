# api/main.py
import os

from dotenv import load_dotenv
load_dotenv()

import json
import time
import hashlib
import threading
import asyncio
import csv
import io
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List

import pdfplumber
import aiofiles
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, BackgroundTasks, Form, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import sys as _sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from engine.pipeline import build_document, build_issues_payload
from api.config import AppConfig

from services.analyze_dual import DualModeAnalyzer
from config.settings import get_settings

from schemas.issues import (
    AnalysisRequest, AnalysisResponse, HealthStatus, 
    DualModeResponse, JobContext, AnalysisConfig,
    create_default_config, IssueItem, MergedSummary
)
from services.ai_rule_runner import run_ai_rules_batch
from services.engine_rule_runner import run_engine_rules
from services.merge_findings import merge_findings
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

try:
    from src.db.connection import DatabaseConnection
    from src.db.migrations import run_migrations, get_migration_status
    from src.db.safe_ops import validate_schema_name
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

try:
    from src.security import (
        SecurityMiddleware,
        security_config,
        verify_api_key,
        validate_file_upload,
        sanitize_filename,
        log_request_safely,
    )
    from src.exceptions import (
        AppException,
        handle_exception,
        create_success_response,
        ValidationError,
        FileValidationError,
        FileTooLargeError,
        NotFoundError,
    )
    SECURITY_AVAILABLE = True
except ImportError:
    SECURITY_AVAILABLE = False
    security_config = None
    async def verify_api_key(request: Request = None):
        return "anonymous"

try:
    from services.org_storage import get_org_storage
    from schemas.organization import Organization, OrganizationLevel
    ORG_AVAILABLE = True
except ImportError:
    ORG_AVAILABLE = False
    get_org_storage = None
    Organization = None
    OrganizationLevel = None

import logging
logger = logging.getLogger(__name__)

# ----------------------------- 基础配置 -----------------------------
APP_TITLE = "GovBudgetChecker API"
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "30"))
UPLOAD_ROOT = Path(os.getenv("UPLOAD_DIR", "uploads")).resolve()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title=APP_TITLE)
config = AppConfig.load()

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

if SECURITY_AVAILABLE and security_config and security_config.enabled:
    app.add_middleware(SecurityMiddleware, config=security_config)
    logger.info("Security middleware enabled with rate limiting")

async def safe_set_search_path(conn, schema: str) -> None:
    if DB_AVAILABLE:
        if not validate_schema_name(schema):
            raise ValueError(f"Invalid schema name: {schema}")
    await conn.execute(f'SET search_path TO "{schema}", public')

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


def _to_dict(obj: Any) -> Dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return dict(obj)


def _read_json_file(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if default is None:
        default = {}
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read JSON file: %s", path)
    return default


def _collect_job_summary(job_dir: Path) -> Dict[str, Any]:
    status_file = job_dir / "status.json"
    status_data = _read_json_file(status_file, default={})

    filename = ""
    try:
        filename = _find_first_pdf(job_dir).name
    except Exception:
        pass

    progress = status_data.get("progress", 0)
    status = status_data.get("status", "unknown")
    stage = status_data.get("stage")
    ts = status_data.get("ts")
    if ts is None:
        try:
            ts = job_dir.stat().st_mtime
        except Exception:
            ts = time.time()

    return {
        "job_id": job_dir.name,
        "filename": filename,
        "status": status,
        "progress": progress,
        "ts": ts,
        "mode": status_data.get("mode", "legacy"),
        "stage": stage,
    }


def _iter_job_dirs() -> List[Path]:
    if not UPLOAD_ROOT.exists():
        return []
    return [p for p in UPLOAD_ROOT.iterdir() if p.is_dir()]


def _extract_tables_from_page(page) -> List[List[List[str]]]:
    """
    读取单页表格，返回：该页的多张表；每张表是 2D 数组（行→列）
    （和引擎里的逻辑一致，先用线策略，再退回默认）
    """
    tables: List[List[List[str]]] = []
    try:
        t1 = page.extract_tables(table_settings={
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 3,
            "min_words_vertical": 1,
            "min_words_horizontal": 1,
        }) or []
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
        norm_tables.append([[("" if c is None else str(c)).strip() for c in row] for row in (tb or [])])
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
        
        if status_file.exists():
            try:
                status_data = json.loads(status_file.read_text(encoding="utf-8"))
                use_local_rules = status_data.get("use_local_rules", True)
                use_ai_assist = status_data.get("use_ai_assist", True)
                mode = status_data.get("mode", "legacy")
            except:
                pass
        
        # 检查是否启用双模式
        dual_mode_enabled = settings.get("dual_mode.enabled", False) or mode == "dual"
        
        # 标记 processing
        _safe_write(job_dir, {
            "job_id": job_dir.name,
            "status": "processing",
            "progress": 5,
            "ts": time.time(),
            "use_local_rules": use_local_rules,
            "use_ai_assist": use_ai_assist,
            "mode": mode,
            "dual_mode_enabled": dual_mode_enabled,
            "stage": "开始解析文档"
        })

        pdf_path = _find_first_pdf(job_dir)
        started = time.time()

        # 读取 PDF -> 文本/表格
        _safe_write(job_dir, {
            "job_id": job_dir.name,
            "status": "processing",
            "progress": 15,
            "ts": time.time(),
            "use_local_rules": use_local_rules,
            "use_ai_assist": use_ai_assist,
            "mode": mode,
            "dual_mode_enabled": dual_mode_enabled,
            "stage": "解析PDF内容"
        })
        
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
        page_texts, page_tables, filesize = await loop.run_in_executor(None, _sync_parse_pdf)

        # 构建 Document
        _safe_write(job_dir, {
            "job_id": job_dir.name,
            "status": "processing",
            "progress": 25,
            "ts": time.time(),
            "use_local_rules": use_local_rules,
            "use_ai_assist": use_ai_assist,
            "mode": mode,
            "dual_mode_enabled": dual_mode_enabled,
            "stage": "构建文档对象"
        })
        
        doc = build_document(
            path=str(pdf_path),
            page_texts=page_texts,
            page_tables=page_tables,
            filesize=filesize
        )

        # 双模式分析
        if dual_mode_enabled:
            _safe_write(job_dir, {
                "job_id": job_dir.name,
                "status": "processing",
                "progress": 35,
                "ts": time.time(),
                "use_local_rules": use_local_rules,
                "use_ai_assist": use_ai_assist,
                "mode": mode,
                "dual_mode_enabled": dual_mode_enabled,
                "stage": "双模式分析"
            })
            
            # 构建JobContext
            from schemas.issues import JobContext
            job_context = JobContext(
                job_id=job_dir.name,
                pdf_path=str(pdf_path),
                page_texts=page_texts,
                page_tables=page_tables,
                filesize=filesize,
                meta={"started_at": started}
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
                    "elapsed_ms": dual_result.meta.get("elapsed_ms", {}),
                    "tokens": dual_result.meta.get("tokens", {})
                }
            }
        else:
            # 传统模式分析
            # AI辅助检测阶段
            if use_ai_assist:
                _safe_write(job_dir, {
                    "job_id": job_dir.name,
                    "status": "processing",
                    "progress": 35,
                    "ts": time.time(),
                    "use_local_rules": use_local_rules,
                    "use_ai_assist": use_ai_assist,
                    "mode": mode,
                    "dual_mode_enabled": dual_mode_enabled,
                    "stage": "AI辅助状态"
                })
                
                _safe_write(job_dir, {
                    "job_id": job_dir.name,
                    "status": "processing",
                    "progress": 50,
                    "ts": time.time(),
                    "use_local_rules": use_local_rules,
                    "use_ai_assist": use_ai_assist,
                    "mode": mode,
                    "dual_mode_enabled": dual_mode_enabled,
                    "stage": "开始抽取"
                })
                
                # 这里会调用AI抽取服务，在build_issues_payload中处理
                _safe_write(job_dir, {
                    "job_id": job_dir.name,
                    "status": "processing",
                    "progress": 80,
                    "ts": time.time(),
                    "use_local_rules": use_local_rules,
                    "use_ai_assist": use_ai_assist,
                    "mode": mode,
                    "dual_mode_enabled": dual_mode_enabled,
                    "stage": "抽取完成"
                })
                
                _safe_write(job_dir, {
                    "job_id": job_dir.name,
                    "status": "processing",
                    "progress": 90,
                    "ts": time.time(),
                    "use_local_rules": use_local_rules,
                    "use_ai_assist": use_ai_assist,
                    "mode": mode,
                    "dual_mode_enabled": dual_mode_enabled,
                    "stage": "结果转换"
                })

            # 运行规则并打包统一结构（issues: {error/warn/info/all}）
            _safe_write(job_dir, {
                "job_id": job_dir.name,
                "status": "processing",
                "progress": 95,
                "ts": time.time(),
                "use_local_rules": use_local_rules,
                "use_ai_assist": use_ai_assist,
                "mode": mode,
                "dual_mode_enabled": dual_mode_enabled,
                "stage": "执行规则检查",
                "provider_stats": provider_stats
            })
            
            # 使用线程池为规则检查设置超时，避免在95%阶段长时间卡住
            provider_stats = []
            try:
                RULES_TIMEOUT_SEC = int(os.getenv("RULES_TIMEOUT_SEC", "150"))
            except Exception:
                RULES_TIMEOUT_SEC = 150

            def _run_build_issues():
                return build_issues_payload(doc, use_ai_assist)

            payload_issues = None
            try:
                loop = asyncio.get_running_loop()
                # Run the synchronous build task in a separate thread, properly awaited
                with ThreadPoolExecutor(max_workers=1) as ex:
                    payload_issues = await asyncio.wait_for(
                        loop.run_in_executor(ex, _run_build_issues),
                        timeout=RULES_TIMEOUT_SEC
                    )
            except asyncio.TimeoutError:
                # 超时：返回空结果并记录回退信息
                payload_issues = {
                    "issues": {
                        "error": [],
                        "warn": [],
                        "info": [],
                        "all": []
                    }
                }
                provider_stats.append({
                    "fell_back": True,
                    "provider_used": "ai_extractor",
                    "error": f"rules_timeout_{RULES_TIMEOUT_SEC}s",
                    "latency_ms": RULES_TIMEOUT_SEC * 1000,
                    "timestamp": time.time()
                })
                # 及时写入处理中状态，便于前端读取 provider_stats
                _safe_write(job_dir, {
                    "job_id": job_dir.name,
                    "status": "processing",
                    "progress": 95,
                    "ts": time.time(),
                    "use_local_rules": use_local_rules,
                    "use_ai_assist": use_ai_assist,
                    "mode": mode,
                    "dual_mode_enabled": dual_mode_enabled,
                    "stage": "执行规则检查（超时回退）",
                    "provider_stats": provider_stats
                })
            except Exception as e:
                # 规则执行异常：返回空结果并记录
                    payload_issues = {
                        "issues": {
                            "error": [],
                            "warn": [],
                            "info": [],
                            "all": []
                        }
                    }
                    provider_stats.append({
                        "fell_back": True,
                        "provider_used": "engine",
                        "error": f"rules_error:{e}",
                        "timestamp": time.time()
                    })
                    # 及时写入处理中状态，便于前端读取 provider_stats
                    _safe_write(job_dir, {
                        "job_id": job_dir.name,
                        "status": "processing",
                        "progress": 95,
                        "ts": time.time(),
                        "use_local_rules": use_local_rules,
                        "use_ai_assist": use_ai_assist,
                        "mode": mode,
                        "dual_mode_enabled": dual_mode_enabled,
                        "stage": "执行规则检查（异常回退）",
                        "provider_stats": provider_stats
                    })

            # 组装最终返回体（保持你之前的契约字段）
            result = {
                "summary": "",                       # 现在没有汇总，可后续填充
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
                    "provider_stats": provider_stats
                }
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
            "stage": "完成"
        }
        _safe_write(job_dir, payload)

    except Exception as e:
        _safe_write(job_dir, {
            "job_id": job_dir.name,
            "status": "error",
            "error": str(e),
            "ts": time.time(),
            "provider_stats": provider_stats,
        })

# ----------------------------- 上传接口与兼容 API -----------------------------

def _ensure_pdf(file: UploadFile) -> bool:
    ct = (file.content_type or "").lower()
    name = (file.filename or "").lower()
    return ct in ("application/pdf", "application/x-pdf") or name.endswith(".pdf")


async def _store_upload_file(file: UploadFile) -> Dict[str, Any]:
    if SECURITY_AVAILABLE:
        safe_name = sanitize_filename(file.filename or "file.pdf")
        data = await file.read()
        is_valid, error_msg = validate_file_upload(
            filename=safe_name,
            content_type=file.content_type or "",
            content=data,
        )
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_msg)
    else:
        if not _ensure_pdf(file):
            raise HTTPException(status_code=415, detail="仅支持 PDF 文件")
        data = await file.read()
        safe_name = Path(file.filename or "file.pdf").name

    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"文件超过 {MAX_UPLOAD_MB}MB 限制")

    job_id = os.urandom(16).hex()
    job_dir = UPLOAD_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    dst = job_dir / safe_name

    async with aiofiles.open(dst, "wb") as f:
        await f.write(data)

    checksum = hashlib.sha256(data).hexdigest()
    return {
        "id": job_id,
        "job_id": job_id,
        "filename": safe_name,
        "size": len(data),
        "saved_path": str(dst.relative_to(UPLOAD_ROOT)),
        "checksum": checksum,
    }


def _get_job_status_payload(job_id: str) -> Dict[str, Any]:
    job_dir = UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id 不存在")
    status_file = job_dir / "status.json"
    if not status_file.exists():
        return {"job_id": job_id, "status": "processing", "progress": 0}
    try:
        return json.loads(status_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取任务状态失败: {e}")


async def _start_analysis(job_id: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    job_dir = UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id 不存在，请先上传文件")

    status_file = job_dir / "status.json"
    body = body or {}
    use_local_rules = bool(body.get("use_local_rules", True))
    use_ai_assist = bool(body.get("use_ai_assist", True))
    mode = str(body.get("mode", "legacy"))
    payload = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "message": "分析任务已排队",
        "use_local_rules": use_local_rules,
        "use_ai_assist": use_ai_assist,
        "mode": mode,
        "ts": time.time(),
    }
    try:
        status_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        asyncio.create_task(_run_pipeline(job_dir))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动分析失败: {e}")
    return {"job_id": job_id, "status": "started"}


def _require_org_storage():
    if not ORG_AVAILABLE:
        raise HTTPException(status_code=503, detail="organization service unavailable")
    return get_org_storage()


@app.get("/health")
@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "service": APP_TITLE, "ts": time.time()}


@app.get("/ready")
@app.get("/api/ready")
async def ready() -> Dict[str, Any]:
    checks = {
        "upload_root_exists": UPLOAD_ROOT.exists(),
        "upload_root_writable": os.access(UPLOAD_ROOT, os.W_OK),
    }
    ready_state = all(checks.values())
    return {
        "status": "ready" if ready_state else "not_ready",
        "checks": checks,
        "ts": time.time(),
    }


@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    api_key: str = Depends(verify_api_key),
):
    return await _store_upload_file(file)


@app.post("/api/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    org_unit_id: Optional[str] = Form(None),
    org_id: Optional[str] = Form(None),
    fiscal_year: Optional[str] = Form(None),
    doc_type: Optional[str] = Form(None),
    api_key: str = Depends(verify_api_key),
):
    uploaded = await _store_upload_file(file)
    selected_org = org_unit_id or org_id
    if selected_org and ORG_AVAILABLE:
        try:
            storage = _require_org_storage()
            storage.link_job(uploaded["job_id"], selected_org, match_type="manual", confidence=1.0)
        except Exception:
            logger.exception("Failed to link uploaded job to organization")

    uploaded["organization_id"] = selected_org
    uploaded["fiscal_year"] = fiscal_year
    uploaded["doc_type"] = doc_type
    return uploaded


# ================= 配置、分析、状态端点（含 /api 前缀别名） =================
@app.get("/config")
@app.get("/api/config")
async def get_config():
    ai_enabled = os.getenv("AI_ASSIST_ENABLED", "true").lower() == "true"
    ai_extractor_url = os.getenv("AI_EXTRACTOR_URL", "http://127.0.0.1:9009/ai/extract/v1")
    return {
        "ai_enabled": ai_enabled,
        "ai_assist_enabled": ai_enabled,
        "ai_extractor_url": ai_extractor_url,
        "auth_enabled": bool(security_config.enabled) if security_config else False,
    }


@app.post("/analyze/{job_id}")
@app.post("/api/analyze/{job_id}")
@app.post("/analyze2/{job_id}")
@app.post("/api/analyze2/{job_id}")
async def analyze_job(job_id: str, request: Request):
    body: Optional[Dict[str, Any]] = None
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:
        body = None
    return await _start_analysis(job_id, body)


@app.post("/api/documents/{version_id}/run")
async def run_document(version_id: str, request: Request):
    body: Optional[Dict[str, Any]] = None
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:
        body = None
    return await _start_analysis(version_id, body)


@app.get("/api/jobs")
@app.get("/jobs")
async def list_jobs():
    jobs = [_collect_job_summary(job_dir) for job_dir in _iter_job_dirs()]
    jobs.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return jobs


@app.get("/jobs/{job_id}/status")
@app.get("/api/jobs/{job_id}/status")
async def get_job_status(job_id: str):
    return _get_job_status_payload(job_id)


@app.get("/api/jobs/{job_id}")
async def get_job_detail(job_id: str):
    payload = _get_job_status_payload(job_id)
    payload.setdefault("job_id", job_id)
    try:
        payload["filename"] = _find_first_pdf(UPLOAD_ROOT / job_id).name
    except Exception:
        payload.setdefault("filename", "")
    return payload


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    job_dir = UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id 不存在")
    try:
        shutil.rmtree(job_dir)
        if ORG_AVAILABLE:
            try:
                _require_org_storage().unlink_job(job_id)
            except Exception:
                logger.exception("Failed to unlink job during delete: %s", job_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除任务失败: {e}")
    return {"success": True, "job_id": job_id}


@app.post("/api/jobs/{job_id}/associate")
async def associate_job(job_id: str, request: Request):
    body = await request.json()
    org_id = (body or {}).get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="org_id is required")
    storage = _require_org_storage()
    org = storage.get_by_id(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    link = storage.link_job(job_id, org_id, match_type="manual", confidence=1.0)
    return {"success": True, "link": _to_dict(link)}


@app.get("/api/organizations")
async def get_organizations():
    storage = _require_org_storage()
    tree = [_to_dict(node) for node in storage.get_tree()]
    return {"tree": tree, "total": len(storage.get_all())}


@app.post("/api/organizations")
async def create_organization(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid request body")
    name = str(body.get("name") or "").strip()
    level = str(body.get("level") or "unit").strip()
    parent_id = body.get("parent_id")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    if Organization is None:
        raise HTTPException(status_code=503, detail="organization schema unavailable")
    org_id = body.get("id") or Organization.generate_id(name, level, parent_id)
    try:
        org = Organization(
            id=org_id,
            name=name,
            level=level,
            parent_id=parent_id,
            code=body.get("code"),
            keywords=body.get("keywords") or [name],
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid organization payload: {e}")
    storage = _require_org_storage()
    created = storage.add(org)
    return _to_dict(created)


@app.get("/api/organizations/list")
async def get_organizations_list():
    storage = _require_org_storage()
    organizations = []
    for org in storage.get_all():
        level_name = org.level
        if OrganizationLevel is not None:
            level_name = OrganizationLevel.get_display_name(org.level)
        organizations.append({
            "id": org.id,
            "name": org.name,
            "level": org.level,
            "level_name": level_name,
            "parent_id": org.parent_id,
        })
    return {"organizations": organizations, "total": len(organizations)}


@app.post("/api/organizations/import")
async def import_organizations(
    file: UploadFile = File(...),
    clear_existing: bool = Form(False),
):
    storage = _require_org_storage()
    filename = (file.filename or "").lower()
    raw = await file.read()

    rows: List[Dict[str, Any]] = []
    if filename.endswith(".csv"):
        text = raw.decode("utf-8-sig", errors="ignore")
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(row) for row in reader if row]
    elif filename.endswith(".xlsx"):
        try:
            import openpyxl
        except Exception:
            raise HTTPException(status_code=400, detail="xlsx import requires openpyxl")

        workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True)
        worksheet = workbook.active
        iterator = worksheet.iter_rows(values_only=True)
        headers_row = next(iterator, None)
        if headers_row is None:
            raise HTTPException(status_code=400, detail="empty xlsx")
        headers = [str(x).strip() if x is not None else "" for x in headers_row]
        for row in iterator:
            item: Dict[str, Any] = {}
            for idx, header in enumerate(headers):
                if not header:
                    continue
                value = row[idx] if idx < len(row) else None
                if value is not None:
                    item[header] = str(value).strip()
            if any(str(v).strip() for v in item.values()):
                rows.append(item)
    else:
        raise HTTPException(status_code=400, detail="only .csv/.xlsx are supported")

    result = storage.import_from_list(rows, clear_existing=clear_existing)
    return _to_dict(result)


@app.get("/api/organizations/{org_id}/jobs")
async def get_organization_jobs(org_id: str):
    storage = _require_org_storage()
    org = storage.get_by_id(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")

    jobs: List[Dict[str, Any]] = []
    for job_id in storage.get_org_jobs(org_id, include_children=True):
        job_dir = UPLOAD_ROOT / job_id
        if job_dir.exists():
            jobs.append(_collect_job_summary(job_dir))
        else:
            jobs.append({
                "job_id": job_id,
                "filename": "",
                "status": "unknown",
                "progress": 0,
                "ts": time.time(),
                "mode": "legacy",
            })
    jobs.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return {"jobs": jobs, "total": len(jobs)}


@app.get("/api/files/{job_id}/source")
async def get_source_pdf(job_id: str):
    job_dir = UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id 不存在")
    try:
        pdf_path = _find_first_pdf(job_dir)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="source pdf not found")
    return FileResponse(str(pdf_path), media_type="application/pdf", filename=pdf_path.name)
