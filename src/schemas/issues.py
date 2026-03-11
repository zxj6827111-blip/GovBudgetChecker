"""
统一数据结构定义
定义 AI 和规则引擎的统一输出格式
"""
from typing import List, Optional, Dict, Any, Literal, Union
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, model_validator
import time
import hashlib

from src.utils.issue_display import build_issue_display
from src.utils.issue_location import normalize_issue_location


class IssueSource(str, Enum):
    """问题来源"""
    AI = "ai"
    RULE = "rule"
    MERGED = "merged"


class IssueSeverity(str, Enum):
    """问题严重程度"""
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ConflictType(str, Enum):
    """冲突类型"""
    VALUE_MISMATCH = "value_mismatch"
    PAGE_MISMATCH = "page_mismatch"
    TITLE_MISMATCH = "title_mismatch"
    AMOUNT_MISMATCH = "amount_mismatch"
    PERCENTAGE_MISMATCH = "percentage_mismatch"


class IssueDisplay(BaseModel):
    """Readable display fields for UI and exports."""

    summary: str = Field(default="", description="一句话问题摘要")
    page_text: str = Field(default="", description="页码文本")
    location_text: str = Field(default="", description="定位文本")
    detail_lines: List[str] = Field(default_factory=list, description="细项说明")
    evidence_text: str = Field(default="", description="证据原文")


class IssueItem(BaseModel):
    """统一的问题项数据结构"""
    id: str = Field(..., description="全局唯一ID")
    source: Literal["ai", "rule"] = Field(..., description="来源：AI或规则引擎")
    rule_id: Optional[str] = Field(None, description="规则ID，如 V33-002 或 AI-RATIO-01")
    severity: Literal["info", "low", "medium", "high", "critical"] = Field(..., description="严重程度")
    title: str = Field(..., description="问题标题")
    message: str = Field(..., description="详细描述")
    evidence: List[Dict[str, Any]] = Field(default_factory=list, description="证据列表")
    location: Dict[str, Any] = Field(default_factory=dict, description="位置信息")
    metrics: Dict[str, Any] = Field(default_factory=dict, description="指标数据")
    suggestion: Optional[str] = Field(None, description="建议")
    tags: List[str] = Field(default_factory=list, description="标签")
    created_at: float = Field(default_factory=time.time, description="创建时间戳")
    
    # 新增字段以支持更丰富的数据结构
    page_number: int = Field(default=1, description="页码")
    bbox: Optional[List[float]] = Field(default=None, description="边界框 [x1, y1, x2, y2]")
    amount: Optional[float] = Field(default=None, description="金额")
    percentage: Optional[float] = Field(default=None, description="百分比")
    text_snippet: Optional[str] = Field(default=None, description="文本摘录")
    why_not: Optional[str] = Field(default=None, description="未命中原因")

    display: Optional[IssueDisplay] = Field(default=None, description="可直接展示的问题信息")

    @model_validator(mode="after")
    def populate_display(self) -> "IssueItem":
        self.location = normalize_issue_location(
            rule_id=self.rule_id,
            location=self.location,
            message=self.message,
            evidence_text=self.text_snippet or (self.evidence[0].get("text") if self.evidence else "") or "",
            evidence=self.evidence,
        )
        if self.location.get("page"):
            self.page_number = int(self.location["page"])
        if self.display and self.display.summary:
            return self
        payload = build_issue_display(self.model_dump(exclude={"display"}, mode="python"))
        self.display = IssueDisplay.model_validate(payload)
        return self

    @classmethod
    def create_id(cls, source: str, rule_id: str, location: Dict[str, Any]) -> str:
        """生成唯一ID"""
        loc_str = f"{location.get('page', 0)}_{location.get('section', '')}_{location.get('table', '')}"
        hash_input = f"{source}:{rule_id}:{loc_str}"
        return f"{source}:{rule_id}:{hashlib.md5(hash_input.encode()).hexdigest()[:8]}"


class ConflictItem(BaseModel):
    """冲突项数据结构"""
    key: str = Field(..., description="冲突键")
    ai_issue: Optional[str] = Field(None, description="AI问题ID")
    rule_issue: Optional[str] = Field(None, description="规则问题ID")
    reason: Literal["value-mismatch", "missing", "page-mismatch"] = Field(..., description="冲突原因")
    details: Dict[str, Any] = Field(default_factory=dict, description="冲突详情")
    
    # 新增字段
    ai_finding: Optional[IssueItem] = Field(default=None, description="AI检查结果")
    rule_finding: Optional[IssueItem] = Field(default=None, description="规则检查结果")
    conflict_type: Optional[ConflictType] = Field(default=None, description="冲突类型")
    reasons: List[str] = Field(default_factory=list, description="冲突原因列表")
    similarity_score: float = Field(default=0.0, description="相似度评分")
    recommended_action: str = Field(default="manual_review", description="建议操作")


class MergedSummary(BaseModel):
    """合并结果汇总"""
    totals: Dict[str, int] = Field(default_factory=dict, description="计数汇总")
    conflicts: List[ConflictItem] = Field(default_factory=list, description="冲突列表")
    agreements: List[str] = Field(default_factory=list, description="一致项ID列表")
    merged_ids: List[str] = Field(default_factory=list, description="合并后的问题ID列表")
    
    # 新增字段
    ai_only: List[IssueItem] = Field(default_factory=list, description="仅AI发现")
    rule_only: List[IssueItem] = Field(default_factory=list, description="仅规则发现")


class DualModeResponse(BaseModel):
    """双模式分析响应"""
    job_id: str = Field(..., description="任务ID")
    ai_findings: List[IssueItem] = Field(default_factory=list, description="AI检查结果")
    rule_findings: List[IssueItem] = Field(default_factory=list, description="规则检查结果")
    merged: MergedSummary = Field(default_factory=MergedSummary, description="合并结果")
    meta: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    
    # 兼容性字段（camelCase适配）
    aiFindings: Optional[List[IssueItem]] = Field(default=None, description="AI检查结果（camelCase）")
    ruleFindings: Optional[List[IssueItem]] = Field(default=None, description="规则检查结果（camelCase）")
    
    def __init__(self, **data):
        super().__init__(**data)
        
        # 确保meta中包含provider_stats
        if "provider_stats" not in self.meta:
            self.meta["provider_stats"] = []
            
        # 添加camelCase适配字段（过渡期兼容）
        self.aiFindings = self.ai_findings
        self.ruleFindings = self.rule_findings


class JobContext(BaseModel):
    """任务上下文"""
    job_id: str = Field(..., description="任务ID")
    pdf_path: str = Field(..., description="PDF文件路径")
    ocr_text: Optional[str] = Field(None, description="OCR提取的文本")
    page_texts: List[str] = Field(default_factory=list, description="每页提取的文本")
    page_tables: List[Any] = Field(default_factory=list, description="每页提取的表格")
    tables: List[Dict[str, Any]] = Field(default_factory=list, description="表格数据")
    pages: int = Field(default=0, description="页数")
    filesize: int = Field(default=0, description="文件大小")
    meta: Dict[str, Any] = Field(default_factory=dict, description="其他元数据")


class AnalysisConfig(BaseModel):
    """分析配置"""
    dual_mode: bool = Field(default=True, description="是否启用双模式")
    ai_enabled: bool = Field(default=True, description="是否启用AI分析")
    rule_enabled: bool = Field(default=True, description="是否启用规则分析")
    merge_enabled: bool = Field(default=True, description="是否启用结果合并")
    
    # 合并参数
    title_similarity_threshold: float = Field(default=0.85, description="标题相似度阈值")
    money_tolerance: float = Field(default=0.005, description="金额容差（0.5%）")
    percentage_tolerance: float = Field(default=0.002, description="百分比容差（0.2pp）")
    page_tolerance: int = Field(default=1, description="页码容差")
    
    # AI参数
    ai_timeout: int = Field(default=60, description="AI超时时间（秒）")
    ai_retry: int = Field(default=1, description="AI重试次数")
    ai_temperature: float = Field(default=0.2, description="AI温度参数")
    
    # 兼容双模式与引擎运行器所需的扩展字段
    rules_version: str = Field(default="v3_3", description="规则版本（如 v3_3）")
    enable_ai_analysis: bool = Field(default=True, description="是否启用AI分析（双模式分支开关）")
    enable_ai_locator: bool = Field(default=True, description="是否启用AI定位增强（将AI帮助用于定位证据）")
    ai_fallback_on_error: bool = Field(default=True, description="AI分析失败时是否静默回退到仅规则模式")
    record_rule_failures: bool = Field(default=False, description="是否将规则执行失败记录为问题项")


# 新增模型定义
class ProviderStats(BaseModel):
    """提供商统计"""
    name: str = Field(..., description="提供商名称")
    model: str = Field(..., description="模型名称")
    requests: int = Field(default=0, description="请求次数")
    successes: int = Field(default=0, description="成功次数")
    failures: int = Field(default=0, description="失败次数")
    total_tokens: int = Field(default=0, description="总token数")
    total_latency_ms: int = Field(default=0, description="总延迟毫秒")
    circuit_state: str = Field(default="closed", description="熔断器状态")
    last_used: Optional[datetime] = Field(default=None, description="最后使用时间")


class AnalysisMetrics(BaseModel):
    """分析指标"""
    # 时间指标
    ai_elapsed_ms: int = Field(default=0, description="AI分析耗时")
    rule_elapsed_ms: int = Field(default=0, description="规则分析耗时")
    merge_elapsed_ms: int = Field(default=0, description="合并耗时")
    total_elapsed_ms: int = Field(default=0, description="总耗时")
    
    # 结果指标
    ai_findings_count: int = Field(default=0, description="AI发现数量")
    rule_findings_count: int = Field(default=0, description="规则发现数量")
    agreements_count: int = Field(default=0, description="一致结果数量")
    conflicts_count: int = Field(default=0, description="冲突数量")
    
    # AI指标
    provider_used: Optional[str] = Field(default=None, description="使用的提供商")
    model_used: Optional[str] = Field(default=None, description="使用的模型")
    fell_back: bool = Field(default=False, description="是否发生回退")
    retries: int = Field(default=0, description="重试次数")
    total_tokens: int = Field(default=0, description="总token数")
    
    # 质量指标
    agreement_rate: float = Field(default=0.0, description="一致率")
    conflict_rate: float = Field(default=0.0, description="冲突率")
    coverage_rate: float = Field(default=0.0, description="覆盖率")


class AnalysisRequest(BaseModel):
    """分析请求"""
    job_id: str = Field(..., description="任务ID")
    file_path: Optional[str] = Field(default=None, description="文件路径")
    mode: str = Field(default="auto", description="分析模式")
    config: Optional[AnalysisConfig] = Field(default=None, description="分析配置")
    rules: Optional[List[str]] = Field(default=None, description="指定规则")


class AnalysisResponse(BaseModel):
    """分析响应"""
    job_id: str = Field(..., description="任务ID")
    status: str = Field(..., description="状态")
    
    # 结果数据
    ai_findings: List[IssueItem] = Field(default_factory=list, description="AI检查结果")
    rule_findings: List[IssueItem] = Field(default_factory=list, description="规则检查结果")
    merged: Optional[MergedSummary] = Field(default=None, description="合并结果")
    
    # 元数据
    meta: AnalysisMetrics = Field(default_factory=AnalysisMetrics, description="分析指标")
    provider_stats: List[ProviderStats] = Field(default_factory=list, description="提供商统计")
    
    # 错误信息
    error: Optional[str] = Field(default=None, description="错误信息")
    warnings: List[str] = Field(default_factory=list, description="警告信息")


class HealthStatus(BaseModel):
    """健康状态"""
    status: str = Field(..., description="整体状态")
    timestamp: datetime = Field(default_factory=datetime.now, description="检查时间")
    
    # 组件状态
    ai_service: Dict[str, Any] = Field(default_factory=dict, description="AI服务状态")
    rule_engine: Dict[str, Any] = Field(default_factory=dict, description="规则引擎状态")
    
    # 提供商状态
    providers: List[Dict[str, Any]] = Field(default_factory=list, description="提供商状态")
    
    # 系统指标
    uptime_seconds: int = Field(default=0, description="运行时间秒数")
    total_requests: int = Field(default=0, description="总请求数")
    success_rate: float = Field(default=0.0, description="成功率")


class RuleCoverage(BaseModel):
    """规则覆盖率"""
    rule_id: str = Field(..., description="规则ID")
    rule_name: str = Field(..., description="规则名称")
    executor: str = Field(..., description="执行器类型")
    
    # 统计信息
    total_runs: int = Field(default=0, description="总运行次数")
    successful_runs: int = Field(default=0, description="成功运行次数")
    failed_runs: int = Field(default=0, description="失败运行次数")
    
    # 命中信息
    hits: int = Field(default=0, description="命中次数")
    misses: int = Field(default=0, description="未命中次数")
    hit_rate: float = Field(default=0.0, description="命中率")
    
    # 失败原因统计
    failure_reasons: Dict[str, int] = Field(default_factory=dict, description="失败原因统计")
    
    # 最近状态
    last_run: Optional[datetime] = Field(default=None, description="最后运行时间")
    last_status: Optional[str] = Field(default=None, description="最后状态")


# 便捷函数
def create_issue_item(title: str, 
                     rule_id: str, 
                     source: str,
                     page_number: int,
                     **kwargs) -> IssueItem:
    """创建问题项的便捷函数"""
    return IssueItem(
        id=IssueItem.create_id(source, rule_id, {"page": page_number}),
        title=title,
        rule_id=rule_id,
        source=source,
        page_number=page_number,
        **kwargs
    )


def create_default_config() -> AnalysisConfig:
    """创建默认配置"""
    return AnalysisConfig(
        dual_mode=True,
        ai_enabled=True,
        rule_enabled=True,
        merge_enabled=True,
        title_similarity_threshold=0.85,  # 调宽阈值，让AI结果更容易显示
        money_tolerance=0.005,
        percentage_tolerance=0.002,
        page_tolerance=1,  # 调宽页码容差
        ai_timeout=60,
        ai_retry=1,
        ai_temperature=0.1,
        # 新增字段默认值，保证与双模式分析器兼容
        rules_version="v3_3",
        enable_ai_analysis=True,
        enable_ai_locator=True,
        ai_fallback_on_error=True,
        record_rule_failures=False
    )
