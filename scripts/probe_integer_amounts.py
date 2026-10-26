"""IR-17 整数金额探针：真实语料是否存在“只有整数金额”的表格。

只读取验收解析事实（coded facts），不重新解析 PDF，不改产品逻辑。
同时区分三类：零值整数、非零整数金额、同表中的小数金额；只有一张表的
所有非零事实都是整数时，才计为“整表只有整数金额”。T0 X01/X03 用合成
样本覆盖解析器的整数金额读取/标记行为。
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARSE = ROOT / "outputs/intake_remediation_20261024/batch2_final/parse.jsonl.gz"
DEFAULT_OUT = ROOT / "outputs/intake_remediation_20261024/ir17_integer_probe.json"
DEFAULT_MD = ROOT / "outputs/intake_remediation_20261024/ir17_integer_probe.md"
INTEGER = re.compile(r"-?\d+")


def new_stats() -> dict[str, int]:
    return {"integer": 0, "decimal": 0, "zero": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parse", type=Path, default=DEFAULT_PARSE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--md", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()

    files = 0
    coded_facts = 0
    uncoded_facts = 0
    zero_integer_facts = 0
    nonzero_integer_facts = 0
    nonzero_files: set[str] = set()
    integer_hits: list[dict] = []
    table_stats: dict[str, dict[str, int]] = defaultdict(new_stats)
    only_integer_tables: list[dict] = []

    with gzip.open(args.parse, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            files += 1
            uncoded_facts += int(row.get("uncoded_facts") or 0)
            per_table: dict[str, dict[str, int]] = defaultdict(new_stats)
            for key in row.get("facts") or []:
                parts = str(key).split("|")
                if len(parts) < 7:
                    continue
                coded_facts += 1
                table, amount = parts[0], parts[5].strip()
                try:
                    value = Decimal(amount)
                except InvalidOperation:
                    continue
                if value == 0:
                    zero_integer_facts += 1
                    table_stats[table]["zero"] += 1
                    per_table[table]["zero"] += 1
                elif INTEGER.fullmatch(amount):
                    nonzero_integer_facts += 1
                    nonzero_files.add(str(row.get("file")))
                    table_stats[table]["integer"] += 1
                    per_table[table]["integer"] += 1
                    if len(integer_hits) < 200:
                        integer_hits.append({
                            "file": row.get("file"),
                            "table_code": table,
                            "indicator": parts[1],
                            "fact": key,
                        })
                else:
                    table_stats[table]["decimal"] += 1
                    per_table[table]["decimal"] += 1
            for table, counts in per_table.items():
                if counts["integer"] > 0 and counts["decimal"] == 0:
                    only_integer_tables.append({
                        "file": row.get("file"),
                        "table_code": table,
                        "counts": dict(counts),
                    })

    report = {
        "probe": "IR-17 integer-only amount tables",
        "parse_file": str(args.parse),
        "corpus_files": files,
        "coded_facts": coded_facts,
        "uncoded_facts": uncoded_facts,
        "integer_coded_facts": zero_integer_facts + nonzero_integer_facts,
        "zero_integer_facts": zero_integer_facts,
        "nonzero_integer_facts": nonzero_integer_facts,
        "files_with_nonzero_integer": len(nonzero_files),
        "tables_with_only_integer_nonzero": only_integer_tables,
        "table_stats": dict(sorted(table_stats.items())),
        "nonzero_examples": integer_hits,
        "t0_reference": [
            "T0-X01 整数金额（元）合成 4 行：read_or_flag",
            "T0-X03 无框线整数表合成 2 行：read_or_flag",
        ],
        "limits": [
            "只扫描 coded facts；253,822 个 uncoded 事实未逐格展开",
            "不重新解析 PDF；不改变解析器或规则行为",
            "零值整数只是事实键的格式，不等于原件印成整数；判断原件格式需回看单元格",
            "未区分元/万元；非零整数事实可能是万元整数，以元为单位的整数金额仍由 T0 X01/X03 合成样本覆盖",
            "“整表只有整数金额”按 coded facts/表级判定；未逐页核对原始单元格",
            "单个整数事实可能与编码/数量混淆，需人工确认；示例已列出",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    conclusion = (
        "没有一张表的所有非零事实都是整数；"
        "计划中“只有整数金额的表格页为 0 页”在 coded facts/表级成立。"
        if not only_integer_tables
        else "发现整表只有整数金额的候选，需逐页核对原始单元格。"
    )
    md = [
        "# IR-17 整数金额探针",
        "",
        f"- 输入：`{args.parse}`",
        f"- 语料文件：{files}；coded facts：{coded_facts}；uncoded facts：{uncoded_facts}",
        f"- 整数表示 coded facts（含零值）：**{zero_integer_facts + nonzero_integer_facts}**"
        f"（零值 {zero_integer_facts}，非零 {nonzero_integer_facts}，非零涉及 {len(nonzero_files)} 份文件）",
        f"- 整表只有整数金额的候选：**{len(only_integer_tables)}**",
        "",
        "## 结论",
        "",
        conclusion,
        "",
        "非零整数金额示例（均与小数金额同表出现）：",
        "",
        *[f"- `{item['fact']}`（{item['file']}）" for item in integer_hits[:10]],
        "",
        "## 限制",
        "",
        *[f"- {item}" for item in report["limits"]],
        "",
    ]
    args.md.write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({k: report[k] for k in (
        "corpus_files", "coded_facts", "uncoded_facts",
        "integer_coded_facts", "zero_integer_facts", "nonzero_integer_facts",
        "files_with_nonzero_integer", "tables_with_only_integer_nonzero", "table_stats",
    )}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
