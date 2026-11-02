# 组织目录重复记录清理执行单（2026-11-01）

## 适用范围

只处理“同一个主体在组织目录里存在多条记录”的问题。

**本机（macOS 开发机）已于 2026-11-01 执行完毕：684 条 → 651 条，合并 33 条。**
其他机器（原 Windows 机、服务器）的组织目录是各自独立的 `data/organizations.json`，需要按本单各自执行一遍，否则那边的上传仍会提示“组织匹配置信度不足”。

## 一、症状与判定

上传材料时出现以下任一现象，先按本单体检，不要急着在界面上挑一个：

- 确认面板提示“组织匹配置信度不足”，候选里是同一单位的多种写法（`局本级` / `局本部` / `局（本级）`）
- 预检返回 `binding_decision.decision = needs_confirmation`，`reasons[0].code = equivalent_candidates_tie`

## 二、成因（本机已查明，供对照）

| 事实 | 证据 |
|---|---|
| 684 条里 657 条创建于同一秒 `2026-10-22 23:31:13` | `organizations.json` 的 `created_at` 分布 |
| 一次批量写入，数据形态为 `code=''`、`keywords=[自身名字]` | 与 `import_from_list` 的行装配一致 |
| 源清单本身重复：同一单位写成「本级 / 本部 /（本级）」多行 | 29 组重复组全部来自该批 |
| 当时无去重：重复校验 `4f25c7a9`（2026-10-24）才提交 | `git log -S "重复组织"` |
| 该校验只抹平全/半角括号，写法变体仍会漏过 | 现改为 `identity_name` 口径 |

写入侧已在代码中改为身份归一判重（`_catalog_errors`、`import_from_list`、ingest 的 `_find_org_by_name_and_level`），本单只负责清理历史数据。

## 三、执行前检查

1. 确认没有正在运行的上传/解析任务（工作队列空闲），避免清理与写入交错。
2. 确认目标目录可写、磁盘空间充足。
3. 记录当前状态，便于对比：

```bash
# macOS / Linux
shasum -a 256 data/organizations.json
# Windows PowerShell
Get-FileHash data\organizations.json -Algorithm SHA256
```

## 四、执行步骤

### 步骤 0：备份（必做）

```bash
STAMP=$(date +%Y%m%d-%H%M%S)
cp -p data/organizations.json "data/organizations.json.bak.$STAMP"
cp -p data/job_org_links.json "data/job_org_links.json.bak.$STAMP"
```

### 步骤 1：只读体检

```bash
uv run python scripts/report_org_duplicates.py --org-data-dir data --out <仓外目录>/approve_groups.candidate.json
```

输出会分两类：

- **严格重复**：全/半角括号、空白差异，清理工具默认可合并，无需人工确认；
- **写法变体**：如「局本级 / 局（本级）」，**必须逐组人工确认**。

### 步骤 2：人工确认变体组

打开 `approve_groups.candidate.json`，逐组判断是否确为同一主体。以下情况**不得**放进批准清单：

- 「机关」与「本级」——机关是本级的一个部门口径，不是同一节点；
- 「部门」与「单位」混写；
- 「（筹）」的筹备机构与正式机构；
- 同一名称但上级部门不同的两条记录（不同区同名单位）。

确认后的组写进 `<仓外目录>/approved.json`，格式为名称二维数组：

```json
[["上海市水务局本级", "上海市水务局本部", "上海市水务局（本级）"]]
```

> 不要把候选文件直接改个名当批准清单用。工具只校验“同组确实身份等价”，不会替你判断业务上是否同一主体。

### 步骤 3：dry-run

```bash
set -a && source .env && set +a   # 提供 DATABASE_URL
uv run python scripts/clean_org_directory.py \
  --org-data-dir data \
  --uploads-dir uploads \
  --out <仓外目录>/dryrun \
  --approve-groups <仓外目录>/approved.json \
  --database-url-env DATABASE_URL \
  --dry-run
```

`--database-url-env` 不可省略：不给的话数据库引用状态是 `unverified`，正式应用会被工具自己拦住（这是有意的保护）。

### 步骤 4：审阅报告，三个必看项

打开 `<仓外目录>/dryrun/report.json`：

1. `merge_ids`：合并条数是否与预期一致（`cleaned_count = original_count - len(merge_ids)`）；
2. `database.status`：必须是 `checked_read_only`，且 `database.tables` 每一项的 `references` 都是空数组；
3. `references`：会被改写的文件列表。本次 33 条冗余均无引用，应为空；若不为空，说明有任务挂着这些记录，需先人工迁移。

同时看 `<仓外目录>/dryrun/manual_review.csv`，确认没有把该保留的记录列成待确认。

### 步骤 5：正式应用

```bash
uv run python scripts/clean_org_directory.py \
  --org-data-dir data \
  --uploads-dir uploads \
  --out <仓外目录>/apply \
  --approve-groups <仓外目录>/approved.json \
  --database-url-env DATABASE_URL \
  --apply
```

被阻止时的处理：

- 提示“数据库引用尚未核实”→ 检查 `DATABASE_URL` 是否真的指向本环境；
- 提示“仍需独立迁移”→ 说明有任务/材料挂在待删记录上，先在 `material_slots`、`job_org_links` 一类表中确认，再单独安排迁移，不要手工改 JSON。

### 步骤 6：验证

```bash
# 重复归零、引用不失联
uv run python scripts/report_org_duplicates.py --org-data-dir data

uv run python - <<'PY'
import json, pathlib
orgs = json.loads(pathlib.Path("data/organizations.json").read_text(encoding="utf-8-sig"))["organizations"]
links = json.loads(pathlib.Path("data/job_org_links.json").read_text(encoding="utf-8-sig"))["links"]
ids = {o["id"] for o in orgs}
print("组织总数:", len(orgs))
print("job_org_links 悬空:", sum(1 for l in links if l.get("org_id") not in ids))
print("父节点悬空:", sum(1 for o in orgs if o.get("parent_id") and o["parent_id"] not in ids))
PY
```

再传一份此前会并列的材料，确认预检返回 `decision = bound`（不再需要三选一）。

### 步骤 7：回滚

应用失败或结果不符预期时，用步骤 0 的备份覆盖回去即可（清理工具同时会在 `<仓外目录>/apply/backup/` 留一份）：

```bash
cp -p "data/organizations.json.bak.<STAMP>" data/organizations.json
cp -p "data/job_org_links.json.bak.<STAMP>" data/job_org_links.json
```

## 五、验收标准

- [ ] `report_org_duplicates.py` 输出：严格重复 0 组、写法变体 0 组
- [ ] `job_org_links` 悬空引用 0、父节点悬空 0
- [ ] `report.json` 中 `database.status = checked_read_only` 且引用全空
- [ ] 抽样上传此前并列的材料，预检 `decision = bound`
- [ ] 界面组织树层级完整（部门下仍能看到其直属单位）

## 六、注意事项

- `data/*.json` 被 `.gitignore` 忽略，**清理不会随代码分发**，每台机器各跑一次。
- 不要手工编辑 `organizations.json`：绕过层级校验与并发锁，容易制造悬空父节点。
- 清理后组织总数会减少（合并数），材料数量与任务不受影响；`src_sha256` 记录在 `report.json` 中便于核对来源。
