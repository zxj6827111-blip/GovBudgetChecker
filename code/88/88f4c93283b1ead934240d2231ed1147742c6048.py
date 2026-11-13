import json,re
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1]
assert not (OUT/'final_acceptance_hold.json').exists(), '原件页面验收发现待修缺陷，不能生成全部通过报告；详见 final_acceptance_hold.json'
def read(name):return json.loads((OUT/name).read_text())
release=read('release_candidate.json');label=release['final_replay'];comparison=read(label+'_comparison.json');identity=read(label+'_identity_verification.json')
engine=release.get('engine_candidate',release['candidate'])
unique_pdfs=len({row['sha256'] for row in read(label+'_source_manifest.json').values()})
database=read('database_final_verified.json');preserve=read('preservation_final.json');history=read('production_history_verified.json');served=read('served_build_verification.json');deployment=read('deployment_final_receipt.json');finished=read('daily_final_finished.json')
assert comparison['materials']==345 and not comparison['errors'] and not comparison['monetary_cell_changes'] and not comparison['unproven_fact_changes']
assert not identity['cross_group_identity_collisions'] and not identity['unresolved_receipt_refs']
assert database['verified']==database['expected']==111 and not database['pending'] and not finished['errors']
assert deployment['after']==preserve['release']==release['candidate']
assert (OUT/'served_build_verification.json').stat().st_mtime >= (OUT/'deployment_final_receipt.json').stat().st_mtime
read('database_history_verified.json');assert read('shared_scope_final_verified.json')['distinct_documents_and_reports']
backend=(OUT/release['backend_test_log']).read_text();tests=re.search(r'(\d+) passed, (\d+) skipped',backend);assert tests and 'FAILED ' not in backend
pg=read(release['postgres_receipt']);assert pg['exit_code']==0
ui=read('ui_final_verification.json') if (OUT/'ui_final_verification.json').exists() else {'verified':False}
if ui['verified']:assert ui['candidate']==release['candidate'] and ui['build_id']==release['build_id']
front=read('frontend_final_test_receipt.json');assert front['all_checks_passed'] and front['build_id']==release['build_id']
entries={e['job']:e for e in read(label+'_summary.json')};core=[]
daily={row['job']:row for row in database['rows']}
for job in read('reported_and_boundary_jobs.json'):
 e=entries[job];s=json.loads((OUT/label/job/'uploads'/job/'status.json').read_text());r=s['review_report'];ids=set(e['todo'].get('blocking_ids') or [])
 current=json.loads((ROOT/'uploads'/job/'status.json').read_text());actual=current['review_report']
 assert job in daily and current['checksum']==s['checksum']
 core.append(dict(job=job,filename=e['filename'],source_sha256=s['checksum'],formal_issues=len(r['issues']),tips=len(r['tips']),
    blocking_checks=[{'id':i['obligation_id'],'title':i['title'],'detail':i.get('detail')} for i in r['manual_review'] if i.get('obligation_id') in ids],
    production_revision=current['analysis_revision'],production_counts=daily[job]['counts'],
    production_issues=[{'id':i['id'],'rule':i.get('rule_id'),'title':i.get('title'),'detail':i.get('message')} for i in actual['issues']],
    production_unfinished_checks=[{'id':i.get('obligation_id'),'title':i.get('title'),'status':i.get('status'),'detail':i.get('detail')} for i in actual['manual_review'] if i.get('obligation_id')]))
(OUT/'重点原件验收明细.json').write_text(json.dumps(core,ensure_ascii=False,indent=2))
lines=['# 重点材料核验清单','',f"日常候选：`{release['candidate']}`。下列21份均保留原件哈希与本轮数据库版本，完整字段见[验收明细](重点原件验收明细.json)。",'',
       '“正式问题条目”与“未完成检查”分别列示；后者需要人工补核，不等于已确认原件错误。页面的必须处理队列包含这两类，不能仅按本表的正式问题数与页面总数比较。未完成原因取自固定原件回放，不以人工是否已经处理作为材料真实性判断。','',
       '| 材料 | 日常报告正式问题条目 | 固定原件未完成检查 |','|---|---:|---:|']
for item in core:
 lines.append(f"| {item['filename']} | {len(item['production_issues'])} | {len(item['blocking_checks'])} |")
for item in core:
 if not item['blocking_checks']:continue
 lines.extend(['',f"## {item['filename']}",'',f"当前日常版本：`{item['production_revision']}`。"])
 for check in item['blocking_checks']:
  lines.extend(['',f"- **{check['title']}**（`{check['id']}`）：{check['detail'] or '未提供具体原因，仍需人工核实。'}"])
lines.extend(['','取数能够证明时恢复自动核对；科目错名、主体冲突、金额多处披露冲突、原格缺失或原文未披露时继续保留。石泉部门三列的独立原件复算另见[原件三列逐行复算](原件三列逐行复算.csv)。'])
(OUT/'重点材料核验清单.md').write_text('\n'.join(lines)+'\n')
status='通过（本次系统修复、原件回归及日常服务链路）' if ui['verified'] else '后台链路通过；页面实看待完成，不能声明全部验收完成'
version_context=(f"最终计算与工作流候选均为`{engine}`，全部345次隔离历史任务回放和最终111份日常任务使用该候选。前端分类构建来自`{release.get('ui_candidate')}`，其源码与当前候选完全相同，生产资源逐字节核实。最终版本同时补齐全空网格缺格阻断、原生续页首行业务角色、跨代撤销与编号复用的决定顺序，以及历史意见快照的重复读取；不改变金额容差或模糊匹配门禁。" if release.get('safety_final') else f"计算引擎固定版本为`{engine}`；最终提交额外修改两个前端分类文件。25份任务在计算提交上完成，其余恢复任务使用最终提交，但后端源码逐文件哈希完全相同；不能把这111份说成都执行了后来的UI提交。")
health=read('runtime_recovery_health.json');health_errors=sum(row['health']['status']!=200 for row in health['rows']);ready_errors=sum(row['ready']['status']!=200 for row in health['rows'])
text=f'''# 本次修复验收报告

验收状态：**{status}**。

日常运行副本（main分支）：`{release['candidate']}`。前端构建：`{release['build_id']}`；常用入口仍为 `http://localhost:3000`，后端为 `18743`。主开发区原有15处未提交改动保留。

{version_context} 本报告中的全量测试及固定原件回放均对应列出的计算候选。

## 四类修复

| 范围 | 已落实行为 |
|---|---|
| 取数失败 | 只使用当前原件的真实金额格与经认证空白；补齐全空金额表、合并总计标签、跨页名称尾行及原生注释行处理；不移动相邻项目列，不反填原始事实。 |
| 科目对应 | 去除语法连接符造成的名称误读；只依赖精确名称、编码和完整原生层级链。错名与主体冲突保留，不按同金额猜测对应。 |
| 重复提示 | 同一原格算式、同一空白格与同金额说明冲突合并，完整保留各处证据和失败回执；展示排序不再使成员编号串到另一事项。不同公式保持独立，按业务分类集中展示。 |
| 人工决定 | 仅对原件、主体、年度、检查、政策、判断对象和证据唯一一致的事项跨代继承；撤销以最新决定为准。去除仅凭旧事实/证据编号相同的回退路径。不能安全继承的原决定与备注在历史区保留。 |

重复完成率短语只增加原页相邻原行作为定位见证，计算范围不变，仍通过原生字符与坐标门。空白分子、分母单独留有原格证明，不伪造印刷的“0”。

原生经济分类续页的首条业务行，只有前表身份、相邻页、原生逐列边界、逐字编码及同类编码顺序共同证实时，才纠正误标的表头角色；金额、名称原文和格坐标保持原值。缺失金额格、横线、OCR及不确定角色继续阻断，不把它们凑成全零表。

功能分类的计算层级只接受同一原件另一完整原生表的精确同名类/款/项链证明：当前表也须实际披露相同类与款，错前缀项紧跟该款，双方原页、原格、哈希、证据ID和原生来源均通过认证。原印编码与金额保留，另一表的金额不参与本表求和。同一镜像冲突跨表归为一条，保留每处原行和独立失败回执。没有这组证明的合法纯项级、纯款级或混层披露保持原有核对范围，不按层级长度或相同金额猜测。

历史意见读取在单次投影内复用同一份经哈希核验的旧快照；新请求重新读取并验哈希，篡改快照仍不能继承。对同一32条真实历史意见的函数级采样，详情与工作流读取分别从约4.4/4.7秒降到1.0/0.9秒，意见计数与工作流文件哈希未变；实际页面能否打开另由页面实看验证。

展示分类按实际问题语义决定：采购构成求和放入金额勾稽，序号跳号放入文字规范，比例不成立放入比例核对，未完成取数放入识别范围；年份笔误与年度口径分开。345次历史任务报告的展示条目全部获得具体业务分类，未知类型仍保留“其他”回退，不靠删除条目消除它。分类补丁经过独立只读审查，问题身份、证据、严重度和人工决定保持原对象。

残联部门与本级单位同名时，共享报告使用不同的稳定财政文档归档键，保留双方结果及防覆盖约束。首次日常批次的1份入库冲突已单独记录；最终111份须全部产生可信新结果，才计入本报告。

## 验证结果

- 固定代码后端：{tests[1]}通过、{tests[2]}跳过；真实 PostgreSQL 集成：160通过。前端单元、TypeScript及生产构建通过。
- 全部345次历史上传任务隔离重放，按文件哈希覆盖{unique_pdfs}种不同PDF内容：0执行/解析异常；财政金额事实变化{len(comparison['monetary_cell_changes'])}，无证据事实变化{len(comparison['unproven_fact_changes'])}，资格变化{len(comparison['qualification_changes'])}。{len(comparison['source_label_repairs'])}个标量事实只恢复了有原生可见文字证据的总计标签。
- {identity['canonical_findings']}个规范发现中，跨问题组成员编号冲突0，悬空失败回执引用0。
- 日常当前最新111份重新分析，并逐份核对数据库当前版本指针、不可变结果哈希、发布状态、文件镜像、实际执行源码哈希和原件哈希。AI保持关闭，本次未调用外部模型。
- 原件及历史标注PDF共{preserve['original_and_historical_pdfs_preserved']}份不变；其余{preserve['historical_statuses_preserved']}份历史任务结果不重写。原有{preserve['workflow_records_preserved']}条工作流记录、{preserve['workflow_events_preserved']}个决定事件及全部备注保留。20个旧补核会话、2条数值补核决定保留；仅按正常重跑流程使相关旧会话失效。

345是历史任务回归次数，涉及{unique_pdfs}种不同PDF内容；111是日常当前任务重跑数量，两者不混计，也不将重复上传算成不同原件。9份带真实人工记录的材料另在隔离环境复验77条旧决定：全部可见；不能证明现行判断依据相同的，不自动视为本轮已处理。日常当前/历史可见性另由实际工作流模型核实。

## 日常运行恢复

首次最终批次在自动32路并发下，健康看门狗多次记录96秒无响应并重启，54份任务被取消；这些失败不计为通过，日志及状态已保全。保留已成功25份，通过官方入口仅重试54份，原排队32份由持久队列续跑。最终111份全部发布并通过上列入库验证后，才生成本报告。

当前使用项目现成的API/worker分离方式：API环境`JOB_QUEUE_ROLE=api`；独立`python -m api.worker`环境`JOB_QUEUE_ROLE=worker`、`JOB_QUEUE_WORKERS=2`。端口不增加，健康阈值未放宽，原`.env`及AI设置未修改。后续启动应沿用这组运行角色与并发参数，避免回到未限制时自动32路并发。当前进程见`deployment_final_receipt.json`；中间恢复参数见`runtime_recovery_deployment.json`，原失败现场见`engine_cdbf_acceptance/`。

{'中间批次验收记录另保留在phase5eed_runtime_acceptance。全部最终修正后，再用最终候选完整重跑111份；不把中间批次当作最终版本的执行结果。' if release.get('safety_final') else ''}

API/worker分离运行期间的负载采样共{health['samples']}轮：API健康请求失败{health_errors}次；worker就绪采样不可用{ready_errors}次，未放宽阈值。{'就绪短暂不可用的采样另保留，不算作持续就绪通过。' if ready_errors else '本轮采样未记录就绪失败。'}任务最终全部发布，验收时健康/就绪均200。这份功能验收不等同于生产SLA的全面保证。

## 原件问题与验收边界

原件独立网格复算保留石泉部门支出表的真实矛盾：本年支出合计19,044.27万元及项目支出4,838.27万元逐类求和正确；基本支出14,206.00万元与各类之和4,610.99万元差9,595.01万元。不能把相邻项目支出移入基本支出，或修改原数据使等式成立。独立逐行复算见“原件三列逐行复算.csv”。

曹杨原来因零支出说明、折行、错误说明页提示造成的假未完成已按真实原件恢复核对。建管委单位原件缺失支出决算表、三公表仅表头等情况仍作为材料问题；连带检查归到相应根问题，不伪造零表。主体全称冲突、错科目名、重复金额披露冲突、缺少明确预算金额及原件编码不完整，仍保留问题或人工复核。

页面验收另发现曹杨街道本级单位的原码3013299被当作类级根，重复累计95.90万元，造成95.89/95.88万元假差额。本次已纠正：独立原件复算收入、本年支出列差0.01万元，基本支出列一致，项目支出列差0.02万元，均按原有显示舍入标准处理；第7、12页原码3013299与第19页原印2013299的完整同名链冲突合成一条，双方编码均不改写。独立网格数据见[曹杨本级单位原件独立复算](曹杨本级单位原件独立复算.csv)。中间候选曾误伤合法纯项级表及合法混层披露，既有测试与独立原件差异门均拦住了回归；这些候选未部署，拒绝记录见final10_rejected.json和final11_rejected.json。最终限定为经原格认证的精确原生镜像链，并补测OCR、原文缺失、证据ID伪造、跨年/跨主体、同金额与镜像金额不同等边界。

按345次历史任务计数，独立阻断检查由{comparison['baseline_blockers']}条变为{comparison['candidate_blockers']}条；包含重复上传、恢复核对和连带项归并，不能当作不同原件的问题数量，或等同于自动通过相同数量的材料。本次未把剩余证据不足项改判通过，也不声称所有历史问题已逐条获得人工金标准或AI语义验收。核心21份的当前正式问题及剩余原因见[重点材料核验清单](重点材料核验清单.md)，完整字段见“重点原件验收明细.json”。

## 可复核证据

- `{label}_comparison.json`、`{label}_identity_verification.json`、`{label}_inventory.json`
- `database_final_verified.json`、`deployment_final_receipt.json`、`served_build_verification.json`
- `preservation_final.json`、`production_history_verified.json`、`database_history_verified.json`
- `shared_scope_final_verified.json`、`real_decision_history_final_receipt.json`
- `frontend_final_test_receipt.json`、`ui_category_independent_review.json`、`category_census.json`
- `runtime_recovery_deployment.json`、`recovery_retry_accepted.json`
- `{label.replace('release_','')}_orphan_impact_verified.json`、`{label.replace('release_','')}_source_acceptance.json`、`final_acceptance_hold_resolved.json`

页面实看：{'已完成，详见 ui_final_verification.json。' if ui['verified'] else '尚未完成，不把静态资源验证当作页面实看。'}
'''
(OUT/'验收报告.md').write_text(text);print(status)
