"""
组织架构存储服务
使用 JSON 文件存储组织数据
"""
import json
import logging
import os
from pathlib import Path
from typing import List, Optional, Dict, Any
from schemas.organization import (
    Organization, OrganizationStore, OrganizationTree, 
    JobOrganizationLink, OrganizationLevel, ImportResult
)

logger = logging.getLogger(__name__)

# 数据文件路径
DATA_DIR = Path(__file__).parent.parent / "data"
ORG_FILE = DATA_DIR / "organizations.json"
LINKS_FILE = DATA_DIR / "job_org_links.json"


class OrganizationStorage:
    """组织架构存储服务"""
    
    def __init__(self):
        self._ensure_data_dir()
        self._organizations: List[Organization] = []
        self._links: List[JobOrganizationLink] = []
        self._load()
    
    def _ensure_data_dir(self):
        """确保数据目录存在"""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    def _load(self):
        """加载数据"""
        # 加载组织数据
        if ORG_FILE.exists():
            try:
                data = json.loads(ORG_FILE.read_text(encoding="utf-8"))
                store = OrganizationStore(**data)
                self._organizations = store.organizations
                logger.info(f"Loaded {len(self._organizations)} organizations")
            except Exception as e:
                logger.error(f"Failed to load organizations: {e}")
                self._organizations = []
        
        # 加载关联数据
        if LINKS_FILE.exists():
            try:
                data = json.loads(LINKS_FILE.read_text(encoding="utf-8"))
                self._links = [JobOrganizationLink(**link) for link in data.get("links", [])]
                logger.info(f"Loaded {len(self._links)} job-org links")
            except Exception as e:
                logger.error(f"Failed to load links: {e}")
                self._links = []
    
    def _save_organizations(self):
        """保存组织数据"""
        store = OrganizationStore(
            organizations=self._organizations,
            meta={"updated_at": __import__("time").time()}
        )
        ORG_FILE.write_text(
            json.dumps(store.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    
    def _save_links(self):
        """保存关联数据"""
        data = {
            "links": [link.model_dump() for link in self._links],
            "updated_at": __import__("time").time()
        }
        LINKS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    
    # ==================== 组织 CRUD ====================
    
    def get_all(self) -> List[Organization]:
        """获取所有组织"""
        return self._organizations.copy()
    
    def get_by_id(self, org_id: str) -> Optional[Organization]:
        """根据ID获取组织"""
        for org in self._organizations:
            if org.id == org_id:
                return org
        return None
    
    def get_by_level(self, level: str) -> List[Organization]:
        """根据层级获取组织"""
        return [org for org in self._organizations if org.level == level]
    
    def get_children(self, parent_id: Optional[str]) -> List[Organization]:
        """获取子组织"""
        return [org for org in self._organizations if org.parent_id == parent_id]
    
    def add(self, org: Organization) -> Organization:
        """添加组织"""
        # 检查是否已存在
        existing = self.get_by_id(org.id)
        if existing:
            raise ValueError(f"Organization with id {org.id} already exists")
        
        self._organizations.append(org)
        self._save_organizations()
        return org
    
    def update(self, org_id: str, updates: Dict[str, Any]) -> Optional[Organization]:
        """更新组织"""
        for i, org in enumerate(self._organizations):
            if org.id == org_id:
                org_dict = org.model_dump()
                org_dict.update(updates)
                org_dict["updated_at"] = __import__("time").time()
                self._organizations[i] = Organization(**org_dict)
                self._save_organizations()
                return self._organizations[i]
        return None
    
    def delete(self, org_id: str) -> bool:
        """删除组织（及其所有子组织）"""
        to_delete = set()
        
        def collect_children(parent_id: str):
            to_delete.add(parent_id)
            for org in self._organizations:
                if org.parent_id == parent_id:
                    collect_children(org.id)
        
        collect_children(org_id)
        
        original_count = len(self._organizations)
        self._organizations = [org for org in self._organizations if org.id not in to_delete]
        
        if len(self._organizations) < original_count:
            self._save_organizations()
            # 同时删除关联
            self._links = [link for link in self._links if link.org_id not in to_delete]
            self._save_links()
            return True
        return False
    
    def clear_all(self):
        """清空所有组织"""
        self._organizations = []
        self._links = []
        self._save_organizations()
        self._save_links()
    
    # ==================== 组织树 ====================
    
    def get_tree(self) -> List[OrganizationTree]:
        """获取组织树"""
        # 构建ID到组织的映射
        org_map = {org.id: org for org in self._organizations}
        
        # 统计每个组织的任务数和问题数
        job_counts = {}
        issue_counts = {}
        for link in self._links:
            job_counts[link.org_id] = job_counts.get(link.org_id, 0) + 1
        
        def build_tree_node(org: Organization) -> OrganizationTree:
            children = self.get_children(org.id)
            child_nodes = [build_tree_node(child) for child in children]
            
            # 累加子节点的统计
            total_jobs = job_counts.get(org.id, 0)
            total_issues = issue_counts.get(org.id, 0)
            for child in child_nodes:
                total_jobs += child.job_count
                total_issues += child.issue_count
            
            return OrganizationTree(
                id=org.id,
                name=org.name,
                level=org.level,
                level_name=OrganizationLevel.get_display_name(org.level),
                parent_id=org.parent_id,
                children=child_nodes,
                job_count=total_jobs,
                issue_count=total_issues
            )
        
        # 找到根节点（没有父组织的）
        roots = [org for org in self._organizations if org.parent_id is None]
        return [build_tree_node(root) for root in roots]
    
    # ==================== 任务关联 ====================
    
    def link_job(self, job_id: str, org_id: str, match_type: str = "manual", confidence: float = 1.0) -> JobOrganizationLink:
        """关联任务到组织"""
        # 检查是否已存在
        for link in self._links:
            if link.job_id == job_id:
                # 更新现有关联
                link.org_id = org_id
                link.match_type = match_type
                link.confidence = confidence
                self._save_links()
                return link
        
        # 创建新关联
        link = JobOrganizationLink(
            job_id=job_id,
            org_id=org_id,
            match_type=match_type,
            confidence=confidence
        )
        self._links.append(link)
        self._save_links()
        return link
    
    def get_job_org(self, job_id: str) -> Optional[JobOrganizationLink]:
        """获取任务的组织关联"""
        for link in self._links:
            if link.job_id == job_id:
                return link
        return None
    
    def get_org_jobs(self, org_id: str, include_children: bool = True) -> List[str]:
        """获取组织下的所有任务ID"""
        org_ids = {org_id}
        
        if include_children:
            def collect_children(parent_id: str):
                for org in self._organizations:
                    if org.parent_id == parent_id:
                        org_ids.add(org.id)
                        collect_children(org.id)
            collect_children(org_id)
        
        return [link.job_id for link in self._links if link.org_id in org_ids]
    
    def unlink_job(self, job_id: str) -> bool:
        """取消任务关联"""
        original_count = len(self._links)
        self._links = [link for link in self._links if link.job_id != job_id]
        if len(self._links) < original_count:
            self._save_links()
            return True
        return False
    
    # ==================== 导入 ====================
    
    def import_from_list(self, data: List[Dict[str, Any]], clear_existing: bool = False) -> ImportResult:
        """从列表导入组织"""
        if clear_existing:
            self._organizations = []
        
        result = ImportResult(success=True, total=len(data))
        
        for item in data:
            try:
                # 提取字段
                name = item.get("name") or item.get("名称") or item.get("单位名称", "")
                level = item.get("level") or item.get("层级", "unit")
                parent_name = item.get("parent") or item.get("上级") or item.get("所属部门", "")
                code = item.get("code") or item.get("代码", "")
                
                if not name:
                    result.errors.append(f"缺少名称: {item}")
                    result.skipped += 1
                    continue
                
                # 查找父组织
                parent_id = None
                if parent_name:
                    for org in self._organizations:
                        if org.name == parent_name:
                            parent_id = org.id
                            break
                
                # 生成ID
                org_id = Organization.generate_id(name, level, parent_id)
                
                # 检查是否已存在
                if self.get_by_id(org_id):
                    result.skipped += 1
                    continue
                
                # 创建组织
                org = Organization(
                    id=org_id,
                    name=name,
                    level=level,
                    parent_id=parent_id,
                    code=code,
                    keywords=[name]  # 使用名称作为匹配关键词
                )
                self._organizations.append(org)
                result.imported += 1
                
            except Exception as e:
                result.errors.append(f"导入失败: {item}, 错误: {str(e)}")
                result.skipped += 1
        
        self._save_organizations()
        result.success = result.imported > 0
        return result


# 单例实例
_storage_instance = None

def get_org_storage() -> OrganizationStorage:
    """获取组织存储单例"""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = OrganizationStorage()
    return _storage_instance
