# 统一修复专项候选（2026-11-06）

本候选基于 main `47581360966191d180880f7fb07dab48974948bb`。当前不是独立签收或发布版本。

## 工具职责与入口

- 治理：`scripts/verify_pending_review.py`，工具版本 `unified-governance-v1-candidate`；复用保存提交 `3ca22f88` 的 r4 保护及测试，不整体导入 r5。
- 适配：`scripts/adapt_acceptance_collection.py SOURCE NEW_OUT`；版本 `review-report-adapter-v2`。旧采集只读，缺字段与空列表区分；保留正式、候选、提示、待核与原始问题层。
- 效果：`scripts/effect_acceptance_v3.py --baseline B --candidate C --kit K --out NEW_OUT [--evidence E --evidence-sha256 SHA] [--repeat C2]`。
- r4 原契约只读复用副本在 `contracts/pending_review_r4`。采集包另有原件清单、环境与哈希封印，必须显式指定 `--kit`；此目录不是完整采集包。

治理 self 比较验证同版本一致性。历史 DH 阶段比较限定于“只增诊断、不变业务”的旧契约，不能拿来否定业务修复；业务效果变化由 v3 对具体对象的独立依据裁决。两者不得互相代签。

## 独立证据包

`schema=independent-effect-evidence-v1`。`samples[].binding` 精确匹配新记录的 `source_binding`（材料、主体、年度、文种、分析代际、代码、规则、配置）；`facts[]` 保存独立核对的 fact_id、value_wan、unit、year、原页、引文、bbox、file_hash、relation_keys。来源与待核回执分离，不能从 operand_fact_ids/required_fact_ids 构造事实全集。

支持的直接复算限于有完整独立数值的加总。其它公式、空白解释及 N/A 必须有 `relation_proofs`：key、完整 receipt_digest、conclusion、reason、sources。未实现公式不自动放行。

问题增删、证据替换及合理收严使用 `decisions`：action、before、after、key、old_digest、new_digest、reason、sources；绑定对象完整内容，不能同规则不同材料/关系/版本借用。action 为 remove_issue / add_issue / evidence_replaced / justified_regression。

SHA256 防止核验输入漂移，不是核验人的身份认证。独立真值与签收由核验人提供；本工具不会从系统输出自动生成真值或批准文件。

## 业务边界

原件已为空的格不生成零值。已确认列头、行、原格但金额为空时，登记 cell_blank_semantics_unknown/human_confirm；真缺列、歧义或技术缺陷仍保留相应状态。部分子关系通过不能掩盖规则摘要中的未完成。来源诊断组明确列出原有关系成员，不虚构新的金额算式；成员缺失仍待核。

## 发布门禁

专项台账、全部原始及新证据位于同一工作区 `outputs/unified_remediation_20261106`。只有独立复核签收后才能合入 main；服务切换另做版本与真实流程验收。B1、原7争议、预算/OCR/AI能力仍单独待验收，不以本专项单元测试通过代替。
