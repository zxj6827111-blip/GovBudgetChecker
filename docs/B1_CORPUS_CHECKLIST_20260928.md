# B1 第一期基准语料清单（S2 签字稿）

> 状态：**待签字**。签署本清单即确认 30 份语料的构成与配比，S2 收灌随即启动。
> 配比设计依据：docs/WP4-I_BENCHMARK_PLAN.md §2.1–§2.4（30±5 份、干净对照 ≥1/3、
> 锚定 70% / 泛化探针 30%、扫描观察集 ≤3 不进主指标）。
> 登记纪律：每份登记 sha256（重复源拒绝）、来源 URL、子集标记；PDF 永不入 git；
> 金标纪律——L2 标注必须在系统输出生成前完成。

## 一、既有资产 7 份（PDF 均已在本机定位，签署后即登记）

| # | 材料 | 源 PDF SHA-256（前 12 位） | 位置 | 拟登记子集 | 文种真值 |
|---|---|---|---|---|---|
| 1 | 生态环境局 2025 年度部门决算（golden DOC-20260905-001） | `113b98bb5df1` | corpus + uploads/3f44e78b… | final-main | final |
| 2 | 宜川路街道 2025 年度部门决算（Y02 真值样张） | `f809eef2afa9` | uploads | final-main | final |
| 3 | 石泉路街道 2025 年度部门决算 | `e8315830f800` | uploads | final-main | final |
| 4 | 文化和旅游局 2025 年度部门决算 | `74e6afd06669` | uploads | final-main | final |
| 5 | 建设和管理委员会 2026 年度部门预算公开 | `73528f0b78d1` | uploads/864d06b2… | budget-main | budget |
| 6 | 城市管理行政执法局 2026 年度部门预算公开 | `c56f4368be5d` | uploads/debb635a… | budget-main | budget |
| 7 | 文化和旅游局 26 年度部门预算 | 待登记时计算 | uploads/ef67c3fa… | budget-main | budget |

备注： uploads 中另有建管委/城管执法局 2026 **单位预算**两份
（0b3e393a… / a69cfe01…），可作 clean-contrast 或替补位，签署时决定是否入集。
4 份决算的解析产物已冻结为 tests/fixtures（源 SHA 双锁），登记后评测可直接复用。

## 二、待采集 23 份（配比与采集要求）

| 维度 | 配额 | 说明 |
|---|---|---|
| 决算 final-main | +14（合计 18） | 区级委办局 ≥60%、街道/乡镇 ≥20%、其余区本级/事业单位 |
| 预算 budget-main | +6（合计 9） | 同上结构 |
| 干净对照 clean-contrast | 主集 27 份中 ≥9 份 | 无问题或问题很少（误报率分母的健康样本） |
| 泛化探针 probe-region | ≈9 份（外省 2–3 省市） | 单独分层报告，不与锚定混算 |
| 扫描观察 scan-watch | ≤3 份 | 不进主指标（可读性闸门转人工路径的观察面） |
| 年度配比（决算） | 2023 年度 3–4 / 2024 年度 ≈10 / 2025 年度 4–5 | 同比类规则的跨年切片 |
| 年度配比（预算） | 2025 年度 ≈6 / 2026 年度 3 | 同上 |
| 版式难点 | ≥5 份 | 跨页断裂/续页盲行/合并单元格错位/目录页码偏移 |

每份采集要求：政府网站公开材料或用户确认有权使用的内部评审版；**来源 URL 必填**；
文本层字符量探测通过（纯扫描件归入 scan-watch）；页数 15–60 为宜。

## 三、签署后即执行的收灌动作（S1 工具已就绪）

```bash
# 每份材料一条登记命令（示例）：
python scripts/bench_register.py --pdf <材料.pdf> \
    --doc-id DOC-B1-001 --report-kind-true final --subset final-main \
    --region anchor --source-url <URL>
# 既有 7 份用 --doc-id 复用原编号（示例）：
python scripts/bench_register.py --pdf corpus/DOC-20260905-001/sample.pdf \
    --doc-id DOC-20260905-001 --report-kind-true final --subset final-main
python scripts/run_benchmark.py     # 全量纯规则重放（确定性，禁 AI）
```

## 四、签字

- [ ] 确认 30 份构成与上表配比（或批注修订）
- [ ] 确认 2 份单位预算替补位是否入集
- [ ] 确认 23 份待采集位的采集责任方与时间

签字后：S2 收灌 → S3 盲标（L1 全量 30 份 / L2 深标 20 份，先于系统输出）→
S4 评测（MR 修复节点前后对比）→ S5《基准报告》。
