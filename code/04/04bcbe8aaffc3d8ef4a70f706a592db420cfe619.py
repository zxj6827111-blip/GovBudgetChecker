"""离线检查实验产物；已知锚点不冒充完整人工真值。"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[1]


def read(name):
    return json.loads((OUT/name).read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


manifest=read("manifest.json")
supplement=read("review_supplement_manifest.json")
frozen={**manifest["files"],**supplement["files"]}
assert all(sha(ROOT/path)==expected for path,expected in frozen.items())
assert sha(OUT/"manifest.json")==supplement["original_manifest_sha256"]
results=[json.loads(p.read_text()) for p in sorted(OUT.glob("result_*.json"))]
assert len(results)==8
assert len(list(OUT.glob("started_*.json")))==8
assert all(r["retry_count"]==0 for r in results)
assert sum(r["status"]=="ok" for r in results)==7
for model in ["gpt","ds"]:
    for task in ["controls1","controls2"]:
        result=read(f"result_{model}_{task}.json")
        assert result["status"]=="ok"
        answers=result["parsed"]["results"]
        assert len(answers)==len({x["id"] for x in answers})==24
        truth={x["id"]:x["expected"] for x in read("controlled_truth.json")}
        assert {x["id"]:x["state"] for x in answers}==truth

mapping={x["id"]:x for x in read("review_calibrated_mapping.json")}
positive_indices={"gpt61sol.json":{1,2,6,7}, "deepseek_v41_flash_64k.json":{1,2,9,10}}
reviews={}
for model in ["gpt","ds"]:
    data=read(f"result_{model}_review.json")
    answers=data["parsed"]["verdicts"]
    assert len(answers)==len({x["id"] for x in answers})==30
    by_id={x["id"]:x for x in answers}
    assert set(by_id)==set(mapping)
    known=[iid for iid,m in mapping.items() if m["index"] in positive_indices.get(m["source"],set())]
    assert len(known)==8
    assert all(by_id[iid]["verdict"]=="confirmed_defect" for iid in known)
    controls=read("review_control_truth.json")
    assert all(by_id[c["id"]]["verdict"]==c["expected"] for c in controls)
    assert by_id["R9883"]["verdict"]=="needs_original"
    assert by_id["R9883"]["pages"]==[7]
    assert by_id["R2231"]["verdict"]=="rounding_hint"
    assert by_id["R8207"]["verdict"] in {"parsing_risk","needs_original"}
    reviews[model]=dict(known_positive_entries_preserved=8,known_unique_issues_preserved=4,
        injected_false_claims_rejected=4,heading=by_id["R9883"],rounding=by_id["R2231"],
        elapsed_s=data["elapsed_s"],states=dict(Counter(x["verdict"] for x in answers)))

pages=json.loads((ROOT/"tests/fixtures/sample_page_data.json").read_text())["page_texts"]
discover=read("result_ds_discover.json")["parsed"]["findings"]
anchors=read("real_text_anchors.json")[:4]
anchor_matches={a["id"]:[f["id"] for f in discover if f["state"]=="defect" and f["page"]==a["page"]
    and re.sub(r"\s+","",a["text"]) in re.sub(r"\s+","",f["quote"])] for a in anchors}
assert all(anchor_matches.values())
literal=[];normalized=[]
for f in discover:
    page=pages[f["page"]-1]
    if f["quote"] in page:literal.append(f["id"])
    else:
        assert re.sub(r"\s+","",f["quote"]) in re.sub(r"\s+","",page)
        normalized.append(f["id"])

# 按表17中12个预算/决算显示值核对出国费预算，表头也保留在证据文件中。
amount_line=next(line for line in pages[16].splitlines() if line.startswith("21.00 16.95"))
amounts=[Decimal(x) for x in amount_line.split()]
assert len(amounts)==12 and amounts[2]==0 and amounts[3]==0
zero=next(f for f in discover if f["id"]=="F10")
assert zero["quote"] in pages[25] and "完成预算" in zero["quote"]
body=re.sub(r"\s+","", "".join(pages[20:25]))
budget_values=[Decimal(x.replace(",","")) for x in re.findall(r"年初预算为([\d,]+(?:\.\d+)?)万元",body)]
assert len(budget_values)==21
detail_sum=sum(budget_values[1:],Decimal("0"))
assert detail_sum==Decimal("4628.16")

gpt_by={x["id"]:x for x in read("result_gpt_review.json")["parsed"]["verdicts"]}
ds_by={x["id"]:x for x in read("result_ds_review.json")["parsed"]["verdicts"]}
disagreements=[dict(id=i,source=mapping[i],gpt=gpt_by[i],deepseek=ds_by[i])
    for i in mapping if gpt_by[i]["verdict"]!=ds_by[i]["verdict"]]
verified=dict(
    scope="1份完整提取文本+24个受控文本案例；未新增人工盲审PDF真值，不能据此给总体精确率/召回率或生产就绪结论",
    frozen_inputs_verified=True,requests=8,completed=7,failed=[dict(model=r["model_key"],task=r["task"],http_status=r.get("http_status")) for r in results if r["status"]!="ok"],
    controls=dict(unique_cases=24,repetitions_per_model=2,gpt=[24,24],deepseek=[24,24],note="重复案例不算48个独立样本"),
    reviews=reviews,review_disagreements=disagreements,
    deepseek_discovery=dict(candidates=len(discover),model_declared_states=dict(Counter(x["state"] for x in discover)),
        known_text_anchor_matches=anchor_matches,literal_page_quotes=len(literal),whitespace_only_page_quotes=normalized,
        zero_budget_candidate=dict(finding_id="F10",budget_page=17,budget_amount_wan=str(amounts[2]),
            final_amount_wan=str(amounts[3]),narrative_page=26,narrative_quote=zero["quote"],
            assessment="提取文本和表内显示值支持零分母完成率候选；与PDF视觉核验分开"),
        additional_numeric_checks=dict(narrative_budget_total_wan=str(budget_values[0]),
            narrative_20_item_sum_wan=str(detail_sum),public_expense_class_sum_wan=str(Decimal("401.93")+Decimal("14.44"))),
        unresolved=["F13关于本单位称谓的主体语境仍需复核，不自动承认为新增真缺陷", "标题编号、表格文字错序等需要原PDF页面", "未对14条候选完成独立人工全集裁定，不能计算材料总体精确率"]),
    production_settings_unchanged=sha(ROOT/"data/ai-model-settings.json")==manifest["production_settings_sha256"],
    limitations=["GPT high参数经既有中转请求，底层实际执行无法独立认证", "GPT全文独立审查524，缺少同题成功结果，不能比较两模型全文召回", "本轮有提示词、规则输入和上下文组织变化，不能单独归因于推理档位", "30条复核候选包含双方重复问题，候选条数不是唯一问题数"],
    script_sha256={p.name:sha(p) for p in [OUT/"experiment.py",Path(__file__).resolve()]})
assert verified["production_settings_unchanged"]
(OUT/"verified_evidence.json").write_text(json.dumps(verified,ensure_ascii=False,indent=2))
print(json.dumps(dict(verified=True,requests=8,completed=7,controls=[24,24],
    false_claims_rejected_each=4,known_unique_issues_preserved_each=4,
    quoted_findings=len(literal)+len(normalized),production_settings_unchanged=True),ensure_ascii=False))
