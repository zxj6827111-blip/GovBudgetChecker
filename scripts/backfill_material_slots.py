#!/usr/bin/env python
"""材料槽位历史回填盘点（默认 dry-run，不写任何数据）。

用法::

    python scripts/backfill_material_slots.py                     # 打印摘要
    python scripts/backfill_material_slots.py --json              # 输出 JSON
    python scripts/backfill_material_slots.py --write out.json    # 写入 JSON 文件
    python scripts/backfill_material_slots.py --markdown out.md   # 写入 Markdown 报告
    python scripts/backfill_material_slots.py --upload-root PATH  # 指定 uploads 目录

为什么默认是 dry-run
--------------------
历史任务元数据质量参差：缺组织 id、年份没认出来、文种互相矛盾都会出现。
直接回填等于把这些不确定性一次性固化成"系统认定的历史事实"，
之后比没有数据更难纠正。因此本脚本先把"会回填成什么样"完整报出来，
由人看过之后再决定是否执行。

本脚本**不具备**写入能力：它调用的 ``scan_upload_root`` 是纯读函数。
回填写入属于独立变更，需要单独评审。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.material_slot_backfill import (  # noqa: E402
    render_markdown,
    scan_upload_root,
)


def resolve_upload_root(explicit: str | None) -> Path:
    """确定要盘点的 uploads 目录。

    与运行时代码用同一个解析口径（``UPLOAD_DIR`` 环境变量，默认仓库下 ``uploads/``），
    避免"盘点的是 A 目录、线上写的是 B 目录"这种让人白跑一轮的错配。
    """
    if explicit:
        return Path(explicit).resolve()
    return Path(os.getenv("UPLOAD_DIR", "uploads")).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="材料槽位历史回填盘点（dry-run，不写库）"
    )
    parser.add_argument("--upload-root", help="要盘点的 uploads 目录，默认取 UPLOAD_DIR")
    parser.add_argument("--json", action="store_true", help="把 JSON 报告打到标准输出")
    parser.add_argument("--write", metavar="PATH", help="把 JSON 报告写入文件")
    parser.add_argument("--markdown", metavar="PATH", help="把 Markdown 报告写入文件")
    parser.add_argument(
        "--include-items",
        action="store_true",
        help="JSON 里包含逐条明细（默认只给汇总与样例）",
    )
    args = parser.parse_args()

    upload_root = resolve_upload_root(args.upload_root)
    report = scan_upload_root(upload_root)

    if args.write:
        Path(args.write).write_text(
            json.dumps(report.to_dict(include_items=args.include_items), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.markdown:
        Path(args.markdown).write_text(
            render_markdown(report, upload_root=str(upload_root)), encoding="utf-8"
        )

    if args.json:
        print(json.dumps(report.to_dict(include_items=args.include_items), ensure_ascii=False, indent=2))
    else:
        print("== 材料槽位历史回填盘点（dry-run，未写入任何数据）==")
        print(f"扫描目录: {upload_root}")
        print(f"任务目录数: {report.scanned_jobs}（其中带校验和 {report.jobs_with_document}）")
        print(f"预计生成槽位: {report.projected_slots} / 版本绑定: {report.projected_versions}")
        if report.ambiguous_same_name:
            print(f"同名部门/单位歧义: {report.ambiguous_same_name} 条")
        print("\n-- 分桶 --")
        for bucket, count in sorted(report.buckets.items(), key=lambda kv: -kv[1]):
            print(f"  {bucket}: {count}")
        print("\n-- 未自动映射原因 --")
        for reason, count in sorted(report.reasons.items(), key=lambda kv: -kv[1]):
            label = report.reason_labels.get(reason, reason)
            print(f"  {reason}: {count}  ({label})")
        if not report.reasons.get("ok"):
            print("\n没有任何任务可自动映射，属于异常，请先检查组织目录是否可读。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
