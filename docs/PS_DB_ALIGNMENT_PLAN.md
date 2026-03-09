# PS 数据库对齐方案

## 目标

以 `tianbaoxitong` 的业务数据层为主线，把 `GovBudgetChecker` 的 PDF 结构化结果直接沉淀到可复用的共享表中，减少后续“字段映射表”成本，并且不影响现有审校规则链路。

## `tianbaoxitong` 当前关键表

- `org_department`：部门主数据。
- `org_unit`：单位主数据，挂在部门下。
- `org_dept_annual_report`：某部门/单位某年度预算或决算原始报告。
- `org_dept_table_data`：整张表的结构化 JSON，适合前台复核、重建原表、归档复用。
- `org_dept_line_items`：按行项目沉淀的结构化明细，适合统计分析、大屏、历史复用。

## `GovBudgetChecker` 当前结构化层

- `fiscal_document_versions`：上传 PDF 版本。
- `fiscal_table_cells`：表格单元格证据层。
- `fiscal_table_instances`：九张表识别结果。
- `fact_fiscal_line_items`：按指标拆开的事实层。

这套结构对“解析”和“规则检查”很友好，但不能直接给 `tianbaoxitong` 前台或历史填报复用，因为：

1. 单位主数据和 `tianbaoxitong` 不同源。
2. `fact_fiscal_line_items` 是“每个指标一行”，而不是“每个表格行项目一行”。
3. 缺少与 `tianbaoxitong` 报告域模型一致的 `report / table_data / line_items` 三层落库。

## 本次落地

### 1. 新增共享业务表

已在 `src/db/migrations.py` 中新增与 `tianbaoxitong` 对齐的共享表：

- `org_department`
- `org_unit`
- `org_dept_annual_report`
- `org_dept_table_data`
- `org_dept_line_items`

这批表用于承接 `GovBudgetChecker` 的解析结果，但不替换现有 `fiscal_*` 表，因此不会影响当前检查功能。

### 2. 新增同步服务

已新增 `src/services/ps_schema_sync.py`，在结构化入库完成后执行：

- 自动补齐部门/单位主数据；
- 把 PDF 版本写入 `org_dept_annual_report`；
- 把整张表的单元格矩阵写入 `org_dept_table_data`；
- 把事实层重新按“行项目”聚合后写入 `org_dept_line_items`。
- 优先使用上传时携带的 `organization_id` 和本地 `organizations.json` 组织树命中正式部门/单位编码。

### 3. 保持原检查链路不变

现有审校仍然使用：

- `fiscal_table_cells`
- `fiscal_table_instances`
- `fact_fiscal_line_items`
- `qc_*`

PS 对齐层只是“并行落库”，即使这层同步异常，也不会阻断原有审校结果产出。

## 当前仍然保留的差异

### 1. 组织主数据存在“正式匹配 + 回退自动补录”双模式

当前优先级已经变成：

1. 先按上传时的 `organization_id` 命中正式组织树；
2. 命不中时，再按名称和关键字匹配；
3. 仍命不中时，才自动补录部门/单位。

也就是说，后续只要把 `tianbaoxitong` 的组织架构导入到 `organizations.json`，`GovBudgetChecker` 就可以直接按统一组织编码入共享库。

### 2. `created_by / uploaded_by` 暂未接入统一用户体系

为了不跟当前 `GovBudgetChecker` 的用户表冲突，现阶段只保留数据列，不强依赖统一用户外键。

### 3. 文本章节尚未对齐

目前只打通了表格与行项目。  
像 `org_dept_text_content` 这种“部门职责、机构设置、名词解释”类文本内容，建议放在下一阶段补齐。

## 推荐的下一阶段

### 第一优先级

- 接入统一 `org_department / org_unit` 编码；
- 把自动生成单位名改成按 PS 主数据匹配；
- 将预算、决算都统一沉淀到 `org_dept_annual_report`。

### 第二优先级

- 新增 `org_dept_text_content` 同步；
- 将 PDF 页码、置信度、原始证据链接进一步落到共享库；
- 让 `tianbaoxitong` 直接读取 `org_dept_table_data` 和 `org_dept_line_items`。

## 组织架构同步脚本

已新增脚本：`scripts/sync_ps_organizations.py`

支持两种来源：

- 直接连接 `tianbaoxitong` PostgreSQL：
  - `python scripts/sync_ps_organizations.py --database-url "postgres://..." --output data/organizations.json --backup`
- 直接读取 `pg_dump` SQL 备份：
  - `python scripts/sync_ps_organizations.py --sql-dump "D:\\...\\govbudget_before_restore_20260223_154736.sql" --output data/organizations.json --backup`

如果希望补齐城市/区级根节点，可追加：

- `--city-name 上海市 --district-name 普陀区`

脚本会把 `org_department` / `org_unit` 转成 `GovBudgetChecker` 使用的 `organizations.json`，供后续上传时的 `organization_id` 精准匹配使用。

### 第三优先级

- 逐步淡化 `GovBudgetChecker` 自有 `fiscal_*` 作为业务层用途；
- 保留 `fiscal_*` 作为解析证据层，PS 表作为业务共享层；
- 最终形成“一个解析引擎 + 一个共享业务库”的结构。
