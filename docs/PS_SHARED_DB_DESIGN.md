# PS 共享数据库设计说明

## 1. 目标

本设计以 `tianbaoxitong` 的业务落库口径为主，结合 `GovBudgetChecker` 当前已经打通的 PDF 解析链路，形成一套可复用的共享数据库方案，满足以下目标：

- `GovBudgetChecker` 解析正确后，预算/决算 PDF 可直接沉淀到 PS 共享库。
- `tianbaoxitong` 后续可直接复用历史表格与行项目数据，不再走额外字段映射表。
- 原有规则检查、问题定位、红框截图等审校链路继续保留，不受共享库同步影响。
- 为后续大屏展示、纵向年度分析、跨单位对比分析预留稳定的数据层。

## 2. 总体架构

当前系统已经形成“三层结构”：

1. **解析证据层**
   - `fiscal_table_cells`
   - `fiscal_table_instances`
   - `fact_fiscal_line_items`
2. **共享业务层（PS 口径）**
   - `org_department`
   - `org_unit`
   - `org_dept_annual_report`
   - `org_dept_table_data`
   - `org_dept_line_items`
3. **审校结果层**
   - `qc_*`
   - 任务状态、规则结果、截图证据

其中：

- 解析证据层服务于“识别、溯源、校验”。
- 共享业务层服务于“入库、复用、展示、分析”。
- 审校结果层继续服务于“找问题”，与共享库解耦。

## 3. 处理流程

当前实际落地链路如下：

1. 上传 PDF。
2. `PDFParser` 把页内表格拆成单元格证据，写入 `fiscal_table_cells`。
3. `TableRecognizer` 识别九张表，写入 `fiscal_table_instances`。
4. `FiscalFactMaterializer` 把金额列物化成结构化 facts，写入 `fact_fiscal_line_items`。
5. `PSSharedSchemaSync` 把同一份文档同步到 PS 共享库：
   - 确认部门/单位主数据；
   - 写入年度报告主记录；
   - 写入整张表的矩阵 JSON；
   - 写入按行项目归并后的结构化明细。
6. 前台只展示同步摘要，不改变原有规则检查结论。

对应代码位置：

- 迁移定义：`src/db/migrations.py:642`
- 同步入口：`src/services/ps_schema_sync.py:26`
- 表数据同步：`src/services/ps_schema_sync.py:574`
- 行项目同步：`src/services/ps_schema_sync.py:705`

## 4. 共享库表设计

### 4.1 `org_department`

用途：部门主数据。

核心字段：

- `id`：UUID 主键
- `code`：部门唯一编码
- `name`：部门名称
- `parent_id`：上级部门
- `sort_order`：排序
- `created_at` / `updated_at`

说明：

- 用于承接 `tianbaoxitong` 的部门树。
- 可以表达区级、委办局、街镇等层级。

### 4.2 `org_unit`

用途：单位主数据。

核心字段：

- `id`：UUID 主键
- `department_id`：所属部门
- `code`：单位唯一编码
- `name`：单位名称
- `sort_order`
- `created_at` / `updated_at`

说明：

- 一个部门下可挂多个单位。
- 对于“本级/本部”这类单位，允许部门与单位名称接近，但仍建议保留独立单位记录。

### 4.3 `org_dept_annual_report`

用途：年度报告主表，一份预算/决算 PDF 对应一条主记录。

核心字段：

- `id`
- `department_id`
- `unit_id`
- `year`
- `report_type`
- `file_name`
- `file_path`
- `file_hash`
- `file_size`
- `uploaded_by`
- `created_at` / `updated_at`

唯一约束：

- `(department_id, unit_id, year, report_type)`

建议口径：

- `report_type = BUDGET | FINAL`

说明：

- 这是 `GovBudgetChecker` 与 `tianbaoxitong` 打通的主锚点。
- 同一单位同一年同一报告类型，只有一条当前生效主记录。

### 4.4 `org_dept_table_data`

用途：存整张表的结构化矩阵，用于前台复原原表、人工复核、后续导出。

核心字段：

- `id`
- `report_id`
- `department_id`
- `year`
- `report_type`
- `table_key`
- `table_title`
- `page_numbers`
- `row_count`
- `col_count`
- `data_json`
- `created_by`
- `created_at` / `updated_at`

唯一约束：

- `(report_id, table_key)`

`data_json` 当前结构：

- `source_system`
- `table_key`
- `table_title`
- `rows[]`
  - `row_index`
  - `cells[]`
    - `col_index`
    - `raw_text`
    - `normalized_text`
    - `numeric_value`
    - `page_number`
    - `bbox`
    - `is_header`
    - `confidence`

说明：

- 这一层最适合“还原原表长什么样”。
- 将来 `tianbaoxitong` 若要直接渲染历史表，可优先读取这张表。

### 4.5 `org_dept_line_items`

用途：按行项目沉淀后的业务明细，适合统计分析、趋势分析、图表和大屏。

核心字段：

- `id`
- `report_id`
- `department_id`
- `year`
- `report_type`
- `table_key`
- `row_index`
- `class_code`
- `type_code`
- `item_code`
- `item_name`
- `values_json`
- `created_by`
- `created_at` / `updated_at`

唯一约束：

- `(report_id, table_key, row_index)`

`values_json` 当前结构：

- 各金额指标，例如：
  - `total_actual`
  - `basic_actual`
  - `project_actual`
  - `fiscal_allocation`
  - 其他 measure
- `_meta`
  - `classification_type`
  - `source_page_numbers`
  - `parse_confidence`

说明：

- 这是 PS 共享库最关键的分析表。
- 后续做“按类款项聚合”“按年度比对”“按单位横向对比”，优先读这张表。

## 5. `GovBudgetChecker` 到 PS 共享库的映射关系

### 5.1 主数据映射

- 上传时若带 `organization_id`，优先按组织树精确命中。
- 若未带 `organization_id`，按名称匹配部门/单位。
- 若仍未命中，允许按名称回退自动建档，但应作为过渡方案。

当前匹配摘要已在同步结果中保留：

- `match_mode`
- `matched_organization_id`
- `department_name`
- `unit_name`

### 5.2 报告映射

来源：

- `fiscal_document_versions`
- 上传文件元数据

去向：

- `org_dept_annual_report`

关键映射：

- `pdf_path.name -> file_name`
- `pdf_path -> file_path`
- `checksum -> file_hash`
- `pdf_path.stat().st_size -> file_size`
- `doc_type -> report_type(BUDGET/FINAL)`

### 5.3 表格映射

来源：

- `fiscal_table_instances`
- `fiscal_table_cells`

去向：

- `org_dept_table_data`

关键映射：

- `table_code -> table_key`
- `source_title -> table_title`
- 单元格矩阵 -> `data_json`
- 相关页码集合 -> `page_numbers`

### 5.4 行项目映射

来源：

- `fact_fiscal_line_items`

去向：

- `org_dept_line_items`

关键映射：

- `table_code -> table_key`
- `row_order -> row_index`
- `classification_code -> class_code/type_code/item_code`
- `classification_name -> item_name`
- `measure + amount -> values_json`
- `source_page_number + parse_confidence -> values_json._meta`

## 6. 为什么不再单独做字段映射表

本次方案的核心判断是：

- `GovBudgetChecker` 目前还没有一套历史包袱很重的业务库；
- `tianbaoxitong` 已经有自己的入库模型和复用需求；
- 因此，与其长期维护“字段映射表”，不如直接把 `GovBudgetChecker` 的结构化结果按 PS 口径落到共享业务层。

这样做的好处：

- 少一层转换；
- 前台展示、历史复用、导出下载口径统一；
- 数据分析口径统一；
- 后续两套系统都围绕同一份共享库演进。

## 7. 对现有检查功能的影响

不会影响。

原因：

- 规则检查依然基于 `fiscal_table_cells`、`fiscal_table_instances`、`fact_fiscal_line_items`。
- PS 共享库同步属于并行后处理。
- 即使共享库同步失败，也不会阻断原有问题检测和报告导出。

换句话说：

- **审校链路** 负责“找错”；
- **共享库链路** 负责“入库复用”；
- 两者并行，但职责分离。

## 8. 当前已验证的入库效果

截至 `2026-03-08`，针对 `D:\普陀区预决算\2026` 的 `118` 份 PDF，全量验证结果为：

- `118` 份成功
- `0` 份硬失败
- `0` 份需复核
- `0` 份机构未匹配

结果快照见：

- `docs/validation_2026_batch_summary.json:1`

说明当前这套“PDF -> 九表识别 -> facts -> PS 共享库”的主链路已经可以稳定跑通。

## 9. 推荐查询场景

### 9.1 填报系统复用历史表

按 `department_id + unit_id + year + report_type` 查询：

- `org_dept_annual_report`
- `org_dept_table_data`

适合：

- 前台直接复用历史整表
- 对照原表人工确认

### 9.2 大屏和统计分析

按 `department_id / year / report_type / table_key` 查询：

- `org_dept_line_items`

适合：

- 类款项汇总
- 跨年度对比
- 跨单位横向对比
- 财政拨款、基本支出、项目支出专题统计

### 9.3 解析溯源与争议复核

若业务层数据有疑问，回溯：

- `org_dept_line_items -> fact_fiscal_line_items -> fiscal_table_cells`

适合：

- 核对具体来源页码
- 核对具体单元格
- 排查 OCR/表格拆分误差

## 10. 已实现查询接口

为后续 `tianbaoxitong` 直接复用共享库，当前已实现以下只读接口：

### 10.1 结构化入库明细

- `GET /api/jobs/{job_id}/structured-ingest`

用途：

- 读取某个任务的完整结构化入库结果
- 包含识别表数、facts 数、复核项、共享库同步摘要

### 10.2 共享库报告列表

- `GET /api/ps/reports`

支持参数：

- `department_id`
- `unit_id`
- `year`
- `report_type`
- `keyword`
- `limit`
- `offset`

用途：

- 按部门/单位/年度/报告类型筛选共享库中的预算或决算报告

### 10.3 共享库报告详情

- `GET /api/ps/reports/{report_id}`

用途：

- 查看某份报告的基础信息
- 返回部门、单位、表数、行项目数等摘要

### 10.4 共享库整表数据

- `GET /api/ps/reports/{report_id}/tables`

支持参数：

- `table_key`
- `include_data`

用途：

- 读取整张表的矩阵 JSON
- 适合 `tianbaoxitong` 直接复原历史表格

### 10.5 共享库行项目数据

- `GET /api/ps/reports/{report_id}/line-items`

支持参数：

- `table_key`
- `limit`
- `offset`

用途：

- 读取某份报告下的行项目明细
- 适合图表、大屏、统计分析和跨年度比对

## 11. 下一步建议

### 第一优先级

- 把 `tianbaoxitong` 的正式部门/单位编码全量同步到 `organizations.json`
- 上传时尽量携带 `organization_id`
- 两套系统统一使用同一套部门/单位编码

### 第二优先级

- 补 `org_dept_text_content`，承接“部门职责、机构设置、名词解释”等文字内容
- 在共享库中补留“来源证据链”查询接口
- 为大屏/分析接口建立只读聚合视图

### 第三优先级

- 将 `tianbaoxitong` 直接切换到读取 `org_dept_table_data` / `org_dept_line_items`
- 把历史库迁移到共享库口径
- 最终形成“一套解析引擎 + 一套共享业务库”的统一架构
