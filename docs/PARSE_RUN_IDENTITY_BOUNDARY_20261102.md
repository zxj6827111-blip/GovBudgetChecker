# parse_run_uid 身份基础：调用链与边界（2026-11-02）

**候选基线与分支**

- 基线提交：`c6cd033d79f907a4541af7036f3d8dc49a140cd2`
- 候选分支：`codex/parse-run-identity-20261102`
- 独立工作区：`gbc-worktrees/parse-run-identity-20261102`
- 范围：只修 `parse_run_uid` 身份基础，不包含 DH-02/DH-03、不修改 r4、不实施方案 B、不采正式基线、不运行正式抽样。

## 1. 目标与结论

原先 `parse_run_uid` 由整份 `ParseArtifact` 内容哈希生成，`PARSER_FINGERPRINT` 又嵌入全仓代码清单。结果是：

- 只改诊断、展示或规则代码，解析身份也会变；
- 只改 `reading_assessment`、`label_visibility` 等展示字段，解析身份也会变；
- 新旧代码之间同一份 PDF 的 `parse_run_uid` 改变，被 I3 当作回执字段漂移判出。

本次把两类身份分开：

| 身份 | 用途 | 变化条件 |
| --- | --- | --- |
| `parse_run_uid`（解析身份） | 解析缓存复用、事实与证据绑定、旧运行隔离 | 原件、解析实现、解析配置、解析依赖、OCR 模型、schema/身份方案版本、文档绑定/列映射/人工补录证据变化 |
| `full_run_fingerprint`（完整运行指纹） | 审计、快照完整性、跨运行比对 | 上述任一变化，或诊断/展示/规则代码、展示字段、完整代码清单变化 |

`parse_run_uid` 不再读取 artifact 内容。内容差异由缓存边界的 `artifact_content_hash` 冲突检查兜底，不由身份冒充。

完整运行指纹不新增数据库列：分析快照已保存 `artifact_content_hash` 与 `processing_manifest`，`full_run_fingerprint()` 由这两项加当前完整实现清单重算，既可用于审计，也可用于跨运行比较。

## 2. 身份生成调用链

### 2.1 两个代码清单

`src/services/input_processing_identity.py`：

1. `runtime_code_manifest()`
   - 冻结进程启动时的全量代码：`src/services`、`src/engine`、`src/schemas`、`src/qc`、`ocr_service` 下所有 `*.py`，加 `api/main.py`、`api/runtime.py`、`src/db/migrations.py`。
   - 同时记录 `config/app.yaml`、`config/providers.yaml`、`rules/v3_3.yaml`、`rules/budget_v3_3_draft.yaml` 的摘要。
   - 只服务完整运行指纹与审计，不进入解析身份。

2. `parse_code_manifest()`
   - 只哈希 `PARSE_IMPLEMENTATION_FILES` 中登记的文件。
   - 记录 `PARSE_DEPENDENCIES`（`pdfplumber`、`pymupdf`、`paddleocr`、`paddlepaddle`、`Pillow`、`numpy`、`opencv-python`）和 Python 版本。
   - 进程首次调用后 `lru_cache` 冻结；运行中改盘不能把未重载进程冒充新实现。

3. `parse_implementation_manifest()`
   - 在 `parse_code_manifest()` 上加入解析相关环境变量（`_FLAGS`）。
   - 输出 `sha256`，是解析实现指纹的唯一来源。

4. `processing_manifest()`
   - 全量代码 + 全部环境变量 + `parse_implementation_manifest()`。
   - 输出 `sha256`，是完整运行清单。

### 2.2 解析器指纹与配置指纹

`src/services/pdf_parser.py`：

```text
PARSER_FINGERPRINT =
  "pdfplumber-cells-v30-parse-identity-" + parse_implementation_manifest()["sha256"][:16]
```

- 版本前缀从 `v29` 升到 `v30`，显式标记身份方案边界。
- 只有解析实现清单变化才会改变该指纹；规则、诊断、展示代码变化不会。

`processing_config_fingerprint(value)`：

- 只拼接 `parse_implementation_manifest()["sha256"]`，默认不再读取完整 `processing_manifest()`。
- 调用方传入的配置前缀保留，`document-binding-v1`、`column-mapping-v1`、`manual-overlay-v2` 等证据后缀按原有规则追加。

### 2.3 解析完成时的身份签发

`pdf_parser.build_parse_artifact_from_handle()`：

1. `processing_manifest()` 写入 artifact，用于完整运行指纹与审计。
2. `parse_implementation_manifest()` 只用于本次 `config_fingerprint`。
3. artifact 字段齐备后调用 `refresh_parse_identity(artifact)`，不再使用 `artifact_content_hash(artifact)[:32]`。

`refresh_parse_identity()` 的实现：

```text
sha256(canonical_json({
  version: "gbc.parse-run-identity.v1",
  source_file_hash,
  parser_name,
  parser_fingerprint,
  config_fingerprint,
  ocr_model_fingerprint,
  schema_version,
}))[:32]
```

### 2.4 后续证据变化的统一重算点

以下位置全部改为调用 `refresh_parse_identity()`，不再各自拼接：

| 触发点 | 位置 | 进入身份的字段 |
| --- | --- | --- |
| 文档归属绑定 | `input_processing_identity.bind_artifact_document_identity()` | `config_fingerprint` 追加 `document-binding-v1:<binding hash>` |
| 人工列映射 | `engine/fact_context.py:apply_table_column_mapping_overlays()` | `config_fingerprint` 追加 `column-mapping-v1:<mapping digest>` |
| 人工格/行/表种补录 | `services/manual_cell_overlay.py:apply_table_cell_overlays()` | `config_fingerprint` 追加 `manual-overlay-v2:<manual digest>` |
| OCR 候选提升 | `services/ocr_fact_promotion.py` | 提升后重算；`parser_fingerprint` 可能已带 OCR 后缀 |
| OCR 分块管线 | `services/ocr_tiled_pipeline.py` | 管线结束统一重算 |

`bind_artifact_document_identity()` 的早退语义保留：同一绑定重复调用不改变身份。

## 3. 解析实现清单边界

`PARSE_IMPLEMENTATION_FILES` 当前包含 27 个文件：

- 解析入口与表结构：`pdf_parser.py`、`fiscal_table_rules.py`、`fiscal_row_roles.py`、`fiscal_column_identity.py`、`input_geometry.py`、`source_reconstruction.py`、`table_reading_assessment.py`、`raster_grid_candidates.py`。
- 原生恢复：`native_dimension_recovery.py`、`native_label_visibility.py`、`native_structure_recovery.py`、`native_ocr_fusion.py`。
- OCR：`ocr_client.py`、`ocr_pipeline.py`、`ocr_preprocessing.py`、`ocr_tiles.py`、`ocr_grid_reconstruction.py`、`ocr_qualification.py`、`ocr_fact_promotion.py`、`ocr_tiled_pipeline.py`，以及 `ocr_service/app.py`、`ocr_service/protocol.py`。
- schema 与纯函数：`parse_artifact.py`、`source_reconstruction.py`（schema）、`engine/fiscal_lexeme.py`、`cell_identity.py`、`input_processing_identity.py`。

明确排除：

- 规则与诊断：`src/engine/rules_v33.py`、`check_obligations.py`、`rule_identity.py`、`common_rules.py` 等。
- 回执与展示：`src/schemas/check_receipt.py`、`table_import_receipt.py`、复核工作台、导出和 AI 链路。
- 仅影响入库/旧表的 `table_recognizer.py`、`parse_artifact_store.py`。

结构拆分：

- `CheckReceipt`、`SUB_CHECK_STATUSES` 移到 `src/schemas/check_receipt.py`；`parse_artifact.py` 保留同名导出，既有导入路径不变。DH-02 后续给回执加字段只改 `check_receipt.py`，不再碰解析 schema。
- `source_cell_id()` 移到 `src/services/cell_identity.py`；`table_import_receipt.py` 保留兼容导出。解析只用稳定的格 ID 拼法，不再把整份回执模块带进解析指纹。

守卫测试 `test_parse_manifest_covers_parse_module_import_edges()` 扫描清单内文件的全部本地导入（含函数内延迟导入），只允许一个非解析消费方：

- `src/schemas/check_receipt.py`（回执/诊断 schema，已显式拆分）

新增解析模块未登记时，该测试会失败；登记时必须同时递增 `PARSE_IMPLEMENTATION_VERSION`。

## 4. 缓存与不可变存储边界

### 4.1 缓存键

`parse_runs` 唯一索引：

```text
(document_version_id, source_file_hash, parser_fingerprint,
 config_fingerprint, COALESCE(ocr_model_fingerprint, ''))
WHERE status = 'completed'
```

`structured_ingest_runner.run_structured_ingest()`：

1. 调用方已带 artifact 时，以其自身 `parser_fingerprint/config_fingerprint/ocr_model_fingerprint` 查询；否则用当前进程指纹。
2. `find_cached_parse_run()` 只命中已完成运行。
3. 命中后完整读回 artifact，并比较 `artifact_content_hash(parse_artifact)` 与缓存对象：
   - 不同：写入 `uploads/<job>/cache_conflicts/` 冲突日志，抛 `cache_key_conflict`；
   - 相同：复用运行，不重开 PDF。
4. 未命中：真正解析一次，`refresh_parse_identity()` 后写入 `parse_runs`。

### 4.2 不可变存储

- `parse_runs.parse_run_uid` 唯一；`ON CONFLICT` 只允许同一 `document_version_id` 的空更新，不覆盖其他版本。
- 写入后校验 `artifact_snapshot_hash`；同一 UID 绑定不同快照直接拒绝。
- `_restore_snapshot()` 校验快照哈希、完整字段和身份字段（`source_file_hash`、`parse_run_uid`、`parser_fingerprint`、`config_fingerprint`、`ocr_model_fingerprint`、`page_count`）。
- 旧运行不删除、不改写；新身份方案通过 `PARSE_IDENTITY_VERSION` 和 v30 解析器前缀与旧运行隔离。

### 4.3 旧身份不会被重新签发

身份 basis 增加 `version`、`parser_name`、`schema_version`，因此：

- 旧 `artifact_content_hash(artifact)[:32]` 身份；
- 旧四字段 `sha256(source|parser|config|ocr)[:32]` 身份；

都不会被新实现对同一份 artifact 重新签发。测试 `test_legacy_identity_schemes_are_not_reissued()` 与 `test_old_immutable_run_cannot_be_overwritten_by_new_identity()` 固定这两条。

## 5. 变化影响矩阵

| 变化 | `parse_run_uid` | 解析缓存键 | `full_run_fingerprint` |
| --- | --- | --- | --- |
| 诊断/展示字段（`reading_assessment`、`label_visibility`、`reconstruction_decisions`） | 不变 | 不变 | 改变 |
| 规则/诊断/回执代码 | 不变 | 不变 | 改变 |
| 解析实现文件 | 改变 | 改变 | 改变 |
| 解析环境配置 | 改变 | 改变 | 改变 |
| 解析依赖版本 / Python 版本 | 改变 | 改变 | 改变 |
| 原件字节 | 改变 | 改变 | 改变 |
| OCR 模型指纹 | 改变 | 改变 | 改变 |
| 文档归属绑定 | 改变 | 改变 | 改变 |
| 人工列映射 / 格行补录 | 改变 | 改变 | 改变 |

## 6. 验证

新增 `tests/remediation/test_parse_run_identity_foundation.py`，覆盖：

1. 相同输入与条件重复运行身份一致，重算幂等。
2. 仅诊断/展示字段变化：解析身份稳定，完整运行指纹改变。
3. 仅诊断代码变化：解析身份稳定，完整运行指纹改变。
4. 回执 schema 变化：解析身份稳定，完整运行指纹改变。
5. 解析实现、配置、依赖、原件、parser/OCR/schema 版本变化均被身份发现。
6. 文档绑定、列映射、人工补录证据变化均被身份发现；同样证据重复应用幂等。
7. 旧内容哈希身份、旧四字段身份不会被重新签发。
8. 旧不可变解析快照不能被新身份覆盖或冒充。
9. 解析清单导入边界守卫。
10. 真实 PDF 连续解析两次，`parse_run_uid` 一致。

本地结果：

```text
tests/remediation/test_parse_run_identity_foundation.py  14 passed
受影响回归集合（身份/回执/入库/人工补录/OCR 空白格）  211 passed, 1 skipped
ruff check src/ 与新增测试  通过
```

## 7. 合并与基线注意

1. DH-02 当前在 `src/schemas/parse_artifact.py` 给 `CheckReceipt` 加字段；合并本候选时，应把这些字段落到 `src/schemas/check_receipt.py`，不要把回执 schema 重新放回 `parse_artifact.py`。
2. 本候选把解析器指纹升到 `v30`，旧 `v29` 缓存不会被新实现命中；旧运行和旧快照保留。
3. 本候选不采正式基线。审核通过、r5 冻结后，再在同一份最终代码和运行条件上重建两次基线，并重采三个阶段证据。
4. 旧基线、最新 HEAD 和旧采集不能混作一个基线；身份方案变化本身就会使新旧 `parse_run_uid` 不可逐字比较。
