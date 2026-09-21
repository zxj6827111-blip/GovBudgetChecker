# 材料槽位历史回填盘点（dry-run）

本报告**不写入任何数据**。所有数字都是「如果执行回填会怎样」的预演。

- 扫描目录：`E:\Software Development\GovBudgetChecker\GovBudgetChecker\uploads`
- 扫描任务目录数：348
- 其中带文档校验和：346
- 预计生成槽位：50
- 预计生成文件版本绑定：56
- 同名（部门/单位）歧义条数：76
- 实际写入条数：0

## 分桶统计

| 分桶 | 条数 |
| --- | --- |
| auto_mappable | 141 |
| missing_subject | 130 |
| missing_kind | 76 |
| no_document | 1 |

## 未自动映射原因

| 原因码 | 说明 | 条数 |
| --- | --- | --- |
| `ok` | 可自动映射 | 141 |
| `organization_missing` | 任务未记录主体组织 | 130 |
| `kind_unknown` | 文种未识别 | 76 |
| `no_document_key` | 缺少可区分文档的校验和 | 1 |

## 可自动映射（按文种）

- budget: 124
- final: 17

## 样例

### missing_kind（76 条）

- `kind_unknown` — split_mode.pdf (年度=None, 文种=unknown, 主体=split)
- `kind_unknown` — split_mode.pdf (年度=None, 文种=unknown, 主体=split)
- `kind_unknown` — split_mode.pdf (年度=None, 文种=unknown, 主体=split)
- `kind_unknown` — split_mode.pdf (年度=None, 文种=unknown, 主体=split)
- `kind_unknown` — split_mode.pdf (年度=None, 文种=unknown, 主体=split)

### missing_subject（130 条）

- `organization_missing` — sample_budget_2025.pdf (年度=2025, 文种=budget, 主体=未记录)
- `organization_missing` — split_mode.pdf (年度=None, 文种=unknown, 主体=未记录)
- `organization_missing` — split_mode.pdf (年度=None, 文种=unknown, 主体=未记录)
- `organization_missing` — sample_budget_2025.pdf (年度=2025, 文种=budget, 主体=未记录)
- `organization_missing` — split_mode.pdf (年度=None, 文种=unknown, 主体=未记录)

### no_document（1 条）

- `no_document_key` — 39295410f5668f939c0eb9cd5f98950c (年度=None, 文种=unknown, 主体=未记录)
