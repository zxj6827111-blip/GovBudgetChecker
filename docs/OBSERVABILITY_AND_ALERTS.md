# 可观测性与告警阈值

- 编制日期：2026-08-26
- 关联缺口：`P2-01` / `B-05`（结构化日志）、`B-06`（关键指标与告警钩子）
- 关联实现：`src/utils/logging_config.py`、`src/services/metrics.py`、`api/routes/metrics.py`
- 与 `docs/PRODUCTION_HARDENING.md` 第 4 节「监控告警建议」配套：那节给方向，本文给可直接配置的阈值与处置动作。

## 一、结构化日志

### 开关与格式

| 变量 | 默认 | 说明 |
|---|---|---|
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `LOG_JSON` | 自动 | 未配置时按 stdout 是否为 TTY 判断：容器/管道 → JSON，本地终端 → 人类可读 |
| `LOG_FILE` | 空 | 可选，额外写入的文件路径（父目录自动创建） |

装配入口只有两处，不要在别处再调 `setup_logging`：

- API：`api/main.py` 的 `_app_lifespan` → `configure_logging_from_env("api")`
- Worker：`api/worker.py` 的 `main()` → `configure_logging_from_env("worker")`

`TESTING=true` 时默认跳过装配（`setup_logging` 会清空 root handler，会打断 pytest 采集）。

### job_id 关联

所有任务相关日志都能按 `job_id` 检索：

- `api/main.py:_run_pipeline` / `_run_pipeline_inner` 用 `log_context(job_id=...)` 绑定，
  绑定基于 `contextvars`，队列并发多任务时不会互相串字段；
- `api/main.py:_safe_write` 是状态流转的唯一出口，在这里统一 `log_job_stage`，
  覆盖 `开始解析文档 / 解析PDF内容 / 构建文档对象 / 双模式分析 / 执行规则检查 / 结构化入库 / 完成（…）` 等全部阶段；
- `api/job_queue.py:_worker_loop` 在执行前绑定 `job_id` 与 `worker_index`。

常用检索字段：`job_id`、`stage`、`job_status`、`progress`、`page_coverage`、
`scanned_page_count`、`quality_status`、`analysis_conclusion`、`review_reason_codes`。

### 敏感内容红线

`redact_log_fields` 在写入侧强制替换，`StructuredFormatter` 再做一次兜底：
命中敏感键时丢弃原值，只保留 `<key>_len` 与 `<key>_sha256`（前 12 位）。

覆盖的键包括凭据类（`api_key` / `authorization` / `password` / `token` / `cookie` / `secret`…）
与材料原文类（`evidence_text` / `text_snippet` / `page_text(s)` / `pdf_text` / `raw_text` / `prompt` / `content` / `snippet`），
以及以 `_password` / `_secret` / `_token` / `_api_key` / `_text` / `_prompt` 结尾的任意键。

> 注意：在 `log_context` 内使用 `logger.xxx(extra=...)` 时必须用 `safe_log_extra(...)` 构造，
> 否则与上下文同名的键会让 `logging.makeRecord` 抛 `KeyError`。

### message 字段的边界与静态门禁（Task C）

`redact_log_fields` 只作用于 `extras`。`record.getMessage()` 是 Python logging 的固有边界，
**不经过任何脱敏**就进入 JSON 的 `message` 字段。因此：

- 硬规则：**不得**把整条 finding / 整行表格数据 / 单元格原值 / AI 响应体拼进 message；
  这类信息只能走 `extra=`，或先用 `fingerprint_for_log()` 换成 `len + sha256`。
- 抛出的异常消息同样受约束：它会顺着上游的 `logger.error("...%s", e)` 落到 message。
  `raise Exception(f"返回格式错误: {result}")` 这种写法一律改为带指纹。
- 校验类异常（pydantic `ValidationError`）会把**输入值**回显在异常消息里。这类路径用
  `describe_exception(exc)`：保留 `error_type` + 字段路径 `loc` + 错误码 `type` +
  消息指纹，丢掉消息原文；并且刻意**不用** `logger.exception`，因为异常栈里带着同一段消息。
- 静态门禁：`python scripts/check_log_message_safety.py`（已接入 `make lint` 与 CI）。
  规则见脚本 docstring；`tests/test_log_message_safety.py` 里有正反对照用例
  与"全仓零违规"回归线。

#### 门禁为什么是 fail-closed（2026-08-27 独立复核后改造）

第一版门禁用"危险名字黑名单"，并据此宣布全仓 0 违规。**这个结论是错的**：
`src/engine/ai/extractor_client.py` 有 4 处 `logger.warning(f"...: {hit}")`，而 `hit`
的必需字段就包含 `budget_text` / `final_text` / `stmt_text`（送检材料原文）——只因为
`hit` 不在黑名单里就被放过。独立复核抓到了这 4 处。

复核后用旧门禁交叉验证：把这 4 种写法喂给它，返回 **0 违规**；改造后的门禁全部命中，
而 `f"job {job_id} done in {elapsed_ms}ms"` 这类排障标量在新旧两版都是 0。
结论是**原理性缺陷**，不是漏改代码，所以改成 fail-closed：

    裸变量插进 message ⇒ 默认违规，除非该名字被判定安全。

判定安全只有三条来源：`SAFE_LOG_NAMES`（逐个登记、每条带理由）、机械安全后缀/前缀
（`*_id` / `*_count` / `*_ms` / `is_*` …）、全大写配置常量。两条红线不可被白名单覆盖：
`is_sensitive_log_key` 命中的名字，以及 `RISKY_OBJECT_NAMES`。
`tests/test_log_message_safety.py` 有一致性不变量测试，防止后人往白名单里塞
`text` / `hit` 把门禁静音。

改造后全仓新暴露 2 处待判定站点（`api/runtime.py` 的 `target`、
`src/db/migrations.py` 的 `description`），均确认安全，但都改成了更精确的名字
（`target_path` / `migration_description`）而不是把 `target` / `description` 这种泛名
放进白名单——`description` 在别处可能承载含金额的 finding 描述。

**已知残留风险（发布决策需知晓）**：

1. 非校验类异常仍保留 `logger.exception`，异常栈进入 JSON 的 `exception` 字段，
   该字段不脱敏。若第三方库把材料内容写进自己的异常消息，仍可能经此路径落盘。
   取舍理由是异常栈对生产排障必需；缓解手段是本仓库自己抛出的异常消息一律不含原文。
2. **`raise` 路径仍是黑名单，不是 fail-closed。** 实测异常消息里的裸变量共 87 处、
   39 个名字，几乎全是配置值与 SQL 标识符（`model`、`timeout`、`table`、`col`、
   `migration_id`）；一律 fail-closed 只出噪声，且原文要落盘必须先经过某个 logger，
   而那一侧已经 fail-closed。代价：若将来 `raise ValueError(f"bad {new_obj}")` 且
   `new_obj` 不在 `RISKY_RAISE_NAMES` 里，门禁不报，只能靠 code review。
   这个取舍有专门的测试（`test_raise_path_stays_narrow_on_purpose`）钉住，
   将来若要改成 fail-closed 会先在那里变红。
3. 门禁只看**语法形态**，不做取值分析。`safe = row["snippet"]` 之后再打印 `safe`
   仍绕得过去（`safe` 需要登记，但登记时看不出来源）；`{obj.field}` 只要 `field`
   不在敏感键口径内就放过。
4. 覆盖范围是 `api` / `src` / `scripts` 的 Python 代码，不含前端与 SQL。

## 二、指标端点

### 为什么用端点而不是只做日志聚合

结构化日志解决"事件可检索"，但"当前积压多少、`review_required` 占比多少"这类**即时态势**
需要外部日志管道先落地并写查询，本轮无法在仓库内自证。端点方案可被测试直接断言、
可被 Prometheus 抓取，与日志是叠加关系：**明细看日志，态势看端点**。

### 访问方式与鉴权

`GET /metrics`、`GET /api/metrics`，支持 `?format=prometheus` 输出文本暴露格式。

鉴权是三道叠加，缺一不可：

1. `SecurityMiddleware` 的 API Key（该路径**不在**免认证白名单里）；
2. 管理员会话（`X-Session-Token` 对应管理员），沿用 `/ready?details=true` 的先例；
3. 或独立抓取令牌 `METRICS_API_TOKEN`，采集器用 `X-Metrics-Token` 提交
   （给采集器单独发令牌，避免把管理员口令写进 Prometheus 配置）。

| 变量 | 默认 | 说明 |
|---|---|---|
| `METRICS_ENABLED` | `true` | 设为 `false` 时端点返回 404（只走日志聚合的部署） |
| `METRICS_API_TOKEN` | 空 | 采集器专用令牌；不配置则只能用管理员会话 |
| `METRICS_CACHE_TTL_SECONDS` | `15` | 采集结果缓存秒数，防止高频抓取反复全量扫盘 |
| `METRICS_MAX_JOBS` | `5000` | 单次扫描的任务目录上限 |

### 数据来源

只读 `UPLOAD_DIR/<job_id>/status.json` 与 `UPLOAD_DIR/.worker-heartbeats/*.json`，
不新增存储、不写任何文件。任务产物本来就是本系统的事实源，从它算指标不会出现
"指标说 A、产物说 B"的两套事实。

## 三、告警阈值与处置动作

阈值依据一栏说明"为什么是这个数"，避免拍脑袋数字。所有比例类指标的分母都是
**被扫描到的任务产物总数**，因此上线初期样本少时波动大，建议同时要求
`govbudget_jobs_total >= 20` 才触发比例类告警。

| 指标（Prometheus 名） | 级别 | 阈值 | 阈值依据 | 触发后处置 |
|---|---|---|---|---|
| `govbudget_queue_live_workers` | P1 | `== 0` 持续 2 分钟 | 心跳窗口默认 15s（`JOB_QUEUE_HEARTBEAT_MAX_AGE_SECONDS`），2 分钟已是 8 个窗口，排除单次抖动 | 检查 worker 进程存活与 `UPLOAD_DIR` 挂载；`/ready` 的 `job_queue_started` 会同步转红 |
| `govbudget_queue_stale_heartbeats` | P2 | `> 0` 持续 10 分钟 | 心跳文件不会自清理，残留说明有 worker 异常退出 | 确认是缩容残留还是崩溃；崩溃需查 `pipeline_error` 日志 |
| `govbudget_queue_backlog` | P2 | `> 2 × JOB_QUEUE_WORKERS` 持续 10 分钟 | 积压超过并发度两倍说明消费跟不上生产 | 扩 worker 或降 `AI_SEQUENTIAL_MODE` 并发压力 |
| `govbudget_queue_oldest_queued_age_seconds` | P1 | `> PIPELINE_TIMEOUT_SEC`（默认 600） | 排队时间已超过单任务超时上限，说明任务实际没被消费 | 同 `live_workers == 0` 处置；必要时重启 worker 触发 resume |
| `govbudget_stage_duration_p95_ms_total` | P2 | `> 0.8 × PIPELINE_TIMEOUT_SEC × 1000` | p95 逼近超时上限意味着下一步就会大面积超时 | 查 PDF 体量分布与 AI 服务延迟；必要时上调超时或拆分材料 |
| `govbudget_stage_duration_p95_ms_ai` | P3 | `> 60000` | AI 阶段超过 1 分钟已显著拖慢整体交付 | 查 `AI_EXTRACTOR_URL` 延迟与限流 |
| `govbudget_ai_failure_rate` | P2 | `> 0.1` 持续 15 分钟 | 10% 以上失败意味着 AI 覆盖面已不完整，双模式退化为单规则 | 查 AI 服务可达性；若 AI 属必需能力应配 `AI_ASSIST_REQUIRED=true`，让门禁把任务转复核而不是静默降级 |
| `govbudget_unknown_report_kind_ratio` | P2 | `> 0.05` | unknown 只跑通用规则、专项规则未覆盖，是漏检来源；5% 是可人工兜住的量级 | 补充材料类型识别关键词；这些任务门禁已转 `review_required`，需人工复核 |
| `govbudget_review_required_ratio` | P2 | `> 0.3` | 超过三成需人工复核时，自动化已基本失去价值 | 按 `review_reason_codes` 分组定位主因（扫描页 / 覆盖率 / 年度 / 证据不足） |
| `govbudget_unresolved_report_year_ratio` | P3 | `> 0.05` | 年度识别失败会让同比与口径判断失去基准 | 检查文件命名与封面年度；**不得**用兜底年份填补（M1 已明确禁止 2000 兜底） |
| `govbudget_error_job_ratio` | P1 | `> 0.05` | 5% 以上硬失败属于系统性问题 | 按 `stage=pipeline_error` 的 `error_type` 聚类定位 |
| `govbudget_report_id_collision_count` | P1 | `> 0` | 冲突意味着不同原件被并进同一份报告身份（缺口 P0-09 的回归信号），会直接污染结构化数据 | 用 `collisions[].job_ids` 定位；核对组织匹配模式与年度识别结果后重跑结构化入库 |
| `govbudget_evidence_degraded_findings` | P3 | 单日增量 `> 20` | 大量 AI 问题拿不出证据，说明提示词或抽取链路退化 | 查 `stage=evidence_checked` 日志与 `prompt_version` 留痕 |

### 与 `/ready` 的分工

- `/ready`：**能不能接流量**（依赖可达性、目录可写、队列有活 worker）。
- `/metrics`：**跑得好不好**（积压、耗时、失败率、结论质量）。

两者都要接监控：`/ready` 做存活探针与流量摘除，`/metrics` 做趋势与质量告警。

## 四、本轮局限（如实说明）

- 比例类指标的分母是"扫描到的历史任务总数"，**不是时间窗口内的任务数**。
  因此它反映的是累计态势，不是瞬时速率；需要瞬时速率时应由采集端做 `rate()` 差分，
  或后续把指标口径改为按时间窗过滤（本轮未做）。
- 无 Golden Corpus，所有质量指标都是**结构性指标**，不含召回率/精确率。
  这些阈值能证明"没有静默失败、没有虚假成功"，不能证明业务召回率达标。
- 告警规则本身未在真实 Prometheus/Alertmanager 环境验证，本轮只验证了
  指标采集函数与端点行为（`tests/test_metrics.py`）。
