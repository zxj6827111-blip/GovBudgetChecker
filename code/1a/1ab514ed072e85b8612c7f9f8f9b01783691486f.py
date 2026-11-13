# R5-01 真实 B1 列身份变异探针：只改 column，不改 quote/page/value/row/table。
import copy
import importlib.util
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
P = Path(os.environ.get("B1_PACKAGE", str(
    HERE.parent / "gold_b1_20261103/run_20261103T150928/revisions/round6_annotation_revision")))
spec_obj = importlib.util.spec_from_file_location(
    "dcol_r6", "/Volumes/workSSD/software development/gbc-worktrees/closeout-tools-r4-20261104/tools/closeout_r4/decimal_checks_v2.py")
dec = importlib.util.module_from_spec(spec_obj)
spec_obj.loader.exec_module(dec)
anns = {}
for path in (P / "annotations_revised").glob("*/annotation.json"):
    payload = json.loads(path.read_text())
    anns[payload["sample_id"]] = payload
spec = json.loads((P / "decimal_spec_v4.json").read_text())["rows"]
results = []
for item, column in [("2210201合计=基本+项目", "基本支出预算数"),
                     ("2140101行政运行", "非财政拨款收入")]:
    base = next(row for row in spec if row["item"] == item)
    mutant = copy.deepcopy(base)
    mutant["operands"][0]["column"] = column
    results.append({
        "item": item,
        "before_source": base["operands"][0],
        "after_source": mutant["operands"][0],
        "before": dec.evaluate(base, anns),
        "after": dec.evaluate(mutant, anns),
    })
out = HERE / "column_dimension_probes_r6.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps([{"item": r["item"],
                   "before_source_ok": r["before"]["source_ok"],
                   "after_source_ok": r["after"]["source_ok"],
                   "before_result": r["before"]["result"],
                   "after_result": r["after"]["result"],
                   "after_reasons": r["after"]["reasons"]} for r in results],
                 ensure_ascii=False, indent=2))
