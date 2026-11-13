# R4-02 真实 B1 来源变异探针：只替换目标字段，断言由 pytest 版本给出。
import copy, importlib.util, json, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
D = Path(os.environ.get("B1_PACKAGE", str(HERE / "B1_review_package")))
spec_obj = importlib.util.spec_from_file_location(
    "dprobe_r5", HERE / "tests/delivery_tools/decimal_checks_v2.py")
dec = importlib.util.module_from_spec(spec_obj)
spec_obj.loader.exec_module(dec)
anns = {}
for path in (D / "annotations_revised").glob("*/annotation.json"):
    payload = json.loads(path.read_text())
    anns[payload["sample_id"]] = payload
spec = json.loads((D / "decimal_spec_v4.json").read_text())
base = next(row for row in spec["rows"] if row["item"] == "2140101行政运行")
results = []
for name, field, value in [("wrong_subject_code", "row", "2140106 财政拨款收入"),
                           ("wrong_income_column", "column", "事业收入")]:
    row = copy.deepcopy(base)
    row["operands"][0][field] = value
    results.append({"name": name, "before": base["operands"][0],
                    "after": row["operands"][0],
                    "baseline": dec.evaluate(base, anns),
                    "mutant": dec.evaluate(row, anns)})
out = HERE / "real_decimal_source_probes_r5.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
print(json.dumps([{"name": r["name"], "baseline_source_ok": r["baseline"]["source_ok"],
                   "mutant_source_ok": r["mutant"]["source_ok"],
                   "baseline_result": r["baseline"]["result"],
                   "mutant_result": r["mutant"]["result"],
                   "mutant_reasons": r["mutant"]["reasons"]} for r in results],
                 ensure_ascii=False, indent=2))
