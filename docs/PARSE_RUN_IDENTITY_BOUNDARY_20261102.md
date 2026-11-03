# parse_run_uid 身份基础：调用链与边界（2026-11-02，ID-01～ID-04 整改版）

**候选基线与分支**

- 基线提交：`c6cd033d79f907a4541af7036f3d8dc49a140cd2`
- 候选分支：`codex/parse-run-identity-20261102`
- 独立工作区：`gbc-worktrees/parse-run-identity-20261102`
- 范围：只修 `parse_run_uid` 身份基础，不包含 DH-02/DH-03、不修改 r4、不实施方案 B、不采正式基线、不运行正式抽样。

## 1. 三层身份

验收方 ID-01～ID-04 整改后，身份拆成三个职责，不再用同一个哈希兼任：

| 身份 | 用途 | 变化条件 |
| --- | --- | --- |
| 输入/缓存查找键 | 查 `parse_runs` 解析缓存 | 原件、解析实现、解析配置、解析依赖、OCR 模型、schema |
| `parse_run_uid`（语义产物身份） | 事实/证据/回执绑定、旧运行隔离 | 输入键变化，或原文、单元格、几何/映射、主体身份、资格证据变化 |
| 分析运行指纹 | 审计、历史执行版本证明 | 本次实际执行的完整代码/配置清单或产物来源变化 |

`parse_run_uid` 的 basis：

```text
{
  version: "gbc.parse-run-identity.v2",
  source_file_hash, parser_name, parser_fingerprint, config_fingerprint,
  ocr_model_fingerprint, schema_version,
  semantic_artifact_hash
}
```

`semantic_artifact_hash` 覆盖解析产物中会改变结论或事实资格的内容；不再由 `artifact_content_hash` 兼任语义身份。

## 2. 语义产物身份

`src/services/input_processing_identity.py:semantic_artifact_payload()` 保留：

- 原文：`text_blocks`、`source_fragments`、`source_tokens`。
- 单元格与物理/逻辑表：`table_fragments`、`physical_fragments`、`source_grids`、`logical_tables`。
- 几何与映射：`page_count`、`coordinate_space`、`coordinate_transforms`、列映射、实例/片段 UID。
- 主体身份：逻辑表 `org_name`、`fiscal_year`、`fund_scope`、表种与标题。
- 资格证据：`page_states`（含 OCR 状态/错误）、`ocr_table_candidates`、`cell_candidates`、`resolved_cells`、单元格 `confidence/presence/native_blank_verified/ocr_provenance/label_recovery`、`reading_assessment`。
- 证据清单：`processing_manifest` 中的 `document_binding`、`manual_cells`、`manual_rows`、`manual_table_identity`、`raw_interpretation`。

明确排除：`reconstruction_decisions`、`label_visibility` 汇总、`parse_run_uid`、完整运行代码清单、输入身份字段。`reading_assessment` **不再整体归为展示字段**：它参与 `assess_table_reading()` 与 `constrain_reading_facts()`，读取缺口会把事实降为 `low_confidence`，必须进入语义身份。

数值在语义投影中统一规范化（`0`、`0.0`、`"0.00"` 等价），避免序列化往返造成同一内容不同哈希。

## 3. 身份生成调用链

### 3.1 两个代码清单

1. `runtime_code_manifest()`：冻结完整分析执行代码与配置。范围覆盖实际本地运行面：`src`（含 `utils`、`security`、`providers`、`db` 等）、`api`、`config`、`rules`、`ocr_service`、`gbc_yaml` 下的全部 `*.py`；配置摘要覆盖 `config/*.yaml`、`rules/**/*.yaml`、`requirements*.txt` 与 `pyproject.toml`。只服务分析运行指纹与审计。
2. `parse_code_manifest()`：只哈希 `PARSE_IMPLEMENTATION_FILES`；记录 `PARSE_DEPENDENCIES` 和 Python 版本；进程启动后冻结。
3. `parse_implementation_manifest()`：解析实现 + 解析环境变量；输出 `sha256`。
4. `processing_manifest()`：全量代码/配置 + `parse_implementation_manifest()`；输出完整执行清单。

`PARSE_DEPENDENCIES` 现包含 `pdfplumber`、`pdfminer.six`、`pymupdf`、`rapidfuzz`、`paddleocr`、`paddlepaddle`、`Pillow`、`numpy`、`opencv-python`。`rapidfuzz` 参与表题/表头匹配，`pdfminer.six` 是 pdfplumber 的底层版面解析。

### 3.2 解析器指纹与配置指纹

```text
PARSER_FINGERPRINT =
  "pdfplumber-cells-v31-parse-identity-" + parse_implementation_manifest()["sha256"][:16]
```

- v31 标记整改后的身份方案，隔离整改前的 v30 运行。
- 只有解析实现/依赖/解析配置变化才会改变该指纹。

`processing_config_fingerprint()` 只拼接 `parse_implementation_manifest()["sha256"]`；`document-binding-v1`、`column-mapping-v1`、`manual-overlay-v2` 等证据后缀照旧追加。

### 3.3 解析完成与后续证据变化

`pdf_parser.build_parse_artifact_from_handle()`：

1. artifact 的 `processing_manifest` 只写 **解析来源清单**（`parse_implementation_manifest()`），不再写全量运行清单。
2. `config_fingerprint` 使用解析实现清单。
3. artifact 字段齐备后调用 `refresh_parse_identity(artifact)`。

统一重算点：

| 触发点 | 进入身份的内容 |
| --- | --- |
| 文档归属绑定 | `config_fingerprint` 追加 `document-binding-v1:<binding hash>`；语义清单含 `document_binding` |
| 人工列映射 | `config_fingerprint` 追加 `column-mapping-v1:<mapping digest>`；`logical_tables.column_mappings` 进入语义哈希 |
| 人工格/行/表种补录 | `config_fingerprint` 追加 `manual-overlay-v2:<manual digest>`；单元格与 `manual_*` 证据进入语义哈希 |
| 原始解释保存 | `raw_interpretation` 进入语义哈希，保存后立即重算身份 |
| OCR 候选提升 / 分块管线 | 单元格、候选与资格证据进入语义哈希，管线结束重算 |

`bind_artifact_document_identity()` 保持早退：同一绑定重复调用不改变身份。同样的列映射/补录重复应用不改变身份。

## 4. 解析实现清单与导入守卫

`PARSE_IMPLEMENTATION_FILES` 当前 33 个文件（含导入时实际执行的父包初始化与 `api/config.py`）：

- 解析入口与表结构：`pdf_parser.py`、`fiscal_table_rules.py`、`fiscal_row_roles.py`、`fiscal_column_identity.py`、`input_geometry.py`、`source_reconstruction.py`、`table_reading_assessment.py`、`raster_grid_candidates.py`。
- 原生恢复：`native_dimension_recovery.py`、`native_label_visibility.py`、`native_structure_recovery.py`、`native_ocr_fusion.py`。
- OCR：`ocr_client.py`、`ocr_pipeline.py`、`ocr_preprocessing.py`、`ocr_tiles.py`、`ocr_grid_reconstruction.py`、`ocr_qualification.py`、`ocr_fact_promotion.py`、`ocr_tiled_pipeline.py`，以及 `ocr_service/app.py`、`ocr_service/protocol.py`。
- schema 与纯函数：`parse_artifact.py`、`source_reconstruction.py`（schema）、`engine/fiscal_lexeme.py`、`cell_identity.py`、`input_processing_identity.py`。

明确排除：规则/诊断（`rules_v33.py`、`check_obligations.py`、`rule_identity.py`、`common_rules.py`）、回执/展示（`check_receipt.py`、`table_import_receipt.py`、复核/导出/AI）、入库旧表（`table_recognizer.py`、`parse_artifact_store.py`）。

结构拆分保持：

- `CheckReceipt`、`SUB_CHECK_STATUSES` 在 `src/schemas/check_receipt.py`，`parse_artifact.py` 兼容导出。DH-02 新字段应落到 `check_receipt.py`。
- `source_cell_id()` 在 `src/services/cell_identity.py`，`table_import_receipt.py` 兼容导出。

导入守卫 `local_import_dependencies()`：

- 解析 `ast.Import`、`ast.ImportFrom`，含函数内延迟导入。
- 支持 `src.*`、`ocr_service.*` 绝对导入，以及按模块所在包解析的 `from .` / `from ..` 相对导入。
- 按 CPython 规则解析同名文件/包：`foo.py` 优先于 `foo/__init__.py`。
- 返回目标包 `__init__.py` 和必要父包链初始化文件；不再整体跳过包。
- 非解析例外在 `NON_PARSE_LOCAL_IMPORTS` 显式登记并说明边界：`check_receipt.py`（回执 schema 拆分）、`job_orchestrator.py`（`src.services.__getattr__` 惰性属性）、`ai_model_settings.py`（`AppConfig.load()` 内惰性导入）。
- 回归覆盖：单文件与包形式的绝对/相对/延迟导入、`from package import symbol`、父包变化、同名文件/包优先级。

新增解析模块必须同时登记并递增 `PARSE_IMPLEMENTATION_VERSION`；影响解析的包初始化变化会改变解析实现指纹。

## 5. 缓存与不可变存储

### 5.1 缓存键与冲突判定

`parse_runs` 唯一索引仍是：

```text
(document_version_id, source_file_hash, parser_fingerprint,
 config_fingerprint, COALESCE(ocr_model_fingerprint, ''))
WHERE status = 'completed'
```

`run_structured_ingest()` 命中缓存后：

- 用 `artifacts_share_semantic_identity()` 比较原件、解析实现、配置、OCR 与 `semantic_artifact_hash`。
- 仅审计/诊断清单差异：接受缓存运行，不产生 `cache_key_conflict`。
- 语义内容/资格证据不同：写 `cache_conflicts` 日志并拒绝。
- 解析完成读回时同样按语义身份校验，不再要求完整 `to_dict()` 逐字相等。

`persist_parse_artifact()`：

- 同一 `parse_run_uid` 的快照哈希不同时，读回已有快照并比较语义身份。
- 语义相同：保留原不可变快照与审计字段，不覆盖。
- 语义/输入不同：拒绝，防止同一 UID 绑定两个不同产物。

### 5.2 分析执行指纹

`src/services/analysis_fact_snapshot.py`：

- `freeze_execution_manifest()`：分析开始时冻结 `{schema_version, processing_manifest()}`。
- `compute_execution_fingerprint()`：完整执行清单 + 原件 + `parse_run_uid` + 完整/语义产物哈希。
- `make_snapshot(..., execution_manifest=...)` 写入 `execution_manifest`、`execution_fingerprint`、`semantic_artifact_hash`；`SNAPSHOT_VERSION` 升到 v2，v1 旧快照仍可校验读取。
- `snapshot_execution_provenance()` 只返回快照中保存的清单与指纹，**绝不调用当前 `processing_manifest()` 重算**。
- `validate_snapshot()` 对 v2 校验执行指纹内部一致；篡改清单或指纹即使重算快照哈希也会被拒绝。
- `verify_consumed_snapshot()` 使用快照内保存的执行清单重建，v1 快照显式拒绝，不偷偷使用当前代码。

生产接线：

- `api/main.py` 在分析开始冻结一次 `execution_manifest`，主链快照与 `run_structured_ingest()` 使用同一份。
- `structured_ingest_runner` 将其传给 `make_snapshot`，入库回执带 `execution_fingerprint` / `semantic_artifact_hash`。
- `GET /api/jobs/{job_id}/fact-snapshot` 返回 `execution_provenance`；`include_execution=true` 时返回完整冻结清单。

### 5.3 旧历史

- `parse_runs.parse_run_uid` 唯一；`ON CONFLICT` 不允许跨文档版本覆盖。
- `_restore_snapshot()` 校验快照哈希、完整字段和身份字段。
- 旧运行不删除、不改写；v31 与 `PARSE_IDENTITY_VERSION v2` 隔离旧缓存。
- 旧 `artifact_content_hash[:32]` 与旧四字段身份不会被重新签发；旧 v1 分析快照仍可读取，但明确没有冻结执行清单。

## 6. 变化影响矩阵

| 变化 | 语义产物身份 / `parse_run_uid` | 解析缓存键 | 分析运行指纹 |
| --- | --- | --- | --- |
| 单元格金额/原文/资格证据 | 改变 | 不变（冲突检查拒绝） | 改变 |
| `reading_assessment` 读取缺口 | 改变 | 不变（冲突检查拒绝） | 改变 |
| `label_visibility` 汇总、`reconstruction_decisions` | 不变 | 不变 | 完整内容哈希改变 |
| 规则/诊断/回执代码 | 不变 | 不变 | 改变 |
| 解析实现文件 | 改变 | 改变 | 改变 |
| 解析环境配置 | 改变 | 改变 | 改变 |
| 解析依赖版本 / Python 版本 | 改变 | 改变 | 改变 |
| 原件字节 | 改变 | 改变 | 改变 |
| OCR 模型指纹 | 改变 | 改变 | 改变 |
| 文档归属绑定 | 改变 | 改变 | 改变 |
| 人工列映射 / 格行补录 | 改变 | 改变 | 改变 |

## 7. 验证

`tests/remediation/test_parse_run_identity_foundation.py` 覆盖：

1. 相同输入与条件重复运行身份一致，重算幂等。
2. 单元格内容 100.00→999.00：身份改变，旧上下文被拒绝。
3. `reading_assessment` 读取缺口：身份改变，事实从 `unverified` 降为 `low_confidence`。
4. 仅审计/展示字段变化：身份稳定，完整内容哈希改变。
5. 诊断代码变化：解析身份稳定，执行指纹改变且随快照冻结。
6. 真实重复入库：审计清单差异不冲突，语义变化仍报 `cache_key_conflict`。
7. 解析实现、配置、`rapidfuzz`/`pdfminer.six` 依赖、原件、parser/OCR/schema 版本变化均被发现。
8. 文档绑定、列映射、人工补录证据变化被发现；同样证据重复应用幂等。
9. 旧内容哈希/四字段身份不重发；旧不可变快照不被冒充。
10. 导入守卫拒绝 `ocr_service` 绝对、相对、`import` 与延迟导入四种未登记模块。
11. 真实 PDF 连续解析两次身份一致，artifact 只保存解析来源清单。

本地结果：

```text
tests/remediation/test_parse_run_identity_foundation.py  39 passed
全量 pytest  4961 passed, 195 skipped
验收方复现脚本（新目录）  ID-01～ID-04 反例全部按预期翻转
残余反例复跑（新目录）  src/utils 辅助模块进入执行清单；包绝对/相对导入均被守卫拒绝
```

## 8. 审核整改对照

| 审核项 | 整改 |
| --- | --- |
| ID-01 身份失去内容/资格约束 | 新增 `semantic_artifact_hash`，纳入原文、单元格、几何/映射、主体、`reading_assessment` 与资格证据；`parse_run_uid` 绑定该哈希；旧上下文因 UID 改变被拒绝 |
| ID-02 诊断代码变化仍导致缓存冲突 | artifact 不再写全量 `processing_manifest`；缓存冲突/读回/持久化改用语义身份，审计清单差异不冲突，真实语义变化仍拒绝 |
| ID-03 完整运行指纹不能证明历史版本 | 冻结 `execution_manifest` 到 v2 分析快照，保存 `execution_fingerprint`；历史读取只返回快照内清单，禁止当前代码重算；主链与入库共用同一份冻结清单。复审后执行清单扩到 `src`（含 `src/utils/narration.py` 等规则辅助）、`api`、`config`、`rules`、`ocr_service`、`gbc_yaml` 及动态规则/依赖配置 |
| ID-04 依赖与守卫不完整 | 补 `rapidfuzz`、`pdfminer.six`；守卫支持 `src`/`ocr_service` 绝对导入、相对导入与延迟导入。复审后补齐目标包 `__init__.py`、必要父包链、同名文件/包优先级与 `from package import symbol`；非解析惰性导入显式登记 |

## 9. 合并与基线注意

1. DH-02 给 `CheckReceipt` 加字段时落到 `src/schemas/check_receipt.py`，不要放回 `parse_artifact.py`；`from_dict` 的读回处理一并迁移。
2. 解析器指纹升到 `v31`，旧 v30/v29 缓存不会被新实现命中；旧运行和旧快照保留。
3. 本候选不采正式基线。审核通过、r5 冻结后，再在同一份最终代码和运行条件上重建两次基线，并重采三个阶段证据。
4. 旧基线、最新 HEAD 和旧采集不能混作一个基线；身份方案变化本身就会使新旧 `parse_run_uid` 不可逐字比较。
