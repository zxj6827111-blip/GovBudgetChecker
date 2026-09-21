# 批次 C 第三轮独立复验（2026-09-16）

**结论：NO-GO，继续暂停点三。** 上轮两项运行状态错报已纠正，123.45 反例已拦截；剩余阻塞集中在 C01 生成器的数值等价判定和证据变化的未决处理。另有一处归档 SHA 陈旧。本轮没有发现新的业务规则回归，也不撤销 B 阶段验收。

## 已确认闭环

- 规资局 V33-220、曹杨 BUD-107 已分别登记业务判断与当前 insufficient_data，不再宣称运行时完成取数/单位识别。
- 修前 247、修后 253 的计数不变；在独立目录重生成账本，与交付 JSON 完全一致。
- 42 份 after 的 findings、summary、页数、PDF SHA 与首轮独立真实重跑逐份一致。
- manifest 的 key_file_fingerprints 全部匹配（含新增生成器/反例脚本）；嵌套 path/sha256 文件条目也全部匹配。例外是下文独立的 diff_review_sha256 字段。
- 旧重放脚本已委托新生成器，原先重复计数比较逻辑已移除。
- skip 节点、独立环境工具版本已纠正；拟提交清单继续保留。
- 本轮独立运行实施方生成器测试：**3 passed**；123.45 会拒绝配对并进入 pending_review。

本轮未重跑全量业务测试或 42 份 PDF 解析。沿用前轮 1240 passed/1 skipped、静态检查和 Golden 的通过证据，并核对当前关键代码指纹和 after 连续性；新增执行的是生成器重建、3 项测试和以下内存反例。下述改动仅发生在独立内存副本，不是对真实材料的金额结论。

## R3-P1-1：单位换算仍用宽固定容差，且该路径仍取首候选

位置：`scratch/build_rigorous_diff_review.py:37`、41～49、244～252。

`verify_unit_scaling_for_bud105` 使用 float 与固定 **0.05 万元**容差来确认“只是单位表示变化”。对于保留两位小数的万元显示，这相当于容许 500 元变化，远大于显示舍入所需范围。

**完整 42 份账本的独立反例：** 长寿路街道综合行政执法队 BUD-105，修前 T3=19971891.80 元，换算为 1997.18918 万元，应显示 1997.19。将修后内存副本改为 **1997.23**（其余保持），实际偏离换算值 **0.04082 万元，即 408.2 元**。生成器仍报告 **245 matched、pending_review=0**，将其确认为单位换算后保留。

这仍是上一轮“不能验证金额变化便判一致”的同一缺口，不能用成功拦截一个大额 123.45 反例代表完整修复。

另外，单位换算路径仍 `for ... break` 取首个通过候选。独立构造两个相同页码、相同换算值的修后候选，结果先确认一个 matched，另一个才记 pending；并没有把原始配对歧义本身保留待审。已有多候选测试未覆盖这个路径。

**最小整改：** 使用 Decimal 及已知序列化显示精度检验换算后的表示等价；不能确定单位/精度时 pending，不借用业务容差 0.05。必须验证预期金额字段集合完整，不能只验证交集。收集全部有效候选后再判断唯一性，同页多候选不得先挑一个。补充 1997.23 和单位换算路径的重复候选测试，同时保留真实 14 条换算配对的核验。

## R3-P1-2：证据实际改变仍不进入待审，删除证据被自动解释为展示差异

位置：`scratch/build_rigorous_diff_review.py:95`、99、`make_matched_entry` 返回值以及 pending_review_items 汇总。

- `b_ev` 非空、`a_ev` 为空，就自动断言“旧展示层回填 message”，没有检验旧证据是否真的等于回填内容或来源于该路径。
- 双方非空且不同时虽然标记 evidence_changed，但最终仍是 matched_and_retained；没有进入 pending_review_items。

**完整账本内存反例：** 选取真实 BUD-111 非空原文证据，保持规则、message、页码不变：

| 修改 | 生成器结果 |
|---|---|
| 将原文证据替换为“原文金额已改变为999999” | evidence_changed，但仍 245 matched、pending=0 |
| 将原文证据删除为空字符串 | 被解释为 representation_difference，仍 245 matched、pending=0 |

这不表示当前交付的 32 条已确定存在证据回归，而是证明生成器尚不能区分“已核实的展示差异”与“尚未裁决的实质证据变化”。当前 pending=0 仍不能作为完整裁决依据。

**最小整改：** 用可核实的回填条件或逐项审定记录确认 representation_difference，不能仅凭空/非空判断；其余 evidence_changed/severity_changed 必须带明确裁决或计入未决。业务对象配对成功与差异已裁决是两件事，可以保留 matched 的关联关系，但不能因此自动将变化标为已确认保留。所有未决类型需参与动态统计，附来源及原因。

## R3-P2-1：manifest 内同一账本有两个不同指纹

当前 diff_review.json 实际 SHA：

`72bd296ee1b250ab3b69f78c2108c2b782616eac0eaca62043b874b0951c0b3a`

key_file_fingerprints 和 handoff 已正确更新，但 `manifest.excluded_evidence_archive.diff_review_sha256`（约第 667 行）仍是 R2 的：

`e2b78302165dad81847914449d61ca55ae78d2e802e1ccf3f3e94e440684e8c8`

统一由同一实际文件计算所有引用，并在最终生成后做跨字段一致性检查。不是代码被修改的证据，而是归档闭环缺口。

## 复验返回条件与范围

仅需修正 C01 生成器/相关反例、重新生成账本及同步报告和指纹。允许保留真实未决，不要求 pending=0，不要求改业务规则以让 deferred 消失。保留已经通过的状态分账、计数、提交清单等成果，不重复返工。

本轮未修改业务源码、Golden、原始 PDF 或正式 C 交付物；未 commit、push、部署或访问 NAS。独立证据：`outputs/batch_c_independent_review_r3_20260916/review.py`、`review.json`、`review.log`、`regenerated_diff.json`、`pytest_generator.log`。其中 `full_corpus_probes` 记录完整账本的三项内存变体，`probes` 记录最小构造例。
