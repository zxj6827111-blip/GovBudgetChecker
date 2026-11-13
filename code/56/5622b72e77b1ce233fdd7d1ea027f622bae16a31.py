from pathlib import Path
import csv,datetime,hashlib,json
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
DOC=ROOT/'docs/GovBudgetChecker_第十六轮系统自检与整改报告_20261007.md'
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
s=read(OUT/'self_check.json');assert s['status'].startswith('passed_for_remediation_development_scope')
f=read(OUT/'finding_register.pending.json');m=read(OUT/'plan_matrix.pending.json')
for row in f['findings']:row['status']='fixed_verified_in_development_scope'
f.update(verified_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),business_acceptance='not_passed',unresolved_engineering_findings_in_this_finite_reproduction_set=0,entire_plan_complete=False)
write(OUT/'findings.json',f)
with (OUT/'findings.csv').open('w',newline='',encoding='utf-8-sig') as target:
 w=csv.DictWriter(target,fieldnames=list(f['findings'][0]));w.writeheader();w.writerows(f['findings'])
write(OUT/'plan_matrix.json',m)
with (OUT/'plan_matrix.csv').open('w',newline='',encoding='utf-8-sig') as target:
 w=csv.DictWriter(target,fieldnames=['work_package','status','evidence','remaining']);w.writeheader();w.writerows(m['rows'])
receipt=read(OUT/'live_services/service_receipt.json');scenes=receipt['scenarios'];scan=next(x for x in scenes if x['scenario']=='original_scanned_budget');mixed=next(x for x in scenes if x['scenario']=='original_mixed_final')
facts=read(OUT/'live_services/original_scanned_budget/fact_context.json').get('facts',[])
state=read(OUT/'live_services/original_scanned_budget/status.json');meta=state['result']['meta'];context=meta.get('fact_context') or {}
links=lambda name:f'`outputs/reacceptance_20261007_round16/{name}`'
issue_table='\n'.join('| '+ ' | '.join([x['id'],x['title'],x['before'],x['after'],x['regression']])+ ' |' for x in f['findings'])
scene_table='\n'.join(f"| {x['scenario']} | {x['terminal_status']} | {x['elapsed_seconds']} | {'通过' if x['structured_ingest_validation']['passed'] else '失败'} | {'通过' if x['service_validation_passed'] else '失败'} |" for x in scenes)
wp_table='\n'.join('| '+ ' | '.join([x['work_package'],x['status'],x['evidence'],x['remaining']])+ ' |' for x in m['rows'])
scan_seconds=scan['pipeline_budget']['elapsed_seconds']
scan_count=len(facts)
relation_control=next(x for x in scenes if x['scenario']=='synthetic_scanned_control')['correction'].get('rule_status_changed')
report=f"""# GovBudgetChecker 第十六轮系统自检与整改报告

日期：2026-10-07。检查依据：`GovBudgetChecker_系统整改PLAN_V2.0_20260930.md` 的 WBS，沿用 V1.0 Gate A–F，并对照《GovBudgetChecker 完整整改计划》和当前 `AGENTS.md`。

## 1. 结论与范围

**存在额外问题。本轮新确认并修复 11 项工程缺陷，已完成本轮修复范围的自检；整个 PLAN 及独立业务验收仍未通过。** 当前有限反例集不再出现修复前行为，不能据此推定系统不存在其他问题或全部精度/召回目标达标。

自检结果：全量后端 **2729 通过、137 跳过、0 失败**；另外启用真实一次性 PostgreSQL 的 **135 项数据库用例全部通过、0 跳过**。两个集合本轮共实际执行 2864 项通过用例；普通全量中的数据库跳过已由后一个集合补验，两个其他环境相关跳过保持如实记录。前端 unit、lint、类型检查和生产构建通过；**70 项生产环境适用的真实浏览器回归通过、0 重试**。7 个 API + Worker + PostgreSQL + OCR 场景通过，40 个导出文件（10 个初始/校正视图）已重新读取并校验四格式一致性及保存的 SHA。

工作仍在 `codex/zcode-remediation`，Git HEAD `{read(OUT/'audit_identity.json')['git_head']}`，包括原有未提交整改。本轮没有提交、部署或操作生产数据库。开始时 719 文件与第十五轮源码身份相同；最终冻结 723 文件，修改 25 个现有文件、新增 4 个回归测试文件。第十五轮保存证据与报告的 730 个构件 SHA 保持不变。

A–E 检查线覆盖材料/版本/状态、表结构与识别风险、金额/主体/单位、口径和失败处理、验收门禁与归档交付。反例区分工程夹具、复用开发材料、真实服务与独立人审真值；全部原件复跑属于开发技术验证。

## 2. 新发现与整改

| 编号 | 问题 | 修复前复现 | 修复后行为 | 回归证据 |
|---|---|---|---|---|
{issue_table}

原始 before/after 证据保存在 {links('rules_audit/')}、{links('runtime_audit/')}、{links('acceptance_audit/')}、{links('archive_before.log')}、{links('archive_after.log')} 和浏览器运行记录。新增 4 个后端测试文件合计 78 个用例；浏览器额外覆盖旧深链主机/端口、匿名登录 next 参数和安全响应头。

真实 PG 首跑曾 **16 失败、119 通过**，第二次 **1 失败、134 通过**，最终 **135 通过**。失败根因是旧测试夹具未提供现行 `build_parse_artifact`、分析版本及当前问题身份，或把 migration 0021 假设为最新版本。补齐真实入口所需的有效夹具，保留非法/过期版本被拒绝的断言，没有放宽生产复核门禁。三次日志与清理回执分别保存。

浏览器首次缺失 Playwright 浏览器，全部用例在启动前失败；改用本机已有 Chrome 的独立临时配置后，新发现了 `127.0.0.1` → `localhost` 的真实跳转缺陷。一次相对 Location 修复被 Next 生产运行时拒绝，随后使用受支持的 `skipMiddlewareUrlNormalize` 和校验后的请求来源生成绝对跳转，最后完整适用集合 70/70 通过。所有失败日志都保留；生产默认关闭的 `/e2e/batch-upload` 演示 CSP 用例不适用于这次生产构建，未放开生产测试页面。

归档包新增确认状态和版本约束，冻结报告体及 SHA；`package_id` 下载会读取冻结快照。已有无快照旧包明确返回 409，须从当前确认问题重建，不自动套用新报告。实际生成 PDF 的回归证明重审后仍得到原归档版本、原始 PDF 字节不变；另有 ZIP 契约和篡改反例。持久报告快照位于现有工作流文件权威存储，本轮没有声称数据库镜像单独恢复也能还原新快照。

## 3. 真实服务和交付复验

| 场景 | 终态 | 场景总耗时（秒，含导出/部分校正） | 本次结构化入库 | 技术服务检查 |
|---|---|---:|---|---|
{scene_table}

21 页原始扫描预算 SHA `5978b569d1f4bf58d07281db4150e72b9cf867fa45a051da4976aabdc6a5eec7`，**21/21 页 OCR 成功**，无失败页、截页或跳过 OCR。Worker 总预算 3120 秒，实测 pipeline **{scan_seconds} 秒**，完成时未超时。`progress_snapshots.jsonl` 保存跨过原 600 秒失败点的过程证据：621.8 秒时 19 页完成、正在处理第20页。预算为 Worker 的实际分页预算，客户端等待 3600 秒不能替代它。

该扫描原件仍是 `{scan['terminal_status']}`；事实上下文仅有 **{scan_count} 项事实**，BUD-001 为 `insufficient_data`，结构页未可靠认定时维持待复核。正式问题数量 0 表示证据门控制，并不表示预算材料没有问题。JSON、PDF、DOCX 没有重新出现确定性“缺失预算表”；PDF/DOCX 明示“结构识别尚未完成”。这是运行可靠性和诚实回执通过，扫描金额可计算能力仍有限。

35 页混合决算原件 SHA `f22ac36e2536a15d9a90c5e2ace74b84c3fa79f8e15d010d8a7663ff883e97f7`。实际金额从 **746.62 → 756.62** 的开发校正已重新分析，原始 `parse_facts` 仍为 746.62，原证据页/来源不变，当前 FactContext 投影为 756.62；原始校正 JSONL 在临时工作区清理前原样复制并校验 SHA。当前结果版本、DB 查询计数、parse_run/version、覆盖层 basis 与当前 FactContext token 均一致，缓存读回和本次入库成功，四格式历史版本下载相同。栏次/行次未再次生成金额事实。

合成扫描控制样本验证合法 OCR 金额操作数及校正后的关联规则状态变化：`rule_status_changed={relation_control}`；它明确不算原件或盲测样本。对35页原件，校正产生新投影并不自动证明所有目标勾稽都已消费该值；真实识别或关联缺口继续保留未完成。

{links('technical_preflight_development/technical_preflight_check.json')} 使用生产技术前置校验器重新读取本轮实际服务附件，验证当前查询回执、不可变原事实、真实账本和生产上下文，结果通过。它没有生成或验收人员签字、冻结真值或盲测指标。

服务启动时的后端冻结身份保持不变。运行期间另修复4个前端/浏览器测试文件；最终另存全量冻结，服务受测源指纹与最终版本逐项一致，未把两个不同版本的全链复跑当成重复性证明。本轮只有一次当前后端的完整7场景复跑；第十五轮两次运行仅保留为历史证据。

前端服务器、临时 API/Worker、服务验证 PostgreSQL、PG 用例容器及本轮 OCR 容器均已清理；服务临时工作区已删除。依赖检查（后端63包、OCR）、环境变量三方对账（136/131/65）、Ruff、保留日志键和 `git diff --check` 通过。macOS 等环境能力限制不通过降低阈值或删除用例掩盖。

## 4. PLAN 对账与剩余工作

| 工作包 | 本轮状态 | 可复验依据 | 未完成范围 |
|---|---|---|---|
{wp_table}

| Gate | 本轮结论 | 未达到整体门禁的原因 |
|---|---|---|
| A 数据/状态 | 自动化与真实PG覆盖现行身份/版本/状态路径 | 真实历史台账、残留处置及年度/发布日期人工抽验没有全量签收 |
| B 检查完整性 | checker 目录无未实现项；失败/缺事实/缺分母不再冒充完成 | 实际材料仍有阻塞义务，7场景终态不能当作7份“无问题”；目录实现率不能替代运行完成率 |
| C 误报 | 新金额/主体准入、证据回执反例通过 | 无独立冻结真值，正式 precision、证据定位率及舍入误报目标未验收 |
| D 漏报 | 现行checker与回执回归通过 | overall/关键勾稽 recall 和8类真实反例全覆盖没有冻结人审基线 |
| E 审核闭环 | 真实PG并发/版本失效/完成锁与归档快照、浏览器交互通过 | 双忽略权威存储统一和完整刷新/重启用户链路签收仍未完成 |
| F 可靠性 | 真实空库/升级/幂等/重试与取消回收、SHA/版本导出通过 | 部署目标环境的完整资源验收和数据库+上传目录+审计日志恢复演练未本轮完成 |

独立交接最新准备包为 {links('independent_intake_final_origin/')}：32 开发 + 32 盲测槽，实际冻结材料 **0**、人审缺陷组 **0**，业务指标 `null`，交接门禁为 `pending_not_passed`。开发已知材料和本轮复跑原件不能更名计入盲测。封存真值未读取；SHA 只绑定文件内容，不冒充人员身份认证。

下一阶段先收口 WP3 双忽略存储与 WP6 全量消费对账，补 WP9 应收清单的端到端证据；由独立保管/标注/复核人员完成官方完整样本抽样、近重复排除、分歧裁定、代码/模型/配置/覆盖层冻结后两次运行，再按授权揭盲。正式发布仍须 Gate C/D 指标、UAT 和三件套恢复演练记录。本轮不宣布整个计划或生产可用性通过。

结构化输出：{links('findings.json')} / {links('findings.csv')}，计划矩阵 {links('plan_matrix.json')} / {links('plan_matrix.csv')}；最终自检 {links('self_check.json')}，源码差异 {links('source_delta_final.json')}，证据完整性 {links('artifact_manifest.json')}。原始日志、失败记录和运行脚本均保存，便于下一位复核者重新计算。
"""
assert not DOC.exists(),'preserve existing report; choose a new report path'
DOC.write_text(report)
paths=[p for p in OUT.rglob('*') if p.is_file() and p.name!='artifact_manifest.json']+[DOC]
paths += [p for p in (ROOT/'docs/acceptance_templates').rglob('*') if p.is_file()]
paths += [ROOT/'docs/独立盲测交接操作_20261006.md',ROOT/'docs/ZC07_真实验收数据格式.md']
manifest={str(p.relative_to(ROOT)):{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(set(paths)) if p.is_file()}
write(OUT/'artifact_manifest.json',manifest)
print(json.dumps({'report':str(DOC),'findings':len(f['findings']),'artifact_count':len(manifest),'self_check_status':s['status'],'plan_accepted':False},ensure_ascii=False))
