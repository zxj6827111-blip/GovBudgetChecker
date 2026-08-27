#!/usr/bin/env python
"""执行数据库迁移并校验幂等性（Task 15.2 / 缺口 P2-03）。

为什么要单独一个脚本
--------------------
CI 此前只有 lint / type / unit / build / e2e，**没有迁移步骤**：
`src/db/migrations.py` 里的 DDL 只在有人手工跑或服务启动时才执行，
迁移写错（重复建索引、缺 IF NOT EXISTS、约束冲突）要到部署当天才暴露。

做两件事：
1. 对 `DATABASE_URL` 指向的库跑一遍全部迁移；
2. **再跑一遍**，断言第二遍没有新增迁移记录 —— 这是迁移幂等性的机器证明。
   PLAN 要求"构建 + 迁移 + E2E 全链"，幂等是回滚与重复部署的前提。

用法：
    DATABASE_URL=postgresql://... python scripts/run_db_migrations.py
    python scripts/run_db_migrations.py --allow-missing   # 未配置 DATABASE_URL 时跳过
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.db.connection import DatabaseConnection  # noqa: E402
from src.db.migrations import (  # noqa: E402
    MIGRATIONS,
    ensure_migrations_table,
    get_applied_migrations,
    run_migrations,
)


async def _applied_ids() -> set:
    pool = await DatabaseConnection.get_pool()
    schema = DatabaseConnection.get_schema()
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{schema}", public')
        await ensure_migrations_table(conn, schema)
        return await get_applied_migrations(conn, schema)


async def _run() -> int:
    await DatabaseConnection.initialize()
    try:
        before = await _applied_ids()
        await run_migrations()
        after_first = await _applied_ids()
        await run_migrations()
        after_second = await _applied_ids()
    finally:
        await DatabaseConnection.close()

    defined = {migration["id"] for migration in MIGRATIONS}
    newly_applied = sorted(after_first - before)
    second_pass_extra = sorted(after_second - after_first)
    missing = sorted(defined - after_first)

    print(f"已定义迁移：{len(defined)} 个")
    print(f"本次新增应用：{len(newly_applied)} 个 {newly_applied[:10]}")
    print(f"第二遍新增应用：{len(second_pass_extra)} 个（幂等要求为 0）")
    if missing:
        print(f"FAIL: 迁移未全部落库，缺失 {missing}", file=sys.stderr)
        return 1
    if second_pass_extra:
        print(f"FAIL: 迁移不幂等，第二遍又应用了 {second_pass_extra}", file=sys.stderr)
        return 1
    print("PASS: 全部迁移已落库且重复执行幂等")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="执行数据库迁移并校验幂等性")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="未配置 DATABASE_URL 时打印跳过原因并以 0 退出",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    database_url = str(os.getenv("DATABASE_URL", "") or "").strip()
    if not database_url:
        message = "未配置 DATABASE_URL，无法执行迁移"
        if args.allow_missing:
            print(f"SKIP: {message}")
            return 0
        print(f"FAIL: {message}", file=sys.stderr)
        return 2
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
