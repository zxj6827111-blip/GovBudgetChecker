#!/usr/bin/env python
"""检查覆盖基线报告：把"应检查的事情"与"当前完成到哪一步"固化成可比对的产物。

为什么需要这个脚本
------------------
plan 批次 A 要求"基线可复现，计数和证据没有歧义"，批次 B 起每批都要拿
"已知严重漏报是否检出、剩余未完成是否如实保留"来验收。这两件事都需要一个
**不依赖某次运行环境**的基线：相同的源码 + 相同的清单版本，必须打印出相同的
计数。因此本脚本刻意只用静态信息（画像解析器版本、义务清单版本与指纹、
逐文种的规则注册表指纹、未实现缺口清单）加逐文种的理论覆盖台账，
不读 PDF、不连数据库、不调用 AI。

它同时承担 plan §3 的一条纪律：**另报自动完成率和人工复核量**。
`coverage_rate` 是"应检查事项里完成了几成"，`by_group` 让每个业务分组
（表间关系、表文关系、三公经费……）的完成情况单独可见——避免用一组的
好成绩掩盖另一组的失效，也避免"全部转人工"被当成高准确率。

用法::

    python scripts/check_coverage_baseline.py                 # 打印基线
    python scripts/check_coverage_baseline.py --json          # 输出 JSON
    python scripts/check_coverage_baseline.py --write out.json
    python scripts/check_coverage_baseline.py --assert-gaps 7 # 缺口数变化即失败

退出码：0 通过；1 门禁未满足（缺口数变化或清单自检失败）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.check_obligations import (  # noqa: E402
    OBLIGATION_CATALOG_VERSION,
    build_obligation_ledger,
    catalog_fingerprint,
    catalog_gaps,
    registered_rule_ids,
    validate_catalog,
)
from src.schemas.document_profile import PROFILE_RESOLVER_VERSION  # noqa: E402

#: 基线覆盖的文种。政府级报告尚未验收，先不列入——列出它会给出一个
#: 看起来"已支持"的假信号（plan 批次 E 才扩展报告类型）。
BASELINE_REPORT_KINDS = ("budget", "final")

#: 除计数外，还必须有内容的锚点，防止台账被改成一个恒空结构而基线仍"通过"。
REQUIRED_COVERAGE_KEYS = (
    "catalog_version",
    "catalog_fingerprint",
    "applicable_total",
    "completed_total",
    "unresolved_total",
    "blocking_total",
    "coverage_rate",
    "by_reason",
    "by_group",
    "instances",
)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _compact_instances(ledger: Dict[str, Any]) -> List[Dict[str, Any]]:
    """逐实例的紧凑投影：编号、分组、状态、原因、依据。

    基线要能复核"计数是从哪来的"，因此保留逐条状态；但不需要把证据字段
    全量抄进来（依赖输入、checker 列表等可从清单指纹侧推出）。
    """
    return [
        {
            "obligation_id": item["obligation_id"],
            "group_id": item["group_id"],
            "status": item["status"],
            "reason": item["reason"],
            "blocks_gate": item["blocks_gate"],
        }
        for item in ledger["instances"]
    ]


def _structural_ceiling(ledger: Dict[str, Any]) -> Dict[str, Any]:
    """制度性完成率上限：把所有"已实现的检查都做完"能到多少。

    这不是当前完成率，而是**当前能力**的上界——分母里有几项根本还没有实现，
    任何材料都不可能让它们完成。把它单独报出来，是为了让"完成率低"这件事
    可以归因：是这次没查完，还是这块能力本来就没有。
    """
    applicable = int(ledger.get("applicable_total") or 0)
    gap = int((ledger.get("by_reason") or {}).get("not_implemented") or 0)
    if not applicable:
        return {"gap_obligation_total": gap, "ceiling_rate": None}
    return {
        "gap_obligation_total": gap,
        "implemented_obligation_total": applicable - gap,
        "ceiling_rate": round((applicable - gap) / applicable, 4),
    }


def _git_revision() -> Dict[str, str]:
    """记录源码基线。取不到就留空，不编造 revision。"""
    revision: Dict[str, str] = {"commit": "", "branch": "", "dirty": ""}
    for key, args in (
        ("commit", ["rev-parse", "HEAD"]),
        ("branch", ["rev-parse", "--abbrev-ref", "HEAD"]),
    ):
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            revision[key] = result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            revision[key] = ""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        revision["dirty"] = "true" if result.stdout.strip() else "false"
    except Exception:
        revision["dirty"] = ""
    return revision


def _registry_fingerprint(kind: str) -> Dict[str, Any]:
    codes = sorted(registered_rule_ids(kind))
    return {"count": len(codes), "fingerprint": _digest("\n".join(codes))}


def build_baseline() -> Dict[str, Any]:
    kinds: Dict[str, Any] = {}
    for kind in BASELINE_REPORT_KINDS:
        ledger = build_obligation_ledger(None, report_kind=kind)
        kinds[kind] = {
            "rule_registry": _registry_fingerprint(kind),
            "structural_ceiling": _structural_ceiling(ledger),
            "coverage": {
                key: ledger[key]
                for key in (
                    "catalog_version",
                    "catalog_fingerprint",
                    "applicable_total",
                    "completed_total",
                    "not_applicable_total",
                    "unresolved_total",
                    "blocking_total",
                    "coverage_rate",
                    "auto_completion_rate",
                    "by_reason",
                    "by_reason_labels",
                    "by_group",
                )
            }
            | {"instances": _compact_instances(ledger)},
        }
    return {
        "baseline_version": 1,
        "source": _git_revision(),
        "profile_resolver_version": PROFILE_RESOLVER_VERSION,
        "obligation_catalog_version": OBLIGATION_CATALOG_VERSION,
        "obligation_catalog_fingerprint": catalog_fingerprint(),
        "catalog_self_check": validate_catalog(),
        "unimplemented_checks": catalog_gaps(),
        "kinds": kinds,
    }


def _assert_baseline_shape(baseline: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    if baseline["catalog_self_check"]:
        problems.extend(f"清单自检失败：{item}" for item in baseline["catalog_self_check"])
    if not baseline["obligation_catalog_fingerprint"]:
        problems.append("缺少义务清单指纹")
    for kind, payload in baseline["kinds"].items():
        if not payload["rule_registry"]["count"]:
            problems.append(f"{kind}: 规则注册表为空，基线不可信")
        missing = [
            key for key in REQUIRED_COVERAGE_KEYS if key not in payload["coverage"]
        ]
        if missing:
            problems.append(f"{kind}: 覆盖台账缺少字段 {missing}")
        if not payload["coverage"].get("applicable_total"):
            problems.append(f"{kind}: 应检查事项为 0，说明台账没有正确展开")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="检查覆盖基线报告")
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认打印摘要）")
    parser.add_argument("--write", metavar="PATH", help="把基线写入文件")
    parser.add_argument(
        "--assert-gaps",
        type=int,
        metavar="N",
        help="断言未实现检查数等于 N，不等则以退出码 1 失败（发布门槛用）",
    )
    args = parser.parse_args()

    baseline = build_baseline()
    problems = _assert_baseline_shape(baseline)

    if args.write:
        Path(args.write).write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if args.json:
        print(json.dumps(baseline, ensure_ascii=False, indent=2))
    else:
        print("== 检查覆盖基线 ==")
        source = baseline["source"]
        print(f"源码: {source.get('branch')}@{source.get('commit')} dirty={source.get('dirty')}")
        print(
            f"画像解析器: {baseline['profile_resolver_version']} | "
            f"义务清单: {baseline['obligation_catalog_version']} "
            f"({baseline['obligation_catalog_fingerprint']})"
        )
        gaps = baseline["unimplemented_checks"]
        print(f"尚未实现的检查要求: {len(gaps)} 项")
        for item in gaps:
            print(f"  - [{item['report_kinds']}] {item['obligation_id']}: {item['gap_note']}")
        for kind, payload in baseline["kinds"].items():
            coverage = payload["coverage"]
            registry = payload["rule_registry"]
            ceiling = payload["structural_ceiling"]
            print(f"\n-- {kind} --")
            print(f"  规则注册表: {registry['count']} 条 ({registry['fingerprint']})")
            print(
                f"  应检查 {coverage['applicable_total']} 项 / "
                f"不适用 {coverage['not_applicable_total']} 项 / "
                f"未完成 {coverage['unresolved_total']} 项（其中阻塞 {coverage['blocking_total']} 项）"
            )
            print(
                "  制度性完成率上限 "
                f"{ceiling['ceiling_rate']}（{ceiling['gap_obligation_total']} 项尚无实现，"
                "任何材料都无法让它们完成）"
            )
            labels = coverage["by_reason_labels"]
            for code, count in sorted(
                coverage["by_reason"].items(), key=lambda kv: -kv[1]
            ):
                print(f"    {labels.get(code, code)}: {count}")
            for group in coverage["by_group"]:
                print(
                    f"    [{group['group_title']}] 应查 {group['applicable']} / "
                    f"完成 {group['completed']} / 未完成 {group['unresolved']}"
                )

    if problems:
        print("\n== 基线问题 ==", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1

    if args.assert_gaps is not None and len(baseline["unimplemented_checks"]) != args.assert_gaps:
        print(
            f"门禁失败：未实现检查数 {len(baseline['unimplemented_checks'])} "
            f"不等于期望的 {args.assert_gaps}。缺口增减必须显式更新基线，"
            "避免能力变化悄悄发生。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
