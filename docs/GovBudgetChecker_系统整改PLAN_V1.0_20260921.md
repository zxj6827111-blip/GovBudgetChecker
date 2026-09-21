# GovBudgetChecker 产品化与准确性系统整改 PLAN
**版本**：V1.0  
**日期**：2026-09-21  
**GitHub 基线**：`main @ 4a1cabf2be3fa6e05e9820a9546ee192634871e4`  
**目标**：把当前“可运行的预算/决算审查原型”整改为“可用于真实业务的材料台账 + 审核辅助软件”，同时系统性降低误报、漏报和“未检查却显示完成”的风险。

---

## 0. 结论先行

本轮不建议继续以“修某个页面、补某条规则”的方式零散推进。当前仓库已经具备较完整的基础能力：组织树、上传预检、PDF 预览、审核工作台、规则引擎、检查义务台账、结构化解析、Golden 回放、导出归档等；但“产品对象”和“质量闭环”仍未统一。

最终产品应围绕一个核心对象重构：

> **材料槽位（Material Slot） = 地区 + 主管部门/单位 + 财政年度 + 文种（预算/决算） + 主体层级/口径**

PDF 是槽位下的文件版本，分析任务是文件版本下的运行记录，问题与人工复核又是分析运行下的业务结果。

这样才能同时解决：
1. 上海 16 区、多主管部门、多下属单位、多年度、多预算/决算的查找和统计；
2. “未到期 / 逾期未上传 / 已上传 / 有问题 / 待复核 / 完成”的真实状态；
3. 同名“部门”和“本部单位”混淆；
4. 一个 PDF 多次上传、多版本、多次分析时历史串线；
5. 当前任务历史只能回答“跑过哪些 job”，不能回答“哪些材料应该有、哪些没有”；
6. 规则未实现、取数不足、解析歧义不能再被包装成“检查通过”。

---

# 1. 当前仓库基线与已确认问题

## 1.1 当前产品结构

当前 `app/app/components/workspace/nav.ts` 一级导航仍是：

- 工作台总览
- 上传中心
- 处理队列
- 审核工作台
- 任务历史
- 导出归档
- 质量管理
- 规则与版本
- 系统设置

其中 `HistoryPage.tsx` 的定义仍是“终态任务检索与报告下载”，数据源为 `/api/jobs`，本质上是 Job 历史，不是业务材料台账。

`OrganizationFilterSelect.tsx` 目前把组织树拉平为下拉选项。组织模型 `src/schemas/organization.py` 定义为：

> `city -> district -> department -> unit`

该结构适合内部组织层级，但不适合在数百个单位的日常查找场景中作为唯一导航方式。

## 1.2 当前已经具备、应保留的能力

以下能力不应推倒重写，而应成为整改后的底座：

- 上传预检识别年度、文种、组织；
- `AttributionWizardPanel` 已区分 `department_summary` 与 `unit`；
- `DocumentProfile` 已统一 `report_year / report_kind / subject_level / caliber`；
- `fiscal_documents` 与 `fiscal_document_versions` 已有文档/版本意识；
- 审核工作台已有 PDF 常驻三栏、问题确认/忽略/备注；
- `evidence_guard.is_formal_finding` 已经区分正式问题与证据降级问题；
- `check_obligations.py` 已建立检查义务台账，避免“规则不存在就不进入分母”；
- Golden replay/evaluate、结构化解析、CI 业务门禁已有基础；
- 导出归档已有问题确认 -> 整改包链路。

整改重点是**统一这些能力的业务对象和状态机**。

## 1.3 当前准确性问题仍未闭环

仓库 `docs/COVERAGE_LEDGER_REMEDIATION_20260916.md` 已明确记录：

- 仍存在 **8 项 `pending_checkers` 实现缺口**；
- 三份真实样张复验中仍有 **38 次 `insufficient_data`**；
- 19 组人工确认问题里仍有 **14 组漏报**；
- 每份材料仍可能存在 **15 项阻塞义务**；
- “把缺口记账”已经完成，但“把检查真正做出来”尚未完成。

当前 8 项明确未实现义务：

1. `OBL-CROSS-SAN-GONG-ECON`：三公经费表与经济分类表的跨表资金来源一致性；
2. `OBL-TXT-FUND-DETAIL`：政府性基金/国资表与说明的逐项金额比对；
3. `OBL-NARRATIVE-INDICATOR-REPEAT`：文内同一指标多处披露一致性；
4. `OBL-TREND-ZERO-BASE`：零基数增长率复算；
5. `OBL-TREND-COMPLETION-RATE`：预算完成率分母及复算；
6. `OBL-DISCLOSURE-PERCENT-UNIT`：占比缺百分号等表达完整性；
7. `OBL-SG-COMPLETION`：三公经费预算数/决算数与说明一致性；
8. `OBL-PERF-PHASE-AMOUNT`：绩效阶段金额、项目集合、预算版本口径校验。

`docs/AUDIT_QUALITY_REMEDIATION_20260905.md` 同时仍记录以下未完成项：

- V33-117/120 的结构化消费迁移；
- 生产管线切换；
- Golden Corpus 扩容；
- top30 人工裁决；
- 真实 AI 预发验证。

因此当前代码“测试很多、门禁很多”并不等于已经达到真实业务可用标准。

## 1.4 当前审核生命周期仍不完整

`ReviewWorkbenchPage.tsx` 文件顶部已经明确写明：

> 后端没有“标记任务复核完成”的端点。

现在“完成复核”只是在前端确认 pending=0 后跳回处理队列，并不会形成真正持久化的“审核已完成”状态。

这会导致：
- 后续无法可靠统计人工复核完成率；
- 重新加载后无法判断材料是否正式复核结案；
- 导出归档与人工结论之间缺少严格状态约束；
- 检查义务的人工补核也没有持久化出口。

这是生产化前必须补齐的 P0。

---

# 2. 最终产品信息架构

最终一级导航建议固定为：

1. **工作台总览**
2. **上传中心**
3. **处理队列**
4. **审核工作台**
5. **材料台账**
6. **导出归档**
7. 质量管理（管理员）
8. 规则与版本（管理员）
9. 系统设置（管理员）

原“任务历史”不再作为一级业务入口。

任务历史保留为：
- 材料详情 -> 处理记录；
- 或系统设置/运维 -> 任务运行历史。

## 2.1 材料台账层级

最终交互层级：

> 上海市总览  
> -> 区县  
> -> 主管部门  
> -> 部门汇总 / 本部 / 直属单位  
> -> 财政年度 × 预算/决算  
> -> 具体材料详情

但是前台**不画成深层文件夹树**，采用：
- 市级：区县卡片；
- 区级：主管部门矩阵；
- 部门级：主体 × 预算/决算矩阵；
- 单位级：多年度预算/决算时间轴；
- 材料级：详情页 Tabs。

## 2.2 两种找材料方式

### 管理式查找
材料台账 -> 普陀区 -> 规划和自然资源局 -> 本部 -> 2024 -> 决算。

### 搜索式查找
全局搜索框输入：

> 规划和自然资源局 本部 2024 决算

直接进入材料详情。

两种方式必须共存。

---

# 3. 核心数据模型整改

## 3.1 新增 `material_slots`（P0）

这是本轮最重要的数据模型。

建议字段：

```text
id UUID PK
jurisdiction_id
department_org_id
subject_org_id
subject_level            # department / unit / government
material_scope           # department_summary / unit_self / government
fiscal_year
report_kind              # budget / final
caliber                  # summary / self / unknown
applicability_status     # applicable / not_applicable / unresolved
due_at                   # 应收/公开截止时间
expected_source_url
status                   # derived 或缓存
current_document_version_id
created_at
updated_at
```

唯一键建议至少覆盖：

```text
(subject_org_id, material_scope, fiscal_year, report_kind, caliber)
```

**注意：部门和本部单位即使名称相同，必须使用不同组织 ID。**

## 3.2 新增 `material_sources`

用于解决政府网站来源、发布日期与财政年度混淆：

```text
id
slot_id
source_url
source_page_title
source_site
published_at
discovered_at
last_checked_at
source_page_hash
status
```

系统要严格区分：

- `fiscal_year = 2024`
- `published_at = 2025-08-20`

不能按网页发布时间推导财政年度。

## 3.3 文档版本归属槽位

现有 `fiscal_document_versions` 应补充或通过映射表关联：

```text
slot_id
original_filename
file_hash
storage_key
storage_backend
ingested_at
source_id
is_current
```

一个材料槽位允许：

> v1 PDF -> v2 PDF -> v3 PDF

每个 PDF 版本又可以：

> 分析运行 A -> 重新分析 B -> 规则升级后的分析 C

“文件版本”和“分析版本”必须是两个概念。

## 3.4 Analysis Job 降为运行记录

`/api/jobs` 继续存在，但职责只用于：

- 处理队列；
- 重试；
- 阶段日志；
- 失败定位；
- 历史分析记录。

Job 不再是用户查找历史材料的主入口。

## 3.5 组织模型不要继续平行扩张

当前仓库已经同时存在多套组织相关存储/表：
- `src/schemas/organization.py`
- 数据库 `organizations`
- `org_units`
- 共享模型中的 `org_department / org_unit`

本轮不应再造第四套组织主数据。

建议先定义一个**产品侧稳定 `org_key`**，所有数据库实体保存映射；后续再逐步统一主数据。

对于未来全国扩展，行政区划建议独立建模：

```text
jurisdiction:
province / municipality / city / district / county
```

不要把“省”硬塞进现有 `OrganizationLevel`；上海场景默认从“上海市 -> 区”即可。

---

# 4. 材料状态机

材料槽位状态不能只有“有文件/没文件”。

建议统一为：

```text
not_due             未到期
missing             逾期未上传
uploaded            已上传待分析
processing          分析中
review_required     待人工复核
reviewing           人工复核中
completed           已复核完成
not_applicable      不适用
mapping_required    年度/文种/主体映射待确认
failed              处理失败
```

关键规则：

### 未到期
例如 2026 年度决算尚未进入公开窗口，不计“缺失”。

### 逾期未上传
只有满足：

```text
now > due_at
AND applicable
AND no current document
```

才算缺失。

### 完成
必须同时满足：

- 分析任务终态；
- 无阻塞检查义务；
- 所有正式问题已人工处理；
- 所有要求人工补核的义务已处理；
- Review Complete 已持久化。

不能再使用“前端按钮跳回队列”代表完成。

---

# 5. 前端整改任务

## WP-UI-01 导航与路由

修改：
- `app/app/components/workspace/nav.ts`

将：
- `任务历史` -> `材料台账`

推荐新路由：

```text
/materials
/materials/district/[districtId]
/materials/department/[departmentId]?year=2025
/materials/unit/[unitId]
/materials/slots/[slotId]
```

旧 `/history`：
- 保留 301/应用内 redirect；
- 或改为 `/materials?tab=runs` 的兼容入口。

## WP-UI-02 工作台总览

`WorkbenchPage.tsx` 不再以 Job KPI 为首页主体。

首页第一屏改成：

- 到期应收
- 已上传
- 逾期未上传
- 有问题
- 待人工复核
- 未到期

下方显示上海 16 区覆盖卡片。

任务运行健康（正在处理/失败）缩成次级区域。

## WP-UI-03 全局材料搜索

新增共享组件：

```text
GlobalMaterialSearch
```

搜索字段：
- PDF 文件名；
- 主管部门；
- 单位；
- 财政年度；
- 文种；
- job id（兼容运维）。

结果直接给两个核心动作：
- 打开材料；
- 进入复核。

支持 `Ctrl/Cmd + K`。

## WP-UI-04 材料台账首页

对应最终效果图：

`06_material_ledger_landing.png`

要求：
- 年度必须是财政年度；
- 预算/决算独立筛选；
- 区县卡片直接显示缺失/问题/待复核；
- 未到期单独统计。

## WP-UI-05 区级主管部门矩阵

对应：

`07_district_department_matrix.png`

每个主管部门行直接显示：

```text
2025预算 5/5
2025决算 4/5
缺失 1
问题 3
待复核 1
完整率 90%
```

点击展开进入主管部门页。

## WP-UI-06 部门/单位材料矩阵

对应：

`08_department_year_material_matrix.png`

必须明确区分：
- 部门汇总；
- 本部单位；
- 直属单位。

预算/决算每个是独立 Material Slot。

## WP-UI-07 单位多年度时间轴

对应：

`09_unit_multiyear_timeline.png`

按财政年度排列预算/决算，不按网页发布时间排列。

## WP-UI-08 材料详情

对应：
- `10_material_detail_overview.png`
- `11_material_detail_coverage.png`
- `12_material_detail_versions_source.png`
- `13_material_slot_missing_state.png`

Tabs：
1. 材料概览
2. 检查结果
3. 检查覆盖
4. 版本与来源
5. 处理记录（可作为第五 Tab）

## WP-UI-09 上传中心

保留当前“自动识别优先、低置信度人工确认”。

新增：
- 从 Material Slot 点击上传时自动带入地区/单位/年度/文种；
- 上传后验证识别结果与 Slot 是否冲突；
- 冲突则阻塞绑定，不允许静默覆盖；
- 批量上传并发建议 3~5 个文件，但分析仍异步入队。

## WP-UI-10 质量管理

`QualityPage` 增加：

- 检查义务完成率；
- 阻塞未完成义务数；
- `not_implemented` 数；
- `insufficient_data` 数；
- `parse_ambiguity` 数；
- 正式问题证据完整率；
- 人工确认误报率；
- Golden precision / recall（仅在有有效样本时显示）。

页面覆盖率继续保留，但不能再被用户误解成“业务检查覆盖率”。

## WP-UI-11 规则与版本

`RulesPage` 除“规则条目数”外增加：

- obligation catalog 版本；
- 义务总数；
- 已实现；
- 尚未实现；
- 当前支持的预算/决算范围；
- 每项 obligation -> checker 映射；
- `pending_checkers` 明细。

---

# 6. 审核工作流整改

## WP-RVW-01 持久化 Review Lifecycle（P0）

新增后端状态：

```text
review_session
review_started_at
review_completed_at
review_completed_by
review_result
```

新增接口示例：

```text
POST /api/reviews/{slot_id}/start
POST /api/reviews/{slot_id}/complete
GET  /api/reviews/{slot_id}
```

Complete 前后端共同校验：

- pending finding = 0；
- 阻塞义务 = 0，或已人工核验；
- 文种/年度/主体无未解决冲突；
- 当前 PDF 版本仍是审核时版本。

如果审核期间 PDF 被替换，Review 必须失效或要求重新确认。

## WP-RVW-02 人工补核检查义务

当前义务状态有：

- not_implemented
- insufficient_data
- parse_ambiguity
- ...

业务上部分事项可以人工补核。

新增：

```text
manual_obligation_review
obligation_id
status: verified_ok / verified_issue / not_applicable
evidence_note
reviewer
reviewed_at
document_version_id
```

这样“尚未实现的自动规则”不会让材料永远卡死，但人工确认必须可追溯。

注意：
- 人工补核只能关闭当前材料实例的阻塞；
- 不能把义务目录里的 `not_implemented` 从系统能力统计里抹掉。

---

# 7. 误报专项整改

目标：宁可明确显示“无法确认/需人工复核”，也不要输出不可靠的正式问题。

## FP-01 正式问题统一门槛

所有正式 finding 必须通过统一 `is_formal_finding` 门禁，建议再增加：

### 数值类正式问题至少具备
- rule/obligation id；
- 页码；
- 表/章节身份；
- lhs/rhs；
- 差值；
- 单位；
- 期间；
- 主体/口径；
- 原文或单元格证据。

缺一类关键维度：
> 降级为 manual_review / insufficient_data，不形成正式问题。

## FP-02 统一金额/舍入口径

必须全链路只使用一个 Decimal 金额模块。

规则：
- 单位统一后计算；
- 动态半单位容差；
- 0.01 万元等纯舍入差异不得高严重度；
- 若无法确定原表金额单位，不做强比较；
- 预算/决算不同期间不能混算。

给三份真实样张写死回归：
- 0.01 差仅 hint/info；
- 9595 级别大额差必须正式报；
- 表移位导致一串派生差异应归并根因。

## FP-03 列身份 fail-closed

严禁：
- “找不到本年列就取第一列”；
- “当前单元格空就向右找金额”；
- “纯数字列默认当金额列”。

期间列身份歧义：
> `insufficient_data`。

## FP-04 表格续页/双栏

结构化解析必须先解决：
- 表名锚；
- 续页；
- 多栏；
- 同页多表；
- code / amount column identity；
- 左右 lane。

之后再让规则消费。

当前仍待迁移的 V33-117/120 必须完成结构化消费后，再考虑切生产主路径。

## FP-05 主体/年度/文种隔离

比较两项数据前必须确认：

```text
same fiscal_year
same report_kind context
same subject
same caliber
same fund_scope
```

其中任何一项不确定：
> 不产生跨表正式问题。

## FP-06 根因归并

一个表格列移位可能产生十几条派生错误。

增加：

```text
root_cause_id
finding_group_id
```

UI 默认显示：

> 1 个根因 + N 个受影响勾稽

而不是把 N 个派生结果当 N 个独立业务问题。

## FP-07 AI 输出限制

AI-only finding 默认不能直接成为高置信度正式问题。

要求：
- 可定位原文；
- span 有效；
- 材料版本一致；
- 通过 deterministic validator；
- 金额类必须由结构化事实复核。

AI 更适合：
- 表述异常；
- 说明缺失；
- 语义冲突候选；
- 人工复核建议。

---

# 8. 漏报专项整改

## FN-01 先补完 8 个 `pending_checkers`

本阶段完成后：

```text
check_coverage_baseline.py --assert-gaps 0
```

或至少 Supported Core Profile 为 0。

优先顺序建议：

### P0
1. `OBL-CROSS-SAN-GONG-ECON`
2. `OBL-TXT-FUND-DETAIL`
3. `OBL-NARRATIVE-INDICATOR-REPEAT`
4. `OBL-TREND-ZERO-BASE`

这些已经有真实样张反例。

### P1
5. `OBL-TREND-COMPLETION-RATE`
6. `OBL-DISCLOSURE-PERCENT-UNIT`
7. `OBL-SG-COMPLETION`

### P1/P2
8. `OBL-PERF-PHASE-AMOUNT`

每补一条规则：
- 先增加人工真值；
- 再加失败反例；
- 再实现；
- 再跑三份真实样张；
- 不允许“先把 pending 从清单删掉让门禁变绿”。

## FN-02 扩大 Golden Corpus

现在 Golden GATE-PASS 仍不足以代表真实上海全量材料。

建议冻结至少：

### 第一阶段 30 份
- 15 决算；
- 15 预算；
- 部门汇总 / 单位；
- 不同区县；
- 不同模板；
- 续表、多栏、零值、空表、基金/国资/三公等。

### 第二阶段 60~100 份
按文种、主体、年份、模板分层抽样。

每份文档至少标：
- defect；
- acceptable；
- rounding_hint；
- manual_review。

避免只标“应该报的问题”，还必须标“不应该报的问题”。

## FN-03 19 组人工确认问题全部入真值

仓库已经记录：
> 19 组人工确认问题中 14 组漏报。

必须把 19 组转成稳定真值集，而不是继续留在文字验收报告里。

每组记录：
- truth_id；
- document sha；
- page；
- evidence；
- expected checker/obligation；
- expected severity；
- acceptable tolerance。

## FN-04 top30 人工裁决

完成 `AUDIT_QUALITY_REMEDIATION` 中遗留的 top30 人工裁决。

建议按以下顺序：
- 高频 finding；
- 高频 info；
- 争议严重度；
- 无法定位 evidence；
- AI finding。

输出：
- 真阳性；
- 假阳性；
- 漏报；
- 舍入提示；
- 无法判定。

---

# 9. 检查义务台账产品化

后台技术状态不要直接原样暴露给普通用户。

映射：

```text
completed              自动检查完成
not_applicable         不适用
not_implemented        自动检查暂不可用，需要人工核验
not_executed           检查未执行
insufficient_data      取数不足，需要人工核验
parse_ambiguity        解析存在歧义
execution_error        检查执行异常
profile_unresolved     材料画像待确认
kind_conflict          文种冲突待确认
ai_not_run             语义检查未执行
ai_failed              语义检查失败
```

技术 rule id 放在展开详情里，不要成为业务用户的主语言。

---

# 10. 官方网站来源与“应收材料”建立

仅靠“用户上传过什么”无法计算未上传。

## 第一阶段：人工/Excel 初始化
管理员导入：

```text
区县
主管部门
单位
财政年度
预算是否应收
决算是否应收
due_at
来源栏目 URL
```

一次即可生成年度材料槽位。

## 第二阶段：官网辅助采集
增加 source collector：
- 抓取栏目页；
- 解析标题；
- 识别财政年度/预算/决算；
- 识别部门/单位；
- 记录 URL 与发布日期；
- 匹配 Slot；
- 不确定时进入 mapping_required。

原则：
> 官网采集负责“发现候选材料”，不能绕过 DocumentProfile 和人工冲突确认。

---

# 11. API 设计

建议新增：

```text
GET /api/materials/coverage
GET /api/materials/search
GET /api/materials/slots
GET /api/materials/slots/{slot_id}
POST /api/materials/slots/{slot_id}/attach
POST /api/materials/slots/{slot_id}/mark-not-applicable

GET /api/materials/departments/{id}/matrix
GET /api/materials/units/{id}/timeline

GET /api/materials/slots/{slot_id}/versions
GET /api/materials/slots/{slot_id}/runs

POST /api/reviews/{slot_id}/start
POST /api/reviews/{slot_id}/complete
POST /api/reviews/{slot_id}/obligations/{obligation_id}

GET /api/quality/obligations
GET /api/quality/golden
```

`/api/jobs` 不删除，只回归“执行队列”职责。

---

# 12. 性能与并发

目标规模按上海 16 区设计：

即使：
- 16 区；
- 数百主管部门；
- 上千单位；
- 多年度；
- 每年预算/决算各 1 份；

Material Slot 很快会达到万级。

因此禁止：
- 首页扫描每个 job 目录实时算汇总；
- 每个区县卡片逐 PDF 打开 summary；
- 每个页面请求全量 `/api/jobs` 再前端过滤。

整改：
- coverage/material stats 从数据库聚合；
- 建索引：
  - fiscal_year
  - report_kind
  - jurisdiction
  - subject_org_id
  - slot status
  - updated_at
- API 服务端分页；
- 搜索 debounce；
- 每页 50~100 条；
- 区县/主管部门汇总可做 30~60 秒缓存。

---

# 13. 质量指标与发布门禁

## Gate A：数据与状态正确性
必须满足：
- 相同部门/本部不串线；
- 财政年度与发布日期不混；
- 同一 Slot 多版本不覆盖；
- 未到期不算缺失；
- 无 Slot 的历史文档明确显示“待映射”。

## Gate B：检查完整性
Supported Core Profile：
- `not_implemented = 0`；
- 不允许缺回执被记 completed；
- formal “无问题”必须带 conclusion_scope；
- blocking obligation > 0 时不得 completed。

## Gate C：误报
建议正式发布门槛：

- 正式 finding precision ≥ **98%**；
- 重大/高风险规则 precision ≥ **99%**；
- evidence 可定位率 = **100%**；
- 舍入差正式误报 = **0**；
- 同一根因重复正式计数率 < **5%**。

以上是目标门槛，不是当前仓库现状。

## Gate D：漏报
- overall recall ≥ **95%**（冻结人工真值集）；
- 关键金额勾稽/跨表一致性 recall = **100%**；
- 8 个当前已知真实缺口全部有正反例。

## Gate E：审核闭环
E2E：
> 上传 -> 自动归属 -> 处理 -> 待复核 -> 人工确认 -> Review Complete -> 导出 -> 再打开仍保持完成。

刷新浏览器、重启服务后状态不丢。

## Gate F：可靠性
- DB migration 空库 + 升级库均通过；
- 测试不得写开发/生产目录；
- 任务失败可重试；
- 原 PDF SHA 不变；
- 导出结果记录 document_version + ruleset + engine version；
- 备份恢复演练通过。

---

# 14. 回归测试矩阵

必须固定至少四类：

## 14.1 真实样张
- 文旅局；
- 石泉路；
- 宜川路；
- 规划和自然资源局；
- 其它预算/决算样本。

## 14.2 结构化反例
- current/prior 列交换；
- 本年空；
- 双年度；
- 多栏；
- 续表；
- 同页两表；
- 0 值；
- 0.01 舍入；
- 金额单位不一致。

## 14.3 产品流程
- 未到期；
- 到期未上传；
- 同名部门/本部；
- 版本替换；
- 文种冲突；
- 年份冲突；
- Slot 人工映射；
- 不适用人工依据。

## 14.4 权限
- 管理员看全市；
- 区级用户只看授权区；
- 审核员不能改组织主数据；
- 下载与预览都做 job/slot 权限校验。

---

# 15. 实施 Work Package

## WP0 — 基线冻结与真值锁定（P0，2~3 天）
输出：
- 当前 main SHA；
- 三份真实样张 SHA；
- 现有 Golden SHA；
- 当前 8 gaps；
- 当前 regression 输出；
- migration 备份。

验收：
> 后续任何整改必须能对比“改前/改后”，不得修改真值来让测试变绿。

## WP1 — Material Slot 数据模型（P0，4~6 天）
输出：
- migration；
- slot/source/version/run 映射；
- backfill；
- indexes；
- API contract。

注意：
> 仅从历史上传数据回填“已有材料”；没有应收清单前不得凭空生成“未上传”。

## WP2 — 材料台账 UI（P0，5~8 天）
实现效果图 06~13。

输出：
- `/materials`；
- district matrix；
- department matrix；
- unit timeline；
- material detail；
- global search。

## WP3 — 审核持久化闭环（P0，4~6 天）
输出：
- review session；
- complete endpoint；
- manual obligation review；
- reanalysis invalidation；
- audit trail。

## WP4 — 8 个义务缺口规则实现（P0/P1，10~15 天）
先做四个真实漏报，再做其余四个。

每一个 obligation 单独 PR 或小批次 PR，不允许一次提交全部。

## WP5 — 结构化消费收敛（P0/P1，5~8 天）
输出：
- V33-117/120 structured；
- shadow compare；
- 结果一致后切主路径；
- legacy fallback 有明确终止计划。

## WP6 — 误报治理与根因归并（P0/P1，5~8 天）
输出：
- formal finding gate v2；
- rounding policy；
- root cause grouping；
- AI finding gate；
- 证据完整率 100%。

## WP7 — Golden 扩容 + top30（P0/P1，5~10 天）
目标：
- 第一阶段 ≥30 docs；
- 19 组人工问题真值化；
- top30 裁决完成；
- precision/recall 按 rule/obligation 分桶。

## WP8 — 质量管理/规则页面（P1，3~5 天）
将 obligation、precision/recall、人工误报反馈真正显示出来。

## WP9 — 官方来源与应收清单（P1/P2，5~8 天）
第一版先 CSV/Excel 导入；
官网采集放第二阶段，避免把爬虫变成 P0 阻塞。

## WP10 — 性能、安全、部署与 UAT（P0，4~7 天）
- 性能压测；
- 10000+ slot 数据；
- 50 并发查询；
- 大 PDF；
- 错误恢复；
- 权限；
- 备份；
- UAT。

---

# 16. 建议实施顺序

推荐严格按依赖执行：

```text
WP0 基线冻结
  ↓
WP1 Material Slot / 数据模型
  ↓
WP2 材料台账 UI
  ├─→ WP3 审核持久化
  └─→ WP9 应收清单
  ↓
WP4 8 个规则缺口
  ↓
WP5 结构化消费
  ↓
WP6 误报治理
  ↓
WP7 Golden / top30
  ↓
WP8 质量与规则可视化
  ↓
WP10 UAT / 发布
```

WP4~WP7 可以有部分并行，但**发布判定必须最后统一跑完整 Golden 和真实样张**。

---

# 17. Git 分支/PR 策略

不要一个超级 PR。

建议：

```text
feat/material-ledger-schema
feat/material-ledger-ui
feat/material-search
feat/review-lifecycle
fix/obligation-cross-san-gong
fix/obligation-fund-detail
fix/obligation-narrative-repeat
fix/obligation-zero-base
fix/obligation-completion-rate
fix/obligation-percent-unit
fix/obligation-sg-completion
fix/obligation-perf-phase
fix/formal-finding-gate
fix/root-cause-grouping
test/golden-expansion
feat/quality-obligation-dashboard
```

每个 PR 必须有：
- 正例；
- 反例；
- 真实样张对比；
- 不降低其它 rule recall 的证明；
- UI 改动截图（如有）。

---

# 18. 明确禁止的“修绿”方式

1. 删除 obligation 让分母变小；
2. 把 `not_implemented` 改成 `not_applicable`；
3. 解析失败时返回空 finding 并标 done；
4. 通过提高金额容差掩盖真实差异；
5. 修改 Golden 以匹配当前系统输出；
6. 用 info 降级代替真正修复；
7. 用文件名猜预算/决算覆盖封面冲突；
8. 让 AI 在无原文证据时生成正式问题；
9. 把未到期材料算“未上传”；
10. 把同名部门和本部按名称合并。

---

# 19. 最终“可用软件”的定义

完成本 PLAN 后，用户应当可以：

1. 打开工作台，一眼看上海 16 区哪些缺材料、哪些有问题；
2. 在材料台账按区县 -> 部门 -> 单位 -> 财政年度 -> 预算/决算找到材料；
3. 直接搜索单位/文件名快速打开 PDF；
4. 明确知道一份材料的来源 URL、发布日期、财政年度和版本；
5. 知道系统到底检查了哪些项目、哪些没检查完；
6. 正式问题必须有可返回 PDF 原文的证据；
7. 明显舍入差不会被当严重错误；
8. 自动能力缺口会明确提示人工核验，而不是假装通过；
9. 人工复核结论可持久化，刷新/重启后不丢；
10. 一份材料替换版本后，旧分析和旧复核仍可追溯；
11. 导出的报告能够回答“这份结论基于哪个 PDF、哪个规则集、哪个引擎版本”；
12. Golden/真实样张持续证明系统误报和漏报在可接受范围内。

当这些条件未满足时，产品应对外定位为：
> **人工审核辅助工具**

而不是：
> **自动替代人工完成预算/决算审查的系统**。

---

# 20. 本次效果图与本 PLAN 的对应关系

最终 UI 包中：

- `01_workbench_city_coverage.png` -> WP-UI-02
- `02_global_search.png` -> WP-UI-03
- `03_upload_center.png` -> WP-UI-09
- `04_processing_queue.png` -> Job 执行态
- `05_review_workbench.png` -> WP-RVW-01/02
- `06_material_ledger_landing.png` -> WP-UI-04
- `07_district_department_matrix.png` -> WP-UI-05
- `08_department_year_material_matrix.png` -> WP-UI-06
- `09_unit_multiyear_timeline.png` -> WP-UI-07
- `10_material_detail_overview.png` -> WP-UI-08
- `11_material_detail_coverage.png` -> obligation 产品化
- `12_material_detail_versions_source.png` -> slot/version/source 模型
- `13_material_slot_missing_state.png` -> expected material + due_at
- `14_export_archive.png` -> review -> archive
- `15_quality_management.png` -> WP-UI-10
- `16_rules_versions.png` -> WP-UI-11
- `17_system_settings.png` -> 组织/权限/运维

至此，主页面层级和材料业务链路的效果图已完整覆盖；后续如再出图，主要是 Modal/Drawer/异常态细化，不再是缺失的主页面层级。

