"""
双模式分析编排服务
并行执行 AI 和规则检查，合并结果
"""
import logging
import asyncio
import time
import os
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path
import json
import copy
from src.services.check_progress import update_progress, finish_incomplete, progress_scope
from src.services.ai_execution import build_ai_execution
from src.services.analysis_timing import record_ai_queue_wait
from src.services.rule_process import run_engine_rules_in_process

from src.schemas.issues import JobContext, AnalysisConfig, DualModeResponse, MergedSummary, IssueItem
from src.services.ai_findings import AIFindingsService
from src.services.engine_rule_runner import EngineRuleRunner
from src.services.merge_findings import merge_findings
try:
    from src.services.ai_locator import AILocator
except Exception:
    AILocator = None

logger = logging.getLogger(__name__)

def save_snapshot(job_id: str, data: Dict[str, Any]) -> None:
    """
    保存快照到status.json，防止空快照覆盖
    要点：只有 None 才覆盖，空数组不覆盖已有非空
    """
    import os
    
    upload_root = Path(os.getenv("UPLOAD_DIR", "uploads")).resolve()
    job_dir = upload_root / job_id
    status_file = job_dir / "status.json"
    
    # 读取现有状态
    existing_data = {}
    if status_file.exists():
        try:
            existing_data = json.loads(status_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"读取现有状态失败: {e}")
    
    # 处理数据，将Pydantic模型转换为字典
    processed_data = {}
    for key, value in data.items():
        if key == "ai_findings" or key == "rule_findings":
            # 将IssueItem列表转换为字典列表
            processed_data[key] = [item.model_dump() if hasattr(item, "model_dump") else item for item in value]
        elif key == "merged":
            # 将MergedSummary对象转换为字典
            processed_data[key] = value.model_dump() if hasattr(value, "model_dump") else value
        else:
            processed_data[key] = value
    
    # 合并数据，防止空快照覆盖
    merged_data = existing_data.copy()
    
    for key, value in processed_data.items():
        if key in ["ai_findings", "rule_findings", "merged"]:
            # 对于关键数据字段，只有None才覆盖，空数组不覆盖已有非空
            if value is None:
                merged_data[key] = None
            elif isinstance(value, list) and len(value) == 0:
                # 空数组：只有现有数据也为空或不存在时才覆盖
                if key not in existing_data or not existing_data[key]:
                    merged_data[key] = value
                # 否则保持现有数据不变
            else:
                # 非空数据：直接覆盖
                merged_data[key] = value
        else:
            # 其他字段（如meta）：直接覆盖
            merged_data[key] = value
    
    # 写入文件
    try:
        job_dir.mkdir(parents=True, exist_ok=True)
        status_file.write_text(
            json.dumps(merged_data, ensure_ascii=False, indent=2), 
            encoding="utf-8"
        )
        logger.debug(f"快照已保存: {job_id}")
    except Exception as e:
        logger.error(f"保存快照失败: {e}")

@dataclass
class AnalysisMetrics:
    """分析指标"""
    ai_elapsed_ms: int = 0
    rule_elapsed_ms: int = 0
    merge_elapsed_ms: int = 0
    total_elapsed_ms: int = 0
    ai_rules_count: int = 0
    engine_rules_count: int = 0
    ai_findings_count: int = 0
    rule_findings_count: int = 0
    merged_conflicts: int = 0
    merged_agreements: int = 0
    provider_stats: List[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.provider_stats is None:
            self.provider_stats = []


class DualModeAnalyzer:
    """双模式分析器"""

    # 全局 AI 串行队列：保证多任务时 AI 审计按进入顺序逐个执行
    _ai_queue_guard = asyncio.Lock()
    _ai_runner_lock = asyncio.Lock()
    _ai_wait_queue: List[str] = []
    
    def __init__(self):
        self.ai_service = None  # 延迟初始化
        self.engine_runner = EngineRuleRunner()
        self.ai_locator = None
        self.ai_sequential_enabled = (
            os.getenv("AI_SEQUENTIAL_MODE", "true").strip().lower()
            not in {"0", "false", "no", "off"}
        )
    
    async def analyze(self, job_context: JobContext, config: Optional[AnalysisConfig] = None) -> DualModeResponse:
        # 配置、调用留痕、规则回执均按任务隔离，切换模型只影响后续任务。
        config = (config or AnalysisConfig()).model_copy(deep=True)
        scoped = copy.copy(self)
        scoped.engine_runner = copy.copy(self.engine_runner)
        scoped._runtime_ai_config = None
        scoped._runtime_ai_error = None
        if config.ai_enabled:
            from src.services.ai_model_settings import resolve_job_runtime, create_client
            try:
                scoped._runtime_ai_config = resolve_job_runtime(job_context.job_id, config.ai_profile_id)
                if scoped._runtime_ai_config:
                    runtime = scoped._runtime_ai_config
                    config.ai_timeout = runtime["timeout_s"]
                    update_progress(job_context.job_id, "ai", model=runtime["model"], profile_id=runtime["id"], profile_name=runtime["name"], config_revision=runtime["revision"])
                    if config.enable_ai_locator and AILocator:
                        scoped.ai_locator = AILocator(client=create_client(runtime), model=runtime["model"], provider="managed")
                elif config.enable_ai_locator and AILocator:
                    scoped.ai_locator = AILocator()
            except ValueError as exc:
                scoped._runtime_ai_error = str(exc)
        with progress_scope(job_context.job_id):
            return await scoped._analyze(job_context, config)

    async def _analyze(self,
                     job_context: JobContext,
                     config: Optional[AnalysisConfig] = None) -> DualModeResponse:
        """
        执行双模式分析，实现真正并行处理、异常转为可见meta和超时降级机制
        
        Args:
            job_context: 作业上下文（包含文档路径、OCR 结果等）
            config: 分析配置
            
        Returns:
            DualModeResponse: 包含 AI 结果、规则结果、合并结果和元数据
        """
        if config is None:
            config = AnalysisConfig()
        
        start_time = time.time()
        metrics = AnalysisMetrics()
        
        # 记录时间戳
        ai_started_at = None
        ai_done_at = None
        rule_started_at = None
        rule_done_at = None
        
        # 按模型单次超时、文本分段和重试计算预算。
        from src.services.ai_model_settings import audit_time_budget
        TIMEOUT_SECONDS = audit_time_budget(config.ai_timeout)
        try:
            RULE_TIMEOUT_SECONDS = max(1, int(os.getenv("RULES_TIMEOUT_SEC", "150")))
        except ValueError:
            RULE_TIMEOUT_SECONDS = 150

        # AC-11：部分结果保全——管线后段（合并/定位/快照）异常时，
        # 已完成的 AI/规则结果不得被整批清空后按空结果重算。
        completed_ai_findings: List[Any] = []
        completed_rule_findings: List[Any] = []
        ai_task = None
        rule_task = None
        rule_enabled = bool(config.rule_enabled)
        rule_error = None

        try:
            # 1. 加载规则
            rules = await self._load_rules(config.rules_version)
            
            # 2. 分离 AI 规则和引擎规则
            ai_rules, engine_rules = self._separate_rules(rules)
            metrics.ai_rules_count = len(ai_rules)
            metrics.engine_rules_count = len(engine_rules)
            
            logger.info(f"Loaded {len(ai_rules)} AI rules and {len(engine_rules)} engine rules")
            
            # 3. 真正并行执行分析，使用 asyncio.gather 处理异常和超时
            ai_started_at = time.time()
            rule_started_at = time.time()
            
            # 创建任务（并行启动）
            ai_enabled = bool(config.enable_ai_analysis and config.ai_enabled)
            rule_enabled = bool(config.rule_enabled)

            ai_task = (
                asyncio.create_task(
                    self._run_ai_analysis(
                        job_context,
                        ai_rules,
                        config,
                        timeout_seconds=TIMEOUT_SECONDS,
                    )
                )
                if ai_enabled
                else None
            )
            rule_task = (
                asyncio.create_task(self._run_engine_analysis(job_context, engine_rules, config))
                if (rule_enabled and engine_rules)
                else None
            )
            
            # ========== 增量发布策略 ==========
            # 1. 先等待规则任务完成（通常较快）
            rule_findings = []
            rule_error = None
            rule_done_at = rule_started_at
            
            if rule_task is not None:
                try:
                    rule_result = await asyncio.wait_for(rule_task, timeout=RULE_TIMEOUT_SECONDS)
                    rule_done_at = time.time()
                    if isinstance(rule_result, Exception):
                        rule_error = str(rule_result)
                        logger.error(f"Rule analysis failed: {rule_error}")
                    else:
                        rule_findings = rule_result or []
                        completed_rule_findings = list(rule_findings)
                        logger.info(f"Rule analysis completed, found {len(rule_findings)} issues")
                except asyncio.TimeoutError:
                    rule_done_at = time.time()
                    rule_error = f"规则分析超时({RULE_TIMEOUT_SECONDS}s)"
                    logger.warning(rule_error)
                except Exception as e:
                    rule_done_at = time.time()
                    rule_error = str(e)
                    logger.error(f"Rule analysis exception: {e}")
            
            metrics.rule_findings_count = len(rule_findings)
            metrics.rule_elapsed_ms = int((rule_done_at - rule_started_at) * 1000)
            # 规则执行摘要（六态）：供质量门与评测契约消费
            try:
                rule_execution_summary = self.engine_runner.get_rule_execution_summary()
            except Exception:
                rule_execution_summary = {}

            if rule_enabled and rule_task is None:
                rule_error = "未加载到可执行的本地规则"
            if rule_enabled and rule_error:
                update_progress(job_context.job_id, "local", state="failed", current_step="本地规则检查失败")
                if ai_task is not None and not ai_task.done():
                    ai_task.cancel()
                    await asyncio.gather(ai_task, return_exceptions=True)
                raise RuntimeError(f"local_rules_failed:{rule_error}")
            if rule_enabled:
                failed = (self.engine_runner.get_stats() or {}).get("failed_rules", 0)
                update_progress(job_context.job_id, "local", state="degraded" if failed else "completed", current_step="部分规则执行失败，需复核" if failed else "本地规则检查完成")
            
            # 2. 规则完成后立即发布快照（前端可先看到规则结果）
            save_snapshot(job_context.job_id, {
                "status": "processing",
                "progress": 70,
                "stage": "本地规则已完成，AI 正在审计..." if ai_task is not None else "本地规则已完成",
                "ai_findings": [],
                "rule_findings": rule_findings,
                "meta": {
                    "ai_status": "processing" if ai_task is not None else "disabled",
                    "rule_error": rule_error,
                    "rule_started_at": rule_started_at,
                    "rule_done_at": rule_done_at,
                    "last_heartbeat": time.time()
                }
            })
            logger.info("Phase 1 snapshot saved: rule findings published, waiting for AI...")
            
            # 3. 等待 AI 任务完成
            ai_findings = []
            ai_error = None
            ai_done_at = ai_started_at
            ai_call_ledger: List[Dict[str, Any]] = []

            if ai_task is not None:
                try:
                    # _run_ai_analysis waits for the global sequential-LLM
                    # queue before starting its own audit timeout. Wrapping the
                    # whole task here would charge queue latency against this
                    # document's budget and cancel late jobs in a batch.
                    ai_result = await ai_task
                    ai_done_at = time.time()
                    if isinstance(ai_result, Exception):
                        ai_error = str(ai_result)
                        logger.error(f"AI analysis failed: {ai_error}")
                        metrics.provider_stats.append({
                            'provider_used': 'unknown',
                            'model_used': 'unknown',
                            'error': ai_error,
                            'latency_ms': int((ai_done_at - ai_started_at) * 1000),
                            'timestamp': ai_done_at
                        })
                    else:
                        ai_findings = ai_result or []
                        ai_error = getattr(self, "_ai_run_error", None)
                        completed_ai_findings = list(ai_findings)
                        logger.info(f"AI analysis completed, found {len(ai_findings)} issues")
                except asyncio.TimeoutError:
                    ai_done_at = time.time()
                    ai_error = f"AI分析超时({TIMEOUT_SECONDS}s)"
                    logger.warning(ai_error)
                except Exception as e:
                    ai_done_at = time.time()
                    ai_error = str(e)
                    logger.error(f"AI analysis exception: {e}")

                # AI 调用留痕：成功也必须记录 provider/model/token，否则
                # "AI 已审计且未发现问题"与"AI 根本没跑"无法区分（P0 假完成修复）。
                if self.ai_service is not None:
                    try:
                        ai_call_ledger = self.ai_service.ai_client.pop_call_ledger()
                    except Exception:
                        ai_call_ledger = []
                    if ai_findings is not None and not ai_error and ai_call_ledger:
                        last_call = ai_call_ledger[-1]
                        metrics.provider_stats.append({
                            'provider_used': last_call.get('provider') or 'unknown',
                            'model_used': last_call.get('model') or 'unknown',
                            'error': None,
                            'latency_ms': int((ai_done_at - ai_started_at) * 1000),
                            'timestamp': ai_done_at,
                            'tokens': last_call.get('token_usage') or {},
                            'finish_reason': last_call.get('finish_reason'),
                            'calls': len(ai_call_ledger),
                        })
            
            metrics.ai_findings_count = len(ai_findings)
            if ai_enabled:
                window_errors = list(getattr(self.ai_service, "ai_errors", []) or []) if self.ai_service else []
                execution = build_ai_execution(True, ai_error=ai_error or "", call_ledger=ai_call_ledger, window_errors=window_errors)
                ai_progress_state = {"succeeded": "completed", "degraded": "degraded"}.get(execution["state"], "failed")
            metrics.ai_elapsed_ms = int((ai_done_at - ai_started_at) * 1000)
            
            # 无论任何一支失败都要落快照
            save_snapshot(job_context.job_id, {
                "ai_findings": ai_findings,
                "rule_findings": rule_findings,
                "meta": {
                    "status": "merging",
                    "stage": "合并结果",
                    "progress": 98,
                    "ai_error": ai_error,
                    "rule_error": rule_error,
                    "ai_started_at": ai_started_at,
                    "ai_done_at": ai_done_at,
                    "rule_started_at": rule_started_at,
                    "rule_done_at": rule_done_at,
                    "last_heartbeat": time.time()
                }
            })
            
            # 4. 处理 AI 定位增强
            if config.enable_ai_locator:
                update_progress(job_context.job_id, "ai", current_step="文本检查已结束，正在完成证据定位")
                rule_findings = await self._enhance_with_ai_locator(
                    job_context, rule_findings, metrics
                )
            if ai_enabled:
                update_progress(job_context.job_id, "ai", state=ai_progress_state, current_step={"failed": "大模型检查失败，请检查连接、密钥及模型响应", "degraded": "部分文本未完成检查，需复核", "completed": "大模型检查完成"}[ai_progress_state])
            
            # 5. 合并结果
            merge_start = time.time()
            merged_summary = merge_findings(
                ai_findings=ai_findings,
                rule_findings=rule_findings,
                config=config
            )
            metrics.merge_elapsed_ms = int((time.time() - merge_start) * 1000)
            
            metrics.merged_conflicts = len(merged_summary.conflicts)
            metrics.merged_agreements = len(merged_summary.agreements)
            
            # 6. 计算总耗时
            metrics.total_elapsed_ms = int((time.time() - start_time) * 1000)
            
            # 保存最终快照
            save_snapshot(job_context.job_id, {
                "ai_findings": ai_findings,
                "rule_findings": rule_findings,
                "merged": merged_summary.dict() if hasattr(merged_summary, 'dict') else merged_summary,
                "meta": {
                    "status": "done",
                    "stage": "完成",
                    "progress": 100,
                    "ai_error": ai_error,
                    "rule_error": rule_error,
                    "ai_started_at": ai_started_at,
                    "ai_done_at": ai_done_at,
                    "rule_started_at": rule_started_at,
                    "rule_done_at": rule_done_at,
                    "last_heartbeat": time.time(),
                    "provider_stats": metrics.provider_stats
                }
            })
            
            logger.info(f"Dual mode analysis completed in {metrics.total_elapsed_ms}ms")

            return DualModeResponse(
                job_id=job_context.job_id,
                ai_findings=ai_findings,
                rule_findings=rule_findings,
                merged=merged_summary,
                meta={
                    "elapsed_ms": {
                        "ai": metrics.ai_elapsed_ms,
                        "rule": metrics.rule_elapsed_ms,
                        "merge": metrics.merge_elapsed_ms,
                        "total": metrics.total_elapsed_ms
                    },
                    "counts": {
                        "ai_rules": metrics.ai_rules_count,
                        "engine_rules": metrics.engine_rules_count,
                        "ai_findings": metrics.ai_findings_count,
                        "rule_findings": metrics.rule_findings_count,
                        "conflicts": metrics.merged_conflicts,
                        "agreements": metrics.merged_agreements
                    },
                    "ai_error": ai_error,
                    "rule_error": rule_error,
                    "ai_started_at": ai_started_at,
                    "ai_done_at": ai_done_at,
                    "rule_started_at": rule_started_at,
                    "rule_done_at": rule_done_at,
                    "last_heartbeat": time.time(),
                    "provider_stats": metrics.provider_stats,
                    "ai_call_ledger": ai_call_ledger,
                    # 覆盖缺口（如审计窗口上限触发）：degraded 判定依据
                    "ai_window_errors": list(getattr(self.ai_service, "ai_errors", []) or []) if self.ai_service is not None else [],
                    "rule_execution_summary": rule_execution_summary,
                    # AC-04：关系级子检查回执与统一事实摘要随结果交付
                    "fact_receipts": (self.engine_runner.get_stats() or {}).get("fact_receipts", []) if self.engine_runner else [],
                    "structured_consumption": (self.engine_runner.get_stats() or {}).get("structured_consumption", []) if self.engine_runner else [],
                    "fact_context_summary": (self.engine_runner.get_stats() or {}).get("fact_context_summary") if self.engine_runner else None,
                    "config": config.dict()
                }
            )
            
        except asyncio.CancelledError:
            finish_incomplete(job_context.job_id, state="cancelled")
            raise
        except Exception as e:
            finish_incomplete(job_context.job_id)
            logger.error(f"Dual mode analysis failed: {e}", exc_info=True)
            
            # 保存错误快照
            save_snapshot(job_context.job_id, {
                "meta": {
                    "status": "failed",
                    "stage": "分析失败",
                    "progress": 0,
                    "error": str(e),
                    "ai_started_at": ai_started_at,
                    "ai_done_at": ai_done_at,
                    "rule_started_at": rule_started_at,
                    "rule_done_at": rule_done_at,
                    "last_heartbeat": time.time()
                }
            })

            # 本地规则是请求的必执行分支；超时/失败不能在同一任务里无界重跑，
            # 也不能用回退成功掩盖本次失败。由管线记录错误后再显式重分析。
            if rule_enabled and rule_error:
                raise
            
            # 降级：引擎结果 + 已完成的 AI 结果一并保留（AC-11 逐条失败隔离）
            try:
                if not completed_rule_findings:
                    completed_rule_findings = await self._fallback_engine_only(job_context, config)
                metrics.total_elapsed_ms = int((time.time() - start_time) * 1000)

                fallback_receipts: List[Dict[str, Any]] = []
                try:
                    stats = self.engine_runner.get_stats() if self.engine_runner else {}
                    fallback_receipts = list((stats or {}).get("fact_receipts") or [])
                except Exception:
                    fallback_receipts = []
                partial = bool(completed_ai_findings)
                return DualModeResponse(
                    job_id=job_context.job_id,
                    ai_findings=completed_ai_findings,
                    rule_findings=completed_rule_findings,
                    merged=MergedSummary(
                        totals={
                            "rule_only": len(completed_rule_findings),
                            "ai_candidates_preserved": len(completed_ai_findings),
                        },
                        conflicts=[],
                        agreements=[],
                        merged_ids=[
                            str(item.id)
                            for item in completed_rule_findings
                            if getattr(item, "id", None)
                        ],
                        candidate_issue_ids=[
                            str(item.id)
                            for item in completed_ai_findings
                            if getattr(item, "id", None)
                        ],
                        merge_policy_version="fact-check-relation-v1",
                    ),
                    meta={
                        "elapsed_ms": {"total": metrics.total_elapsed_ms},
                        "error": str(e),
                        "fallback": "engine_only_partial" if partial else "engine_only",
                        "ai_error": str(e),
                        "merge_error": str(e),
                        "ai_candidates_require_confirmation": bool(completed_ai_findings),
                        "rule_error": None,
                        "provider_stats": [],
                        "fact_receipts": fallback_receipts,
                        "structured_consumption": (self.engine_runner.get_stats() or {}).get("structured_consumption", []) if self.engine_runner else [],
                        "rule_execution_summary": self.engine_runner.get_rule_execution_summary() if self.engine_runner else {},
                    }
                )
            except Exception as fallback_error:
                logger.error(f"Fallback analysis also failed: {fallback_error}")
                raise e
        finally:
            # The pipeline may be cancelled while it is still awaiting local
            # rules. Its AI task must not outlive the parent or retain a queue
            # ticket and block later documents.
            children = [task for task in (ai_task, rule_task) if task is not None]
            for task in children:
                if not task.done():
                    task.cancel()
            if children:
                await asyncio.gather(*children, return_exceptions=True)
    
    async def _load_rules(self, rules_version: str) -> List[Dict[str, Any]]:
        """加载规则"""
        from rules.loader_ext import RuleLoaderExt
        
        loader = RuleLoaderExt()
        return await loader.load_rules_async(rules_version)
    
    def _separate_rules(self, rules: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """分离 AI 规则和引擎规则"""
        ai_rules = []
        engine_rules = []
        
        # 若规则列表为空，记录一次日志，便于诊断
        if not rules:
            logger.warning("No rules parsed from loader, engine and AI analysis will be skipped")
            return ai_rules, engine_rules
        
        for rule in rules:
            executor = rule.get('executor', 'engine')  # 默认使用引擎
            
            if executor == 'ai':
                ai_rules.append(rule)
            elif executor == 'engine':
                engine_rules.append(rule)
            elif executor in ('both', 'hybrid'):
                # 同时添加到两个列表
                ai_rules.append(rule)
                engine_rules.append(rule)
            else:
                logger.warning(f"Unknown executor '{executor}' for rule {rule.get('id', 'unknown')}, defaulting to engine")
                engine_rules.append(rule)
        
        return ai_rules, engine_rules
    
    async def _run_parallel_analysis(self, 
                                   job_context: JobContext,
                                   ai_rules: List[Dict[str, Any]],
                                   engine_rules: List[Dict[str, Any]],
                                   config: AnalysisConfig,
                                   metrics: AnalysisMetrics) -> Tuple[List[IssueItem], List[IssueItem]]:
        """真正并行执行 AI 和引擎分析，使用 asyncio.gather 处理异常"""
        
        # 记录开始时间
        ai_started_at = time.time()
        rule_started_at = time.time()
        
        # 创建任务
        tasks = []
        task_names = []
        
        # AI 分析任务
        if ai_rules and config.enable_ai_analysis:
            ai_task = asyncio.create_task(
                self._run_ai_analysis(job_context, ai_rules, config)
            )
            tasks.append(ai_task)
            task_names.append('ai')
        else:
            tasks.append(None)
            task_names.append('ai')
        
        # 引擎分析任务
        if engine_rules:
            engine_task = asyncio.create_task(
                self._run_engine_analysis(job_context, engine_rules, config)
            )
            tasks.append(engine_task)
            task_names.append('engine')
        else:
            tasks.append(None)
            task_names.append('engine')
        
        # 使用 asyncio.gather 真正并行执行，return_exceptions=True 确保异常不会中断其他任务
        results = await asyncio.gather(*[t for t in tasks if t is not None], return_exceptions=True)
        
        # 处理结果和异常
        ai_findings = []
        rule_findings = []
        
        result_idx = 0
        for i, task_name in enumerate(task_names):
            if tasks[i] is None:
                # 任务未创建，使用空结果
                if task_name == 'ai':
                    ai_findings = []
                    metrics.ai_elapsed_ms = 0
                elif task_name == 'engine':
                    rule_findings = []
                    metrics.rule_elapsed_ms = 0
                continue
            
            result = results[result_idx]
            result_idx += 1
            
            elapsed_ms = int((time.time() - (ai_started_at if task_name == 'ai' else rule_started_at)) * 1000)
            
            if isinstance(result, Exception):
                # 异常情况：记录错误但不中断流程
                error_msg = str(result)
                logger.error(f"{task_name.upper()} analysis failed: {error_msg}")
                
                # 将异常信息记录到 metrics 中
                if task_name == 'ai':
                    ai_findings = []
                    metrics.ai_elapsed_ms = elapsed_ms
                    # 记录 AI 错误到 provider_stats
                    metrics.provider_stats.append({
                        'provider_used': 'unknown',
                        'model_used': 'unknown',
                        'error': error_msg,
                        'latency_ms': elapsed_ms,
                        'timestamp': time.time()
                    })
                elif task_name == 'engine':
                    rule_findings = []
                    metrics.rule_elapsed_ms = elapsed_ms
            else:
                # 正常情况
                if task_name == 'ai':
                    ai_findings = result or []
                    metrics.ai_elapsed_ms = elapsed_ms
                elif task_name == 'engine':
                    rule_findings = result or []
                    metrics.rule_elapsed_ms = elapsed_ms
                
                logger.info(f"{task_name.upper()} analysis completed in {elapsed_ms}ms, found {len(result or [])} issues")
        
        return ai_findings, rule_findings
    
    async def _run_ai_analysis(self, 
                              job_context: JobContext,
                              ai_rules: List[Dict[str, Any]],
                              config: AnalysisConfig,
                              timeout_seconds: Optional[int] = None) -> List[IssueItem]:
        """运行 AI 分析"""
        self._ai_run_error = None
        if not ai_rules:
            self._ai_run_error = "未加载到大模型检查规则"
            update_progress(job_context.job_id, "ai", state="failed", current_step="未加载到大模型检查规则")
            return []

        queue_token = f"{job_context.job_id}:{time.time_ns()}"
        queue_pos = 1
        try:
            if self.ai_sequential_enabled:
                queue_pos = await self._enqueue_ai_ticket(queue_token)
                update_progress(job_context.job_id, "ai", state="queued", queue_position=queue_pos, current_step=f"大模型排队中（前方 {queue_pos - 1} 个任务）")
                if queue_pos > 1:
                    save_snapshot(
                        job_context.job_id,
                        {
                            "status": "processing",
                            "stage": f"AI排队中（前方{queue_pos - 1}个任务）",
                            "meta": {
                                "ai_status": "queued",
                                "ai_queue_position": queue_pos,
                                "last_heartbeat": time.time(),
                            },
                        },
                    )
                with record_ai_queue_wait():
                    await self._wait_for_ai_turn(queue_token, job_context.job_id)
                save_snapshot(
                    job_context.job_id,
                    {
                        "status": "processing",
                        "stage": "AI审计中",
                        "meta": {
                            "ai_status": "processing",
                            "ai_queue_position": 1,
                            "last_heartbeat": time.time(),
                        },
                    },
                )

            # 延迟初始化AI服务
            if getattr(self, "_runtime_ai_error", None):
                raise ValueError(self._runtime_ai_error)
            if self.ai_service is None:
                self.ai_service = AIFindingsService(config, runtime_config=getattr(self, "_runtime_ai_config", None))
            update_progress(job_context.job_id, "ai", state="running", queue_position=0, current_step="正在准备大模型检查")
            
            # 使用AI服务进行分析（串行模式下保证同一时刻仅1个AI任务）
            async def run_analysis() -> List[IssueItem]:
                if self.ai_sequential_enabled:
                    async with self._ai_runner_lock:
                        return await self.ai_service.analyze(job_context)
                return await self.ai_service.analyze(job_context)

            if timeout_seconds is None:
                return await run_analysis()
            try:
                # Begin this task's audit budget after it has acquired its
                # queue ticket; time spent behind other documents is not part
                # of this document's model-check runtime.
                return await asyncio.wait_for(run_analysis(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                update_progress(
                    job_context.job_id,
                    "ai",
                    state="failed",
                    current_step=f"大模型检查超时（{timeout_seconds}s）",
                )
                raise
        except asyncio.TimeoutError:
            # Overall audit timeouts must remain visible even when ordinary AI
            # errors are configured to fall back to local-rule-only results.
            raise
        except Exception as e:
            self._ai_run_error = str(e) or type(e).__name__
            update_progress(job_context.job_id, "ai", state="failed", current_step="大模型检查失败，请检查配置或连接")
            logger.error(f"AI analysis failed: {e}")
            if config.ai_fallback_on_error:
                return []
            else:
                raise e
        finally:
            if self.ai_sequential_enabled:
                await self._dequeue_ai_ticket(queue_token)

    async def _run_engine_analysis(self, 
                                  job_context: JobContext,
                                  engine_rules: List[Dict[str, Any]],
                                  config: AnalysisConfig) -> List[IssueItem]:
        """运行引擎分析"""
        if not config.rule_enabled:
            return []
        try:
            timeout_seconds = max(1, int(os.getenv("RULES_TIMEOUT_SEC", "150")))
        except ValueError:
            timeout_seconds = 150
        return await run_engine_rules_in_process(
            self.engine_runner, job_context, engine_rules, config, timeout_seconds,
        )

    async def _enqueue_ai_ticket(self, token: str) -> int:
        async with self._ai_queue_guard:
            self._ai_wait_queue.append(token)
            return len(self._ai_wait_queue)

    async def _wait_for_ai_turn(self, token: str, job_id: str) -> None:
        last_emit = 0.0
        while True:
            queue_pos: Optional[int] = None
            async with self._ai_queue_guard:
                if self._ai_wait_queue and self._ai_wait_queue[0] == token:
                    return
                if token not in self._ai_wait_queue:
                    return
                queue_pos = self._ai_wait_queue.index(token) + 1
            now = time.time()
            if queue_pos and queue_pos > 1 and (now - last_emit) >= 1.5:
                update_progress(job_id, "ai", queue_position=queue_pos, current_step=f"大模型排队中（前方 {queue_pos - 1} 个任务）")
                save_snapshot(
                    job_id,
                    {
                        "status": "processing",
                        "stage": f"AI排队中（前方{queue_pos - 1}个任务）",
                        "meta": {
                            "ai_status": "queued",
                            "ai_queue_position": queue_pos,
                            "last_heartbeat": now,
                        },
                    },
                )
                last_emit = now
            await asyncio.sleep(0.2)

    async def _dequeue_ai_ticket(self, token: str) -> None:
        async with self._ai_queue_guard:
            if token in self._ai_wait_queue:
                self._ai_wait_queue.remove(token)
    
    async def _enhance_with_ai_locator(self, 
                                     job_context: JobContext,
                                     rule_findings: List[IssueItem],
                                     metrics: AnalysisMetrics) -> List[IssueItem]:
        """使用 AI 定位器增强引擎结果"""
        if self.ai_locator is None:
            logger.warning("AILocator not available, skip locator enhancement")
            return rule_findings
        enhanced_findings = []
        
        for finding in rule_findings:
            # 检查是否需要 AI 定位增强
            if self._needs_ai_locator_enhancement(finding):
                try:
                    enhanced_finding = await self.ai_locator.enhance_finding(
                        job_context=job_context,
                        finding=finding
                    )
                    enhanced_findings.append(enhanced_finding)
                except Exception as e:
                    logger.warning(f"AI locator enhancement failed for finding {finding.rule_id}: {e}")
                    # 失败时使用原始结果
                    enhanced_findings.append(finding)
            else:
                enhanced_findings.append(finding)
        
        return enhanced_findings
    
    def _needs_ai_locator_enhancement(self, finding: IssueItem) -> bool:
        """判断是否需要 AI 定位增强"""
        # 检查是否有 NO_ANCHOR 或 MULTI_ANCHOR 错误
        why_not = finding.why_not or ""
        return "NO_ANCHOR" in why_not or "MULTI_ANCHOR" in why_not
    
    async def _fallback_engine_only(self, 
                                   job_context: JobContext,
                                   config: AnalysisConfig) -> List[IssueItem]:
        """降级：仅使用引擎分析"""
        logger.info("Falling back to engine-only analysis")
        
        # 加载所有规则，但只执行引擎规则
        rules = await self._load_rules(config.rules_version)
        _, engine_rules = self._separate_rules(rules)
        
        return await self._run_engine_analysis(job_context, engine_rules, config)


# 便捷函数
async def analyze_dual_mode(job_context: JobContext, 
                           config: Optional[AnalysisConfig] = None) -> DualModeResponse:
    """便捷的双模式分析函数"""
    analyzer = DualModeAnalyzer()
    return await analyzer.analyze(job_context, config)


async def analyze_engine_only(job_context: JobContext,
                             config: Optional[AnalysisConfig] = None) -> List[IssueItem]:
    """仅使用引擎分析"""
    if config is None:
        config = AnalysisConfig()
    
    analyzer = DualModeAnalyzer()
    rules = await analyzer._load_rules(config.rules_version)
    _, engine_rules = analyzer._separate_rules(rules)
    
    return await analyzer.engine_runner.run_rules(
        job_context=job_context,
        rules=engine_rules,
        config=config
    )


async def analyze_ai_only(job_context: JobContext,
                         config: Optional[AnalysisConfig] = None) -> List[IssueItem]:
    """仅使用 AI 分析"""
    if config is None:
        config = AnalysisConfig()
    
    analyzer = DualModeAnalyzer()
    rules = await analyzer._load_rules(config.rules_version)
    ai_rules, _ = analyzer._separate_rules(rules)
    
    # 使用 AI 分析
    return await analyzer._run_ai_analysis(
        job_context=job_context,
        ai_rules=ai_rules,
        config=config
    )


# 向后兼容的函数
def create_default_config() -> AnalysisConfig:
    """创建默认配置"""
    return AnalysisConfig(
        dual_mode=True,
        enable_ai_analysis=True,
        enable_ai_locator=True,
        ai_fallback_on_error=True,
        rules_version="v3_3"
    )
