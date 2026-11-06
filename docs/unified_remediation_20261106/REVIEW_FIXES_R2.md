# 独立复核整改候选 R2

适用上海市既有材料与模板；上海市人民政府驻广州/驻西宁办事处属于上海机构。本轮不扩展其他地区规则。

已实现 R-01～R-05：叙述零的物理页/句段与断言边界、正式问题语义变更授权、独立事实原格/行列/单位绑定、非正式层逐对象裁决、上海驻外机构合法零读取。

效果策略 `real-painpoint-effect-v4`，新证据契约见 [effect_evidence_v2.schema.json](effect_evidence_v2.schema.json)。文件名保留 `scripts/effect_acceptance_v3.py`，避免破坏已有调用。v1历史包仍可只读载入；金额事实没有v2原格绑定时拒收。旧封印/旧代际授权不自动迁移。

v2决定 `issue_changed` 的 key 为旧问题的 `formal_identity`；`layer_object_changed` 的 key 为 `[layer, "id", object_id]` 或 `[layer, "content", issue_digest]`，分别消费添加/删除/修改的完整前后摘要。展示ID/次序不变化语义时无需裁决；重复对象身份拒收。每项仍须绑定完整 before/after source_binding、old_digest/new_digest、reason 与真实原件 sources。CLI SHA不是核验人身份认证。

独立事实 `source_cell` 明确 cell/row/column/table/unit_source 的页、引文与框，raw_value/raw_unit，以及固定schema；独立关系操作数身份固定其 source_cell_digest。表框涵盖所有定位，原格在行标与列头交点；格中唯一金额及单位换算须与 value_wan 完全相符。此约束不从被检查回执创建事实池，不将叙述零伪造为印刷金额。

治理比较可显式传 `--scope-review` 和 `--scope-review-sha256` 读取独立31条范围补充；冻结r4和旧meta不改。新增恢复后的范围仍需重新裁决，范围通过不代表业务通过。

本轮证据入口：`outputs/unified_review_remediation_20261106/整改说明.txt`、`ledger.json`、`validation.json` 与 `review_manifest.json`。保持 REQUEST_INDEPENDENT_REVIEW，不自签通过，不合入/推送main，不切换日常服务。B1、原争议、全预算/OCR/AI和真实Word排版仍单列未签收。
