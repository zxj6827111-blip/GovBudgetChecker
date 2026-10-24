"""
组织架构存储服务
使用 JSON 文件存储组织数据
"""

import json
from contextlib import contextmanager
from functools import wraps
import threading
import time
import logging
import os
import re
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
from src.services.org_hierarchy import LEVEL_ORDER, validate_organization_hierarchy

logger = logging.getLogger(__name__)

# 数据文件路径
_TESTING = os.getenv("TESTING", "").strip().lower() in {"1", "true", "yes"}
_REPO_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_TEST_DATA_DIR = Path(tempfile.gettempdir()) / "govbudgetchecker_test_data"
_DEFAULT_DATA_DIR = _TEST_DATA_DIR if _TESTING else _REPO_DATA_DIR
DATA_DIR = Path(os.getenv("ORG_DATA_DIR", _DEFAULT_DATA_DIR)).resolve()
ORG_FILE = DATA_DIR / "organizations.json"
LINKS_FILE = DATA_DIR / "job_org_links.json"


@contextmanager
def _catalog_lock():
    """Serialize JSON read/modify/write across API and Worker processes."""
    path = DATA_DIR / ".organization-store.lock"
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _consistent_catalog(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._catalog_thread_lock:
            if self._catalog_operation_depth:
                return method(self, *args, **kwargs)
            with _catalog_lock():
                signature = self._disk_signature()
                if signature != self._catalog_signature:
                    self._organizations = []
                    self._links = []
                    self._load()
                    self._rebuild_indexes()
                    self._catalog_signature = signature
                self._catalog_operation_depth += 1
                try:
                    return method(self, *args, **kwargs)
                finally:
                    self._catalog_operation_depth -= 1
                    self._catalog_signature = self._disk_signature()

    return call


def _atomic_catalog_json(path, payload):
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class OrganizationStorage:
    """组织架构存储服务"""

    def __init__(self):
        self._ensure_data_dir()
        self._catalog_thread_lock = threading.RLock()
        self._catalog_operation_depth = 0
        self._catalog_signature = None
        self._organizations: List[Organization] = []
        self._links: List[JobOrganizationLink] = []
        self._org_by_id: Dict[str, Organization] = {}
        self._children_by_parent: Dict[Optional[str], List[Organization]] = {}
        self._link_by_job: Dict[str, JobOrganizationLink] = {}
        self._jobs_by_org: Dict[str, List[str]] = {}
        with _catalog_lock():
            self._load()
            self._rebuild_indexes()
            self._catalog_signature = self._disk_signature()

    @staticmethod
    def _disk_signature():
        values = []
        for path in (ORG_FILE, LINKS_FILE):
            try:
                stat = path.stat()
                values.append((stat.st_ino, stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                values.append(None)
        return tuple(values)

    def _ensure_data_dir(self):
        """确保数据目录存在"""
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _load(self):
        """加载数据"""
        # 加载组织数据
        if ORG_FILE.exists():
            try:
                data = json.loads(ORG_FILE.read_text(encoding="utf-8-sig"))
                store = OrganizationStore(**data)
                self._organizations = store.organizations
                logger.info(f"Loaded {len(self._organizations)} organizations")
            except Exception as e:
                logger.error(f"Failed to load organizations: {e}")
                self._organizations = []

        # 加载关联数据
        if LINKS_FILE.exists():
            try:
                data = json.loads(LINKS_FILE.read_text(encoding="utf-8-sig"))
                self._links = [JobOrganizationLink(**link) for link in data.get("links", [])]
                logger.info(f"Loaded {len(self._links)} job-org links")
            except Exception as e:
                logger.error(f"Failed to load links: {e}")
                self._links = []

    def _save_organizations(self):
        """保存组织数据"""
        store = OrganizationStore(
            organizations=self._organizations, meta={"updated_at": __import__("time").time()}
        )
        _atomic_catalog_json(ORG_FILE, store.model_dump())

    def _save_links(self):
        """保存关联数据"""
        data = {
            "links": [link.model_dump() for link in self._links],
            "updated_at": __import__("time").time(),
        }
        _atomic_catalog_json(LINKS_FILE, data)

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

    @_consistent_catalog
    def get_all(self) -> List[Organization]:
        """获取所有组织"""
        return self._organizations.copy()

    @_consistent_catalog
    def get_by_id(self, org_id: str) -> Optional[Organization]:
        """根据ID获取组织"""
        return self._org_by_id.get(org_id)

    @_consistent_catalog
    def get_by_level(self, level: str) -> List[Organization]:
        """根据层级获取组织"""
        return [org for org in self._organizations if org.level == level]

    @_consistent_catalog
    def get_children(self, parent_id: Optional[str]) -> List[Organization]:
        """获取子组织"""
        return list(self._children_by_parent.get(parent_id, []))

    @_consistent_catalog
    def get_departments(self) -> List[Organization]:
        """获取所有部门（按名称排序）"""
        departments = [
            org for org in self._organizations if org.level == OrganizationLevel.DEPARTMENT
        ]
        return departments

    @_consistent_catalog
    def get_units_by_department(self, dept_id: str) -> List[Organization]:
        """获取指定部门下的直属单位（按名称排序）"""
        department = self.get_by_id(dept_id)
        if department is None or department.level != OrganizationLevel.DEPARTMENT:
            return []

        units = [org for org in self.get_children(dept_id) if org.level == OrganizationLevel.UNIT]
        return units

    @staticmethod
    def _catalog_errors(organizations: List[Organization]) -> List[str]:
        errors = list(validate_organization_hierarchy(organizations).errors)
        by_id = {org.id: org for org in organizations}
        identities = set()
        for org in organizations:
            key = (org.name.strip(), org.level, org.parent_id)
            if key in identities:
                errors.append(f"重复组织：{org.name}（{org.level}，上级 {org.parent_id or '无'}）")
            identities.add(key)
        # 错误必须能对应到页面上的名称，不能只让管理员看到内部编号。
        for org_id, org in by_id.items():
            pattern = rf"(?<![\w-]){re.escape(org_id)}(?![\w-])"
            errors = [
                re.sub(pattern, lambda match, _o=org, _i=org_id: f"{_o.name}[{_i}]", error)
                for error in errors
            ]
        return errors

    def _commit_organizations(self, organizations: List[Organization]) -> None:
        errors = self._catalog_errors(organizations)
        if errors:
            raise ValueError("组织层级校验失败：" + "；".join(errors))
        if organizations == self._organizations:
            return
        original = self._organizations
        self._organizations = organizations
        self._rebuild_indexes()
        try:
            self._save_organizations()
        except Exception:
            self._organizations = original
            self._rebuild_indexes()
            raise

    @_consistent_catalog
    def add(self, org: Organization) -> Organization:
        """添加组织"""
        # 检查是否已存在
        existing = self.get_by_id(org.id)
        if existing:
            raise ValueError(f"Organization with id {org.id} already exists")

        if not org.name.strip():
            raise ValueError("组织名称不能为空")
        self._commit_organizations([*self._organizations, org])
        return org

    @_consistent_catalog
    def update(self, org_id: str, updates: Dict[str, Any]) -> Optional[Organization]:
        """更新组织"""
        for i, org in enumerate(self._organizations):
            if org.id == org_id:
                org_dict = org.model_dump()
                org_dict.update(updates)
                # 改名、改层级、改上级都沿用原 ID，历史报告关联才能保持有效。
                org_dict["id"] = org.id
                org_dict["updated_at"] = __import__("time").time()
                updated = Organization(**org_dict)
                if not updated.name.strip():
                    raise ValueError("组织名称不能为空")
                candidate = self._organizations.copy()
                candidate[i] = updated
                self._commit_organizations(candidate)
                return updated
        return None

    @_consistent_catalog
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

    @_consistent_catalog
    def clear_all(self):
        """清空所有组织"""
        self._organizations = []
        self._links = []
        self._rebuild_indexes()
        self._save_organizations()
        self._save_links()

    # ==================== 组织树 ====================

    @_consistent_catalog
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
                issue_count=total_issues,
            )

        # 找到根节点（没有父组织的）
        roots = [org for org in self._organizations if org.parent_id is None]
        return [build_tree_node(root) for root in roots]

    # ==================== 任务关联 ====================

    @_consistent_catalog
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
            job_id=job_id, org_id=org_id, match_type=match_type, confidence=confidence
        )
        self._links.append(link)
        self._rebuild_indexes()
        self._save_links()
        return link

    @_consistent_catalog
    def get_job_org(self, job_id: str) -> Optional[JobOrganizationLink]:
        """获取任务的组织关联"""
        return self._link_by_job.get(job_id)

    @_consistent_catalog
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

    @_consistent_catalog
    def unlink_job(self, job_id: str) -> bool:
        """取消任务关联"""
        original_count = len(self._links)
        self._links = [link for link in self._links if link.job_id != job_id]
        if len(self._links) < original_count:
            self._rebuild_indexes()
            self._save_links()
            return True
        return False

    @_consistent_catalog
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

    @_consistent_catalog
    def import_from_list(
        self,
        data: List[Dict[str, Any]],
        clear_existing: bool = False,
        referenced_org_ids: Optional[List[str]] = None,
    ) -> ImportResult:
        """从列表导入组织，支持 `department/unit + parent` 模板。"""

        # 在独立候选目录中完成整批校验，任何错误都不能留下部分写入。
        candidate = [] if clear_existing else self._organizations.copy()
        result = ImportResult(success=False, total=len(data))
        expanded_rows: List[Dict[str, str]] = []
        for index, item in enumerate(data, start=2):
            if not isinstance(item, dict):
                result.errors.append(f"第 {index} 行不是有效组织记录")
                continue
            records = self._expand_import_item(item)
            if not records:
                result.errors.append(f"第 {index} 行缺少组织名称，或表头不符合导入模板")
            expanded_rows.extend(records)
        if not data:
            result.errors.append("文件没有可导入的组织记录")

        by_id = {org.id: org for org in candidate}
        original_by_identity: Dict[tuple, List[Organization]] = {}
        for org in self._organizations:
            original_by_identity.setdefault((org.name.strip(), org.level, org.parent_id), []).append(org)
        by_name: Dict[str, List[Organization]] = {}
        by_identity: Dict[tuple, List[Organization]] = {}
        for org in candidate:
            by_name.setdefault(org.name.strip(), []).append(org)
            by_identity.setdefault((org.name.strip(), org.level, org.parent_id), []).append(org)
        # 先建立全部上级，再绑定下级；否则同名上级若出现在后面的行，会被漏判为歧义。
        pending = sorted(expanded_rows, key=lambda row: LEVEL_ORDER.get(row.get("level", ""), 99))

        allowed_levels = {
            OrganizationLevel.CITY,
            OrganizationLevel.DISTRICT,
            OrganizationLevel.DEPARTMENT,
            OrganizationLevel.UNIT,
        }

        while pending:
            progressed = False
            next_pending: List[Dict[str, str]] = []

            for row in pending:
                name = row.get("name", "").strip()
                level = row.get("level", "").strip().lower()
                parent_name = row.get("parent_name", "").strip()
                code = row.get("code", "").strip()

                if not name:
                    result.errors.append("存在名称为空的组织记录")
                    result.skipped += 1
                    continue
                if level not in allowed_levels:
                    result.errors.append(f"组织“{name}”的层级“{level}”无效，请检查模板中的 level 列")
                    result.skipped += 1
                    continue

                parent_id = None
                if parent_name:
                    parents = [org for org in by_name.get(parent_name, [])
                               if (org.level == "department" if level == "unit"
                                   else LEVEL_ORDER[org.level] < LEVEL_ORDER[level])]
                    if not parents:
                        next_pending.append(row)
                        continue
                    if len(parents) > 1:
                        result.errors.append(f"上级组织“{parent_name}”存在多个同名候选，无法确定归属")
                        result.skipped += 1
                        continue
                    parent_id = parents[0].id
                elif level == "unit":
                    result.errors.append(f"单位“{name}”必须填写所属部门")
                    result.skipped += 1
                    continue

                # 旧目录的 ID 可能采用早期规则；按业务身份复用，不能重新生成同名节点。
                identity = (name, level, parent_id)
                existing = by_identity.get(identity, [])
                if len(existing) > 1:
                    result.errors.append(f"组织“{name}”在现有目录中重复，请先合并重复节点")
                    result.skipped += 1
                    continue
                if existing:
                    result.skipped += 1
                    progressed = True
                    continue

                originals = original_by_identity.get(identity, []) if clear_existing else []
                if len(originals) > 1:
                    result.errors.append(f"组织“{name}”存在多个历史身份，不能自动替换目录")
                    result.skipped += 1
                    continue
                # 覆盖导入相同组织时也要保留旧 ID，避免目录变了、历史材料仍指向旧 ID。
                org_id = originals[0].id if originals else Organization.generate_id(name, level, parent_id)
                if org_id in by_id:
                    result.errors.append(f"组织“{name}”的编号与现有不同组织冲突")
                    result.skipped += 1
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
                    result.errors.append(f"组织“{name}”的信息无效：{e}")
                    result.skipped += 1
                    continue

                candidate.append(org)
                by_id[org.id] = org
                by_name.setdefault(name, []).append(org)
                by_identity[identity] = [org]
                result.attempted += 1
                progressed = True

            pending = next_pending
            if not progressed:
                break

        for row in pending:
            result.errors.append(
                f"组织“{row['name']}”的父级未找到或层级不符合要求"
                f"（上级名称：{row['parent_name']}），整批未导入"
            )
            result.skipped += 1

        result.errors.extend(f"层级校验失败: {message}" for message in self._catalog_errors(candidate))
        references = {link.org_id for link in self._links} | set(referenced_org_ids or [])
        new_orphans = sorted((references & {org.id for org in self._organizations}) - set(by_id))
        if clear_existing and new_orphans:
            result.errors.append("覆盖目录会使已有报告关联失效（含材料引用），已取消导入；请保留现有组织编号：" + ", ".join(new_orphans[:50]))
        if result.errors:
            return result

        self._commit_organizations(candidate)
        result.imported = result.attempted
        result.success = True
        return result


# 单例实例
_storage_instance = None


def get_org_storage() -> OrganizationStorage:
    """获取组织存储单例"""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = OrganizationStorage()
    return _storage_instance
