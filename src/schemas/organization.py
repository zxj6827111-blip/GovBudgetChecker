"""
组织架构数据模型
定义四级层级：市 -> 区 -> 部门 -> 单位
"""
from typing import Optional, List, Literal, Dict, Any
from pydantic import BaseModel, Field
import time
import hashlib


class OrganizationLevel:
    """组织层级常量"""
    CITY = "city"           # 市
    DISTRICT = "district"   # 区
    DEPARTMENT = "department"  # 部门
    UNIT = "unit"           # 单位
    
    @classmethod
    def get_display_name(cls, level: str) -> str:
        """获取层级的中文显示名称"""
        names = {
            cls.CITY: "市",
            cls.DISTRICT: "区",
            cls.DEPARTMENT: "部门",
            cls.UNIT: "单位"
        }
        return names.get(level, level)
    
    @classmethod
    def get_child_level(cls, level: str) -> Optional[str]:
        """获取下一级层级"""
        hierarchy = [cls.CITY, cls.DISTRICT, cls.DEPARTMENT, cls.UNIT]
        try:
            idx = hierarchy.index(level)
            return hierarchy[idx + 1] if idx < len(hierarchy) - 1 else None
        except ValueError:
            return None


class Organization(BaseModel):
    """组织实体"""
    id: str = Field(..., description="组织唯一ID")
    name: str = Field(..., description="组织名称")
    level: Literal["city", "district", "department", "unit"] = Field(..., description="层级")
    parent_id: Optional[str] = Field(None, description="上级组织ID")
    code: Optional[str] = Field(None, description="组织代码（可选）")
    keywords: List[str] = Field(default_factory=list, description="用于匹配的关键词")
    created_at: float = Field(default_factory=time.time, description="创建时间")
    updated_at: float = Field(default_factory=time.time, description="更新时间")
    
    @classmethod
    def generate_id(cls, name: str, level: str, parent_id: Optional[str] = None) -> str:
        """生成组织ID"""
        key = f"{level}:{parent_id or 'root'}:{name}"
        return hashlib.md5(key.encode()).hexdigest()[:12]


class OrganizationTree(BaseModel):
    """组织树节点（用于前端展示）"""
    id: str
    name: str
    level: str
    level_name: str  # 中文层级名
    parent_id: Optional[str]
    children: List["OrganizationTree"] = Field(default_factory=list)
    job_count: int = Field(default=0, description="关联的任务数量")
    issue_count: int = Field(default=0, description="问题总数")


class OrganizationStore(BaseModel):
    """组织数据存储格式"""
    organizations: List[Organization] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)


class JobOrganizationLink(BaseModel):
    """任务与组织的关联"""
    job_id: str = Field(..., description="任务ID")
    org_id: str = Field(..., description="组织ID")
    match_type: Literal["auto", "manual"] = Field(..., description="匹配方式")
    confidence: float = Field(default=1.0, description="匹配置信度（0-1）")
    created_at: float = Field(default_factory=time.time)


class ImportResult(BaseModel):
    """导入结果"""
    success: bool
    total: int = 0
    imported: int = 0
    skipped: int = 0
    errors: List[str] = Field(default_factory=list)


# 更新前向引用
OrganizationTree.model_rebuild()
