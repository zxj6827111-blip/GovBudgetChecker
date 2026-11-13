from pathlib import Path
import csv
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent

rows = [
    ("RC00", "P0", "critical", "后端规则语法错误阻断导入", "original_worktree", "src/engine/budget_rules.py", 3340, "同调用参数唯一，API/Worker可导入", "重复check_relation_id/fact_ids/evidence_ids，65/8个pytest收集错误", "删除重复参数组并在原源码执行全量验收"),
    ("RC13", "P2", "low", "门禁脚本Optional未导入", "original_static", "scripts/run_zc07_acceptance.py", 599, "Ruff通过", "F821 Undefined name Optional", "导入Optional或使用联合类型注解"),
    ("RC01", "P1", "high", "风险合计被可用明细替代", "original_worktree", "src/engine/fact_context.py", 145, "风险合计100应blocked", "选verified明细50，status=ok", "先确定所需角色，再判该角色状态"),
    ("RC06", "P1", "high", "调整后预算进入年初预算比较", "original_selector_and_diagnostic_consumer", "src/engine/fact_context.py", 99, "年初预算过滤排除调整后", "阶段子串匹配预算，调整后11与年初10比较报错", "阶段精确枚举，关系独立声明口径"),
    ("RC01/RC04/RC10", "P1", "high", "默认输出丢事实证据关系身份", "static_and_diagnostic_pipeline", "src/engine/pipeline.py", 387, "同一事实版本贯通问题和回执", "legacy丢IDs，V33-103/104/214作者缺IDs，issue_refs空", "统一两个输出通道并填写作者端身份"),
    ("RC04", "P1", "high", "旧表回退却标事实完成", "diagnostic_rule_contract", "src/engine/common_rules.py", 924, "回执如实标实际来源与操作数", "合法空FactContext/旧表通过，8回执operand[]却fiscal_fact/complete", "正规物化回退操作数或标legacy/pending"),
    ("RC04", "P1", "high", "公车内部勾稽缺关系回执", "static_and_diagnostic_rule", "src/engine/common_rules.py", 1006, "每个适用关系有状态及发现链接", "小计不等购置加运行有Issue但无对应receipt/关系ID", "为正反缺值分支生成专属回执"),
    ("RC10", "P1", "high", "局部列校正删除其他事实", "original_native_pdf_and_diagnostic_route", "src/engine/fact_context.py", 486, "仅修改收入列并保留支出", "合法income_actual列1校正后4事实变2收入事实，applied1/unresolved[]", "在完整有效映射上增量覆盖指定列"),
    ("RC10/RC12", "P1", "high", "后续复核决定覆盖历史", "diagnostic_actual_routes_four_exports", "src/services/issue_workflow_store.py", 988, "后续决定不改变旧版本已作决定/摘要", "100 confirmed→90消失→110 no_issue，100历史四格式confirmed消失且SHA改变", "按revision及问题身份追加事件，历史使用对应快照"),
    ("RC03", "P1", "high", "短异主体金额误绑定", "diagnostic_budget_rule", "src/engine/budget_rules.py", 1277, "乙单位/乙局的金额不进入当前主体", "213012被用于本单位，产生两条误报", "物化章节/主体/年度/阶段叙述事实并精确绑定"),
    ("RC02", "P1", "high", "预算旧续页拼入独立采购页", "diagnostic_budget_rule", "src/engine/budget_rules.py", 595, "独立表不得作为续页", "目标11行被拼成14行", "旧取表路径共享硬身份边界或退出旧拼接"),
    ("RC08", "P2", "medium", "缓存原始物理片段不完整", "original_memory_sql_protocol", "src/services/parse_artifact_store.py", 460, "原物理片段表头和网格原样保存", "第二页重复表头被删，恢复仅合计200但header_row_count仍1", "完整不可变保存物理片段，另建逻辑视图；真实PG再验收"),
    ("RC07", "P1", "manual_review", "可信扫描自动操作数未完成", "original_orchestration_with_model_stub", "src/services/ocr_fact_promotion.py", 329, "可信扫描可安全晋升操作数", "当前全部OCR low_confidence，operand_count0，摘要已诚实", "逐格置信及关键数字复验，真实CPU扫描盲测"),
    ("RC13", "P1", "high", "门禁部分分母缺失和冲突回执被忽略", "original_synthetic_gate", "scripts/run_zc07_acceptance.py", 732, "缺任一冻结分母不可计算，冲突回执不通过", "部分文档无分母仍1.0；pass后execution_error仍1.0；部分定位真值缺失仍1.0", "逐文档验证全集；冲突裁决失败；缺定位真值不得缩分母"),
    ("RC13", "P1", "high", "重复运行遗漏引用和实际阈值", "original_synthetic_gate", "scripts/run_zc07_acceptance.py", 989, "完整证据和关系语义相同才repeatable", "同源配置版本，quote变更或容差0.5→99仍true", "签名纳入完整引用和实际计算字段"),
    ("RC13", "P1", "high", "四格式评测不绑定问题行内容", "original_synthetic_gate_with_nonempty_positive", "scripts/run_zc07_acceptance.py", 1312, "按问题ID比较对应可见语义", "Word/PDF两问题message互换仍export_agreement1.0", "按ID逐行核对，指纹不能替代内容认证"),
    ("RC14/RC15", "P1", "manual_review", "冻结数据及真实服务链缺证据", "original_preflight", "scripts/run_zc07_acceptance.py", 1645, "开发32+盲测32/100缺陷组及四场景实跑", "source-hash verified0；Docker/测试DB/OCR模型缺，预检exit2", "隔离真实服务及冻结独立业务验收"),
    ("RC06", "P1", "high", "决算预算指标合法阶段被排除", "diagnostic_semantic_fact_contract_only", "src/engine/common_rules.py", 541, "按预算/决算列分别接受阶段", "年初预算31被排除，旧表30与说明30回退pass；错误标决算才fail", "物化及过滤按列语义，不统一按文种赋阶段"),
    ("RC04", "P2", "low", "阻断原因展开成字符列表", "static_and_diagnostic_rule", "src/engine/rules_v33.py", 5284, "完整可读的原因字符串", "V33-214状态已未完成，但原因逐字符", "每个原因保存为完整列表项"),
    ("RC10", "P1", "high", "校正依赖不认识实际回执键", "static_and_diagnostic_route", "src/services/correction_overlay_store.py", 122, "按operand_fact_ids识别受影响检查", "实际回执有ID但校正affected_checks=unknown", "统一依赖字段，保留实际全量重算生效的行为"),
    ("RC11", "P1", "high", "AI统一契约及逐关系隔离未接管", "static_and_diagnostic_fault_injection", "src/services/ai_candidate_contract.py", 41, "入口逐项验完整契约，关系异常独立留回执", "校验器未被生产调用；故障仍整批退出合并，AI候选安全fallback已修", "入口接统一契约，坏关系失败、好关系继续，保持候选边界"),
]

issues = []
for index, (rc, priority, severity, title, boundary, source, line, expected, actual, suggestion) in enumerate(rows, 1):
    issues.append({
        "id": f"R4-{index:02d}", "rule_id": rc, "issue_type": "system_acceptance_review",
        "priority": priority, "severity": severity, "title": title,
        "document_type": None, "year": None, "organization": None,
        "page": None, "bbox": None, "table_or_section": source,
        "source_file": str(ROOT / source), "source_line": line,
        "snippet": actual, "expected": expected, "actual": actual, "difference": None,
        "reason": title, "suggestion": suggestion,
        "confidence": 1.0, "evidence_boundary": boundary,
        "status": "pending_real_environment" if severity == "manual_review" else "open",
        "production_acceptance_pass": False,
    })
snapshot = json.loads((OUT / "source_snapshot_before.json").read_text())
test_results = {}
for path in sorted(OUT.glob("*_run.json")):
    test_results[path.stem] = json.loads(path.read_text())
artifact_hashes = {name: hashlib.sha256((OUT / name).read_bytes()).hexdigest() for name in (
    "parse_audit.json", "rules_audit.json", "evidence_audit.json", "root_gate_probes.json",
    "source_snapshot_before.json", "source_snapshot_after.json", "zc07_preflight.json", "deployment_probe.json",
)}
payload = {
    "schema_version": "govbudgetchecker.independent_system_review.v1",
    "summary": {
        "date": "2026-10-04", "round": 4, "head": snapshot["head"], "branch": snapshot["branch"],
        "working_tree_dirty": True, "diff_sha256": snapshot["diff_sha256"], "modified_source_files": 26,
        "overall_assessment": "未通过：原工作树后端SyntaxError，代码化余项及真实业务验收仍未完成",
        "engineering_acceptance": "not_passed", "business_acceptance": "not_passed",
        "document_type": None, "year": None, "organization": None,
        "issue_count": len(issues), "precision": None, "recall": None, "exact_location_rate": None,
        "evidence_authenticity_rate": None, "required_check_completion_rate": None,
        "risk_flags": ["生产导入阻断", "关系/事实身份不足", "部分诊断依赖语法副本", "真实冻结数据和运行证据缺失"],
    },
    "issues": [x for x in issues if x["severity"] != "manual_review"],
    "manual_review": [x for x in issues if x["severity"] == "manual_review"],
    "test_results": test_results, "artifact_sha256": artifact_hashes,
    "limitations": ["没有修改业务代码", "未读取.env/.mimosa/保留盲测原件", "未连接真实DB或云AI", "诊断副本结果不得替代生产通过", "合成1.0不是业务指标"],
    "next_actions": ["修语法并原源码全量回归", "统一角色阶段和增量列映射", "身份/关系/决定版本完整保留", "收口AI和门禁反例", "冻结真实数据及PG/API/Worker/OCR独立验收"],
}
(OUT / "findings.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
columns = ["id", "rule_id", "priority", "severity", "title", "status", "document_type", "year", "organization", "page", "bbox", "source_file", "source_line", "expected", "actual", "suggestion", "evidence_boundary", "confidence", "production_acceptance_pass"]
with (OUT / "findings.csv").open("w", encoding="utf-8-sig", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(issues)
print(json.dumps({"issues": len(issues), "manual_review": len(payload["manual_review"]), "artifact_count": len(artifact_hashes)}, ensure_ascii=False))
