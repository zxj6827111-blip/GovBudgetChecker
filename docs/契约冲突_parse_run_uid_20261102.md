# 契约冲突报告：DH-02 代码改动与 I3 的 parse_run_uid 不变量

## 1. 结论

DH-02 的 22 项比较中，21 项为 PASS；唯一 FAIL 是 I3 的 **RA-002 8 处 `cell_observations[].parse_run_uid` 改变**。这不是业务结论或解析内容变化，而是 `runtime_code_manifest()` 把 `src/engine/`、`src/schemas/`、`src/services/`、`api/main.py`、`api/runtime.py` 的**全部代码哈希**计入 `parse_run_uid` 的直接结果。任何 DH-02 代码改动都会改变该身份。

按提示词“parse_run_uid 继续参与比较，不能加入易变字段白名单来掩盖起步代码或解析产物漂移”和“发现契约冲突时，保存最小复现并交验收方处理，不能自行绕过”，本项不自行修复、不改基线、不白名单，提交验收方裁决。

## 2. 最小复现

```bash
# 起步提交代码
rm -rf /tmp/gbc_baseline_code && mkdir -p /tmp/gbc_baseline_code
git -C "/Volumes/workSSD/software development/GovBudgetChecker" archive c6cd033d | tar -x -C /tmp/gbc_baseline_code

# 基线 processing_manifest
cd /tmp/gbc_baseline_code
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 DATABASE_URL= \
  "/Volumes/workSSD/software development/GovBudgetChecker/.venv/bin/python" -c \
  "import sys,json;sys.path.insert(0,'.');from src.services.input_processing_identity import processing_manifest;m=processing_manifest();print(m['sha256'])"
# c4d9e832d7126db8be3a5ebfee1d679ee28c2ca4c96b730d99e724543cbdbfdd

# DH-02 代码
cd "/Volumes/workSSD/software development/gbc-worktrees/pending-review-dh0203-20261102"
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 DATABASE_URL= \
  "/Volumes/workSSD/software development/GovBudgetChecker/.venv/bin/python" -c \
  "import sys,json;sys.path.insert(0,'.');from src.services.input_processing_identity import processing_manifest;m=processing_manifest();print(m['sha256'])"
# 12e6d1d90177e45ef11a6c2fca6fa9cfdc000bd5763190916126b0016460e468
```

两次 `runtime_code_manifest()['code']` 的差异文件共 21 个（DH-02 改动文件）：

```
api/main.py
src/engine/check_obligations.py
src/engine/diagnostics.py
src/engine/fact_context.py
src/engine/native_final_disclosure.py
src/engine/native_hierarchy_checks.py
src/engine/native_income_sources.py
src/engine/native_no_activity.py
src/engine/native_summary_checks.py
src/engine/native_table_arithmetic.py
src/engine/native_table_text.py
src/engine/native_three_public_ratios.py
src/engine/pipeline.py
src/engine/rule_outcome.py
src/engine/rules_v33.py
src/schemas/material_detail.py
src/schemas/parse_artifact.py
src/schemas/review_lifecycle.py
src/services/engine_rule_runner.py
src/services/material_detail_query_service.py
src/services/review_lifecycle_service.py
```

## 3. 代码链

- `src/services/input_processing_identity.py::runtime_code_manifest()` 对上述目录全部 `*.py` 做 sha256；
- `processing_manifest()['sha256']` 进入 `processing_config_fingerprint()`；
- `PARSER_FINGERPRINT = "pdfplumber-cells-v29-label-visibility-" + processing_manifest()["sha256"][:16]`；
- `artifact.parse_run_uid = artifact_content_hash(artifact)[:32]`，其中 `processing_manifest` 参与内容哈希；
- 空表/无活动回执的 `cell_observations` 带 `parse_run_uid`，因此 I3 逐字段比较时判出 8 处变化。

## 4. 为什么执行方不自行修复

1. 把 `parse_run_uid` 加入 `volatile_fields.json` 或比较器白名单，直接违反提示词与验收包 §15.3。
2. 收窄 `runtime_code_manifest()` 的哈希范围会改变所有文档的一次性 `parse_run_uid`；起步基线已按原范围采集两次并自比较通过，不能在不重新采集基线的情况下单方面更换身份算法。
3. 重新采集基线必须更换起步提交/运行条件，违反“基线采集开始后不更换起点”。
4. 为保持旧值而冻结/硬编码 manifest 会掩盖未来真实解析器漂移。

## 5. 请验收方裁决的选项

- **A（推荐，需重新封包与重采基线）**：把 `runtime_code_manifest()` 收窄为“只含解析实现文件”，并在收窄后的代码上重新采集两次基线；DH-02 及后续阶段在该基线上比较。这样规则/说明类代码改动不再改变解析身份。
- **B（需修订契约）**：验收方明确接受“DH-02 代码改动导致的 parse_run_uid 一次性变化”为 REVIEW，并在验收报告中登记；但这与当前 I3 FAIL 及“不白名单”条款冲突，需验收方文本化确认。
- **C（不推荐）**：把诊断代码移出 manifest 覆盖目录并以非常规加载方式注入；维护性与可审计性不可接受，执行方不采用。

## 6. 证据位置

- 比较报告：`<EVID>/compare_DH-02.json`
- 比较日志：`<EVID>/logs/compare_DH-02.log`
- 基线：`<EVID>/baseline_A`、`<EVID>/baseline_B`
- DH-02 采集：`<EVID>/after_DH-02`
- manifest 差异数据：`/tmp/manifest_baseline.json`、`/tmp/manifest_current.json`（可重建）
