"""材料访问范围与共享的数据库会话入口（材料台账 / 复核共用）。

为什么抽出来
------------
``MaterialAccessScope`` 原本长在 ``api/routes/materials.py`` 里。WP3-A 新增的复核
接口的授权目标是**槽位**，与材料详情完全同一套判定（可见组织 OR 谓词、容器与
数据目标分离、"不存在 404 / 越权 403"）。复制一份实现是做不到长期一致的：
两处只要有一处漏掉一个组织列，就会出现"台账里看得到、复核里 403"或反过来的
越权口子；而漏掉的那一处永远不会报错。

因此这里做的是**搬运 + 提炼**，不是重写：

- 判定逻辑逐行照搬，行为一字不改（WP2 的全部权限用例必须继续通过）；
- ``with_connection`` 也搬过来，让两个路由文件共用"取连接 / 503 口径 / 归还"。

一条不可让步的纪律
------------------
**容器可打开 ≠ 容器下数据可见。** 页面容器（区县/部门）放开是为了让只授权了
下级节点的账号还能导航；数据目标（单位、槽位）必须自己就在可见范围内，
否则"过度拒绝"会一步变成"越权泄露"。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, List, Optional

from fastapi import HTTPException

from api import runtime
from api.auth_utils import user_can_access_org
from src.db.connection import DatabaseConnection

logger = logging.getLogger(__name__)


def is_admin(user: Any) -> bool:
    try:
        return bool(user.get("is_admin"))
    except AttributeError:  # pragma: no cover - 用户记录始终是 dict
        return False


class MaterialAccessScope:
    """当前账号在材料台账上的访问范围。

    两个集合，**职责严格分开**（这是本轮最重要的一条设计）：

    ``visible_org_ids``
        可以读到哪些**数据**。判定复用仓库既有的 ``user_can_access_org``
        （已授权节点可访问其后代），本模块不重新实现祖先/后代判断。
        ``None`` = 管理员，不加权限过滤。

    ``container_org_ids``
        可以打开哪些**页面容器**。= 可见组织的**祖先闭包**。
        用途只有一个：让"只授权了 dept-A / unit-A1"的账号仍能打开其所属区县或
        部门页面做导航（否则他们连自己的材料都点不进去）。

    容器可打开 ≠ 容器下数据可见：页面里的每一行仍然由 ``visible_org_ids``
    在 SQL 层过滤。只放开容器而不加范围过滤，会从"过度拒绝"直接变成"越权泄露"。
    """

    __slots__ = ("visible_org_ids", "container_org_ids")

    def __init__(
        self,
        *,
        visible_org_ids: Optional[List[str]] = None,
        container_org_ids: Optional[set] = None,
    ) -> None:
        self.visible_org_ids = visible_org_ids
        self.container_org_ids = container_org_ids or set()

    @property
    def unrestricted(self) -> bool:
        return self.visible_org_ids is None

    @property
    def has_any_visible_org(self) -> bool:
        return bool(self.visible_org_ids)

    def can_open_container(self, org_id: str) -> bool:
        """能否打开该组织对应的页面（管理员恒可）。"""
        if self.unrestricted:
            return True
        return str(org_id or "") in self.container_org_ids


def build_access_scope(user: Any) -> MaterialAccessScope:
    """按既有 RBAC 语义算出可见范围与容器范围。

    - 管理员：``visible_org_ids=None``，不加任何权限条件；
    - 非管理员：遍历组织目录，凡 ``user_can_access_org`` 为真的节点都是可见节点
      （层级不限：区县、部门、单位一视同仁）。这样 department / unit 授权不会被
      错误折算成"必须能访问区县"，也不会把单位授权放大成整个部门；
    - 组织目录不可用：返回空可见集合（fail-closed，宁可看不到也不越权）。
    """
    if is_admin(user):
        return MaterialAccessScope(visible_org_ids=None)

    try:
        storage = runtime.require_org_storage()
    except Exception:  # noqa: BLE001 - 组织目录不可用时按"无授权"处理（fail-closed）
        logger.warning("Organization catalog unavailable while resolving material scope")
        return MaterialAccessScope(visible_org_ids=[])

    records = list(storage.get_all())
    by_id = {str(getattr(org, "id", "") or ""): org for org in records}

    visible: List[str] = []
    for org in records:
        org_id = str(getattr(org, "id", "") or "")
        if org_id and user_can_access_org(user, org_id):
            visible.append(org_id)

    # 容器集合 = 可见节点的祖先闭包（含可见节点自身）。
    containers = set(visible)
    for org_id in visible:
        current = by_id.get(org_id)
        seen: set = set()
        while current is not None:
            parent_id = str(getattr(current, "parent_id", "") or "")
            if not parent_id or parent_id in seen:
                break
            seen.add(parent_id)
            containers.add(parent_id)
            current = by_id.get(parent_id)

    return MaterialAccessScope(visible_org_ids=visible, container_org_ids=containers)


def require_container_access(scope: MaterialAccessScope, org_id: str, detail: str) -> None:
    if not scope.can_open_container(org_id):
        raise HTTPException(status_code=403, detail=detail)


def require_unit_access(scope: MaterialAccessScope, unit_id: str, detail: str) -> None:
    """单位时间轴的授权：``unit_id`` 本身必须在**可见数据范围**内。

    这里**刻意不用** ``can_open_container``：单位是数据目标，不是导航容器。
    容器的语义是"授权了它的下级就能打开它"，用在单位上会把
    "只授权了隔壁单位"的账号放进来（容器闭包是向上取的，单位本身不在自己的
    闭包里，看起来恰好安全；但只要将来有人把容器算法改成向下闭包，
    就会静默变成越权）。用可见范围判定则与 SQL 的过滤完全同源。
    """
    if scope.unrestricted:
        return
    if str(unit_id or "") not in set(scope.visible_org_ids or []):
        raise HTTPException(status_code=403, detail=detail)


def slot_row_is_visible(row: Any, scope: MaterialAccessScope) -> bool:
    """槽位行是否落在当前账号的可见范围内（管理员恒真）。

    判定按槽位的三个组织列 **OR**：区县、主管部门、主体任一命中即可见。
    与 SQL 里的权限谓词逐字同源 —— 两处各写一套必然出现
    "列表里看得到、点进去 403"（或反过来，那更糟）。

    抽取成公开纯函数是为了让"越权反例"能直接用行数据断言，
    不必先起一个 FastAPI 测试客户端。
    """
    if scope.unrestricted:
        return True
    visible = set(scope.visible_org_ids or [])
    if not visible:
        # 没有任何可见组织：fail-closed，宁可看不到也不越权。
        return False
    for column in ("jurisdiction_org_id", "department_org_id", "subject_org_id"):
        value = str((row or {}).get(column) or "").strip()
        if value and value in visible:
            return True
    return False


def require_slot_access(
    scope: MaterialAccessScope, row: Optional[dict], slot_id: str
) -> dict:
    """槽位级授权：详情、版本、处理记录、**复核**四类接口共用同一个入口。

    三条硬要求：

    1. **不存在与无权不能混为一谈** —— 不存在 404，越权 403；
    2. **不能只保护页面入口** —— 直接带一个别人的槽位 UUID 请求详情、
       ``/versions``、``/runs``、``/reviews`` 都必须 403（IDOR 反例）；
    3. **只能有一处判定** —— 各处各写一份必然漂移，而漏掉的那一个
       就是数据泄露口。
    """
    if row is None:
        raise HTTPException(status_code=404, detail="material slot not found")
    if not slot_row_is_visible(row, scope):
        raise HTTPException(status_code=403, detail="slot access denied")
    return row


# ---- 共享的数据库会话 --------------------------------------------------------


async def open_connection() -> Any:
    """获取一条数据库连接（测试可替换）。"""
    return await DatabaseConnection.acquire()


async def close_connection(conn: Any) -> None:
    await DatabaseConnection.release(conn)


async def with_connection(
    callback: Callable[[Any], Awaitable[Any]],
    *,
    opener: Optional[Callable[[], Awaitable[Any]]] = None,
    closer: Optional[Callable[[Any], Awaitable[None]]] = None,
) -> Any:
    """取一条连接执行 ``callback(conn)``，无论成败都归还。

    把"取连接 / 503 口径 / 归还"收在一处：材料台账与复核接口共用同一条失败语义，
    不会出现"一个接口 503、另一个 500"。

    数据库没起来是可恢复的运维状态，不是"这个接口不存在"，也不该伪装成空数据 ——
    503 让"查不到"与"连不上"在调用方那里保持可分辨。

    ``opener`` / ``closer`` 允许调用方传入自己的取连接函数。存在的理由是既有的
    WP2 用例通过替换**路由模块**上的 ``_open_connection`` 来注入假连接
    （接口契约因此能在无真库时被完整验证）。让调用方显式传入，比在共享模块里
    反向引用某个路由模块干净，也不会把"测试替身"固化进共享层。
    """
    _open = opener or open_connection
    _close = closer or close_connection
    try:
        conn = await _open()
    except Exception as exc:  # noqa: BLE001 - 连接失败的形态由驱动决定
        logger.warning("Material database unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="material ledger database unavailable") from exc
    try:
        return await callback(conn)
    finally:
        await _close(conn)


__all__ = [
    "MaterialAccessScope",
    "is_admin",
    "build_access_scope",
    "require_container_access",
    "require_unit_access",
    "slot_row_is_visible",
    "require_slot_access",
    "open_connection",
    "close_connection",
    "with_connection",
]
