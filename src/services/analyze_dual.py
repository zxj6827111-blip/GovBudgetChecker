"""
双模式分析编排服务
并行执行 AI 和规则检查，合并结果
"""
import logging
import asyncio
import time
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path
import json

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
    
    def __init__(self):
        self.ai_service = None  # 延迟初始化
        self.engine_runner = EngineRuleRunner()
        self.ai_locator = AILocator() if AILocator is not None else None
    
    async def analyze(self, 
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
        
        # 超时设置（120秒）
        TIMEOUT_SECONDS = 120
        
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
            ai_task = asyncio.create_task(self._run_ai_analysis(job_context, ai_rules, config)) if config.enable_ai_analysis else None
            rule_task = asyncio.create_task(self._run_engine_analysis(job_context, engine_rules, config)) if engine_rules else None
            
            # ========== 增量发布策略 ==========
            # 1. 先等待规则任务完成（通常较快）
            rule_findings = []
            rule_error = None
            rule_done_at = rule_started_at
            
            if rule_task is not None:
                try:
                    rule_result = await asyncio.wait_for(rule_task, timeout=30)  # 规则 30 秒超时
                    rule_done_at = time.time()
                    if isinstance(rule_result, Exception):
                        rule_error = str(rule_result)
                        logger.error(f"Rule analysis failed: {rule_error}")
                    else:
                        rule_findings = rule_result or []
                        logger.info(f"Rule analysis completed, found {len(rule_findings)} issues")
                except asyncio.TimeoutError:
                    rule_done_at = time.time()
                    rule_error = "规则分析超时(30s)"
                    logger.warning(rule_error)
                except Exception as e:
                    rule_done_at = time.time()
                    rule_error = str(e)
                    logger.error(f"Rule analysis exception: {e}")
            
            metrics.rule_findings_count = len(rule_findings)
            metrics.rule_elapsed_ms = int((rule_done_at - rule_started_at) * 1000)
            
            # 2. 规则完成后立即发布快照（前端可先看到规则结果）
            save_snapshot(job_context.job_id, {
                "status": "processing",
                "progress": 70,
                "stage": "本地规则已完成，AI 正在审计...",
                "ai_findings": [],
                "rule_findings": rule_findings,
                "meta": {
                    "ai_status": "processing",
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
            
            if ai_task is not None:
                try:
                    ai_result = await asyncio.wait_for(ai_task, timeout=TIMEOUT_SECONDS - 30)  # 剩余时间给 AI
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
                        logger.info(f"AI analysis completed, found {len(ai_findings)} issues")
                except asyncio.TimeoutError:
                    ai_done_at = time.time()
                    ai_error = f"AI分析超时({TIMEOUT_SECONDS - 30}s)"
                    logger.warning(ai_error)
                except Exception as e:
                    ai_done_at = time.time()
                    ai_error = str(e)
                    logger.error(f"AI analysis exception: {e}")
            
            metrics.ai_findings_count = len(ai_findings)
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
                rule_findings = await self._enhance_with_ai_locator(
                    job_context, rule_findings, metrics
                )
            
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
                    "config": config.dict()
                }
            )
            
        except Exception as e:
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
            
            # 降级：仅返回引擎结果
            try:
                rule_findings = await self._fallback_engine_only(job_context, config)
                metrics.total_elapsed_ms = int((time.time() - start_time) * 1000)
                
                return DualModeResponse(
                    job_id=job_context.job_id,
                    ai_findings=[],
                    rule_findings=rule_findings,
                    merged=MergedSummary(
                        totals={"rule_only": len(rule_findings)},
                        conflicts=[],
                        agreements=[]
                    ),
                    meta={
                        "elapsed_ms": {"total": metrics.total_elapsed_ms},
                        "error": str(e),
                        "fallback": "engine_only",
                        "provider_stats": []
                    }
                )
            except Exception as fallback_error:
                logger.error(f"Fallback analysis also failed: {fallback_error}")
                raise e
    
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
                              config: AnalysisConfig) -> List[IssueItem]:
        """运行 AI 分析"""
        try:
            # 延迟初始化AI服务
            if self.ai_service is None:
                self.ai_service = AIFindingsService(config)
            
            # 使用AI服务进行分析
            return await self.ai_service.analyze(job_context)
        except Exception as e:
            logger.error(f"AI analysis failed: {e}")
            if config.ai_fallback_on_error:
                return []
            else:
                raise e
    
    async def _run_engine_analysis(self, 
                                  job_context: JobContext,
                                  engine_rules: List[Dict[str, Any]],
                                  config: AnalysisConfig) -> List[IssueItem]:
        """运行引擎分析"""
        return await self.engine_runner.run_rules(
            job_context=job_context,
            rules=engine_rules,
            config=config
        )
    
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
        
        return await self.engine_runner.run_rules(
            job_context=job_context,
            rules=engine_rules,
            config=config
        )


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