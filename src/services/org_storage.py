"""
组织架构存储服务
使用 JSON 文件存储组织数据
"""
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.schemas.organization import (
    ImportResult,
    JobOrganizationLink,
    Organization,
    OrganizationLevel,
    OrganizationStore,
    OrganizationTree,
)
from src.services.org_hierarchy import validate_organization_hierarchy

logger = logging.getLogger(__name__)

# 数据文件路径
_TESTING = os.getenv("TESTING", "").strip().lower() in {"1", "true", "yes"}
_REPO_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_TEST_DATA_DIR = Path(tempfile.gettempdir()) / "govbudgetchecker_test_data"
_DEFAULT_DATA_DIR = _TEST_DATA_DIR if _TESTING else _REPO_DATA_DIR
DATA_DIR = Path(os.getenv("ORG_DATA_DIR", _DEFAULT_DATA_DIR)).resolve()
ORG_FILE = DATA_DIR / "organizations.json"
LINKS_FILE = DATA_DIR / "job_org_links.json"


class OrganizationStorage:
    """组织架构存储服务"""
    
    def __init__(self):
        self._ensure_data_dir()
        self._organizations: List[Organization] = []
        self._links: List[JobOrganizationLink] = []
        self._org_by_id: Dict[str, Organization] = {}
        self._children_by_parent: Dict[Optional[str], List[Organization]] = {}
        self._link_by_job: Dict[str, JobOrganizationLink] = {}
        self._jobs_by_org: Dict[str, List[str]] = {}
        self._load()
        self._rebuild_indexes()
    
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
    
    def _rebuild_indexes(self):
        """Rebuild read-optimized in-memory indexes."""
        self._org_by_id = {org.id: org for org in self._organizations}

        children: Dict[Optional[str], List[Organization]] = {}
        for org in self._organizations:
            children.setdefault(org.parent_id, []).append(org)
        self._children_by_parent = children

        link_by_job: Dict[str, JobOrganizationLink] = {}
        jobs_by_org: Dict[str, List[str]] = {}
        for link in self._links:
            link_by_job[link.job_id] = link
            jobs_by_org.setdefault(link.org_id, []).append(link.job_id)
        self._link_by_job = link_by_job
        self._jobs_by_org = jobs_by_org

    # ==================== 组织 CRUD ====================
    
    def get_all(self) -> List[Organization]:
        """获取所有组织"""
        return self._organizations.copy()
    
    def get_by_id(self, org_id: str) -> Optional[Organization]:
        """根据ID获取组织"""
        return self._org_by_id.get(org_id)
    
    def get_by_level(self, level: str) -> List[Organization]:
        """根据层级获取组织"""
        return [org for org in self._organizations if org.level == level]
    
    def get_children(self, parent_id: Optional[str]) -> List[Organization]:
        """获取子组织"""
        return list(self._children_by_parent.get(parent_id, []))

    def get_departments(self) -> List[Organization]:
        """获取所有部门（按名称排序）"""
        departments = [
            org for org in self._organizations if org.level == OrganizationLevel.DEPARTMENT
        ]
        return departments

    def get_units_by_department(self, dept_id: str) -> List[Organization]:
        """获取指定部门下的直属单位（按名称排序）"""
        department = self.get_by_id(dept_id)
        if department is None or department.level != OrganizationLevel.DEPARTMENT:
            return []

        units = [
            org
            for org in self.get_children(dept_id)
            if org.level == OrganizationLevel.UNIT
        ]
        return units
    
    def add(self, org: Organization) -> Organization:
        """添加组织"""
        # 检查是否已存在
        existing = self.get_by_id(org.id)
        if existing:
            raise ValueError(f"Organization with id {org.id} already exists")
        
        self._organizations.append(org)
        self._rebuild_indexes()
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
                self._rebuild_indexes()
                self._save_organizations()
                return self._organizations[i]
        return None
    
    def delete(self, org_id: str) -> bool:
        """删除组织（及其所有子组织）"""
        if org_id not in self._org_by_id:
            return False

        to_delete = set()
        
        stack = [org_id]
        while stack:
            current = stack.pop()
            if current in to_delete:
                continue
            to_delete.add(current)
            for child in self._children_by_parent.get(current, []):
                stack.append(child.id)
        
        original_count = len(self._organizations)
        self._organizations = [org for org in self._organizations if org.id not in to_delete]
        
        if len(self._organizations) < original_count:
            self._rebuild_indexes()
            self._save_organizations()
            # 同时删除关联
            self._links = [link for link in self._links if link.org_id not in to_delete]
            self._rebuild_indexes()
            self._save_links()
            return True
        return False
    
    def clear_all(self):
        """清空所有组织"""
        self._organizations = []
        self._links = []
        self._rebuild_indexes()
        self._save_organizations()
        self._save_links()
    
    # ==================== 组织树 ====================
    
    def get_tree(self) -> List[OrganizationTree]:
        """获取组织树"""
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
    
    def link_job(
        self,
        job_id: str,
        org_id: str,
        match_type: str = "manual",
        confidence: float = 1.0,
    ) -> JobOrganizationLink:
        """关联任务到组织"""
        existing_link = self._link_by_job.get(job_id)
        if existing_link is not None:
            existing_link.org_id = org_id
            existing_link.match_type = match_type
            existing_link.confidence = confidence
            self._rebuild_indexes()
            self._save_links()
            return existing_link
        
        # 创建新关联
        link = JobOrganizationLink(
            job_id=job_id,
            org_id=org_id,
            match_type=match_type,
            confidence=confidence
        )
        self._links.append(link)
        self._rebuild_indexes()
        self._save_links()
        return link
    
    def get_job_org(self, job_id: str) -> Optional[JobOrganizationLink]:
        """获取任务的组织关联"""
        return self._link_by_job.get(job_id)
    
    def get_org_jobs(self, org_id: str, include_children: bool = True) -> List[str]:
        """获取组织下的所有任务ID"""
        if not include_children:
            return list(self._jobs_by_org.get(org_id, []))

        ordered_org_ids: List[str] = []
        seen_org_ids = set()
        stack = [org_id]
        while stack:
            current = stack.pop()
            if current in seen_org_ids:
                continue
            seen_org_ids.add(current)
            ordered_org_ids.append(current)
            for child in self._children_by_parent.get(current, []):
                stack.append(child.id)

        result: List[str] = []
        seen_job_ids = set()
        for oid in ordered_org_ids:
            for job_id in self._jobs_by_org.get(oid, []):
                if job_id in seen_job_ids:
                    continue
                seen_job_ids.add(job_id)
                result.append(job_id)
        return result
    
    def unlink_job(self, job_id: str) -> bool:
        """取消任务关联"""
        original_count = len(self._links)
        self._links = [link for link in self._links if link.job_id != job_id]
        if len(self._links) < original_count:
            self._rebuild_indexes()
            self._save_links()
            return True
        return False
    
    def validate_hierarchy(self) -> Dict[str, Any]:
        """校验组织层级关系。"""

        result = validate_organization_hierarchy(self._organizations)
        return result.to_dict()

    # ==================== 导入 ====================

    @staticmethod
    def _first_non_empty(item: Dict[str, Any], keys: List[str]) -> str:
        for key in keys:
            if key not in item:
                continue
            value = item.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def _expand_import_item(self, item: Dict[str, Any]) -> List[Dict[str, str]]:
        """Normalize one import row into one or more records.

        Supported patterns:
        1. Flat record: `name + level (+ parent)`
        2. Department + unit record: `department_name + unit_name`
        """

        level = self._first_non_empty(item, ["level", "层级"]).lower()
        code = self._first_non_empty(item, ["code", "代码"])
        parent_name = self._first_non_empty(item, ["parent", "上级", "parent_name", "父级"])

        if level:
            name = self._first_non_empty(
                item,
                [
                    "name",
                    "名称",
                    "单位名称",
                    "组织名称",
                    "department_name",
                    "department",
                    "部门",
                    "部门名称",
                    "unit_name",
                    "unit",
                    "单位",
                ],
            )
            if not name:
                return []
            return [
                {
                    "name": name,
                    "level": level,
                    "parent_name": parent_name,
                    "code": code,
                }
            ]

        department_name = self._first_non_empty(
            item,
            [
                "department_name",
                "department",
                "部门",
                "部门名称",
                "所属部门",
            ],
        )
        unit_name = self._first_non_empty(
            item,
            [
                "unit_name",
                "unit",
                "单位",
                "单位名称",
                "name",
                "名称",
            ],
        )

        records: List[Dict[str, str]] = []
        if department_name:
            records.append(
                {
                    "name": department_name,
                    "level": OrganizationLevel.DEPARTMENT,
                    "parent_name": "",
                    "code": "",
                }
            )

        if unit_name:
            unit_parent = parent_name or department_name
            records.append(
                {
                    "name": unit_name,
                    "level": OrganizationLevel.UNIT,
                    "parent_name": unit_parent,
                    "code": code,
                }
            )
        return records

    def _resolve_parent_id(self, parent_name: str) -> Optional[str]:
        if not parent_name:
            return None

        candidates = [org for org in self._organizations if org.name == parent_name]
        if not candidates:
            return None

        priority = {
            OrganizationLevel.DEPARTMENT: 0,
            OrganizationLevel.DISTRICT: 1,
            OrganizationLevel.CITY: 2,
            OrganizationLevel.UNIT: 3,
        }
        candidates.sort(key=lambda org: priority.get(org.level, 99))
        return candidates[0].id

    def import_from_list(
        self,
        data: List[Dict[str, Any]],
        clear_existing: bool = False,
    ) -> ImportResult:
        """从列表导入组织，支持 `department/unit + parent` 模板。"""

        original_organizations = [org.model_copy(deep=True) for org in self._organizations]
        if clear_existing:
            self._organizations = []
            self._rebuild_indexes()

        expanded_rows: List[Dict[str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            expanded_rows.extend(self._expand_import_item(item))

        result = ImportResult(success=True, total=len(data))
        pending: List[Dict[str, str]] = expanded_rows.copy()
        pass_count = 0
        max_passes = 5

        allowed_levels = {
            OrganizationLevel.CITY,
            OrganizationLevel.DISTRICT,
            OrganizationLevel.DEPARTMENT,
            OrganizationLevel.UNIT,
        }

        while pending and pass_count < max_passes:
            pass_count += 1
            progressed = False
            next_pending: List[Dict[str, str]] = []

            for row in pending:
                name = row.get("name", "").strip()
                level = row.get("level", "").strip().lower()
                parent_name = row.get("parent_name", "").strip()
                code = row.get("code", "").strip()

                if not name:
                    result.errors.append(f"缺少名称: {row}")
                    result.skipped += 1
                    continue
                if level not in allowed_levels:
                    result.errors.append(f"无效层级 '{level}': {row}")
                    result.skipped += 1
                    continue

                parent_id = self._resolve_parent_id(parent_name)
                if parent_name and parent_id is None:
                    next_pending.append(row)
                    continue

                org_id = Organization.generate_id(name, level, parent_id)
                if any(existing.id == org_id for existing in self._organizations):
                    result.skipped += 1
                    progressed = True
                    continue

                try:
                    org = Organization(
                        id=org_id,
                        name=name,
                        level=level,
                        parent_id=parent_id,
                        code=code,
                        keywords=[name],
                    )
                except Exception as e:
                    result.errors.append(f"导入失败: {row}, 错误: {e}")
                    result.skipped += 1
                    continue

                self._organizations.append(org)
                result.imported += 1
                progressed = True

            pending = next_pending
            if not progressed:
                break

        for row in pending:
            result.errors.append(f"父级未找到，无法导入: {row}")
            result.skipped += 1

        validation = validate_organization_hierarchy(self._organizations)
        if validation.errors:
            self._organizations = original_organizations
            self._rebuild_indexes()
            result.errors.extend([f"层级校验失败: {msg}" for msg in validation.errors])
            result.success = False
            return result

        self._rebuild_indexes()
        self._save_organizations()
        result.success = result.imported > 0 and not result.errors
        return result


# 单例实例
_storage_instance = None

def get_org_storage() -> OrganizationStorage:
    """获取组织存储单例"""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = OrganizationStorage()
    return _storage_instance
