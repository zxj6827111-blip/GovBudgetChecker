"""事务边界：全项目唯一的事务包装（WP1 与 WP3-A 共用）。

为什么必须有这一层
------------------
asyncpg 的连接池连接默认处于 **autocommit**：每条语句自成一个事务。
这在下面这种写法上不会报错，却会让行锁形同虚设：

    await conn.fetchrow("SELECT ... FROM t WHERE id = $1 FOR UPDATE", x)
    # ↑ 锁在**这一条语句**的事务结束时就被释放
    ... 后续读写完全不再受保护 ...

WP3-A 的一轮独立评审正是抓到了这一点：复核写路径里
``SELECT ... FOR UPDATE`` 拿到的槽位锁与会话锁在语句结束就释放，
于是"复核完成"与"版本指针推进"可以同时成功，产出
"review completed，但它复核的是已经被替换掉的那一版文件"这种状态——
而它看起来完全正常。

判定方法不是"看代码里有没有 FOR UPDATE"，而是**看锁的生命周期**：
锁必须活到业务操作结束。因此业务操作必须自带事务边界。

两条纪律
--------
1. **写路径自己开事务，不靠调用约定。** 同一条服务函数会被路由、真库用例、
   内部任务分别调用（``complete_review`` 就有三个调用方）。只要事务边界由
   调用方负责，就一定会有人忘——而忘记的后果是静默的并发缺陷，不是报错。
2. **已在事务中则复用外层事务，不再嵌套。** `transaction_scope` 在已有事务时
   直接让出，既不 BEGIN 也不 SAVEPOINT（详见其文档字符串）。需要"内层失败
   外层继续"的地方必须显式写 `conn.transaction()`——那才会产生保存点。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any


def in_transaction(conn: Any) -> bool:
    """连接是否已处于事务中。

    取不到状态时按"未开启"处理：多开一层事务不会破坏正确性，而
    "以为在事务里、其实不在"会让行锁在语句结束就释放，那才是真正的风险。
    因此不确定时选择更安全的一侧。
    """
    checker = getattr(conn, "is_in_transaction", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:  # pragma: no cover - 驱动实现差异的兜底
        return False


@asynccontextmanager
async def transaction_scope(conn: Any):
    """需要时开启事务；**已在事务中则直接复用外层事务，不创建保存点**。

    两种嵌套语义必须分清楚，它们不是同一件事：

    =====================================  ==================================
    写法                                    连接已在事务中时
    =====================================  ==================================
    ``transaction_scope(conn)``（本函数）    直接 ``yield``：复用外层事务，
                                            既不 BEGIN 也不 SAVEPOINT
    显式 ``conn.transaction()``              asyncpg 起一个 **SAVEPOINT**：
                                            内层抛错只回滚到该保存点，
                                            外层事务仍可继续
    =====================================  ==================================

    也就是说"嵌套会退化成 SAVEPOINT"只对**显式** ``conn.transaction()`` 成立。
    需要"内层失败但外层继续"的地方（例如复核服务处理
    ``uq_review_sessions_active`` 唯一冲突）必须显式写 ``conn.transaction()``，
    只包 ``transaction_scope`` 是拿不到部分回滚语义的。
    """
    if in_transaction(conn):
        yield
        return
    async with conn.transaction():
        yield


__all__ = ["in_transaction", "transaction_scope"]
