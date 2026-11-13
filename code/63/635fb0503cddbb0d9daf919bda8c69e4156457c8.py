import asyncio, hashlib, json, os, subprocess, sys
from pathlib import Path
import asyncpg
import httpx

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
RT=OUT.parent/'runtime'
WT=ROOT/'artifacts/pdf-ai-local-acceptance-20261026'
env=json.loads((RT/'environment.json').read_text());os.environ.update(env);sys.path.insert(0,str(WT))
from src.services.analysis_fact_snapshot import verify_persisted_facts
from src.schemas.parse_artifact import FiscalFact
from src.services.user_store import UserStore

def sha(p):
 with p.open('rb') as h:return hashlib.file_digest(h,'sha256').hexdigest()

async def main():
 assert '4250 passed, 189 skipped' in (OUT/'pytest_release.log').read_text()
 assert json.loads((OUT/'result_browser_receipt.json').read_text())['passed']
 receipts={label:json.loads((OUT/label/'receipt.json').read_text()) for label in ('department','unit')}
 c=await asyncpg.connect(env['DATABASE_URL'])
 try:
  projections={}
  for label,rec in receipts.items():
   row=await c.fetchrow('SELECT p.*,r.document_version_id AS run_document_version_id,d.subject_org_id FROM published_fact_projections p JOIN parse_runs r ON r.id=$1 JOIN fiscal_documents d ON d.id=p.document_id WHERE p.job_uuid=$2',rec['storage']['parse_run_id'],rec['job_id'])
   assert row and row['subject_org_id']==rec['organization_id']
   assert row['document_version_id']==row['run_document_version_id']==rec['document_version_id']
   receipt=json.loads(row['receipt']) if isinstance(row['receipt'],str) else row['receipt']
   projections[label]={'document_id':row['document_id'],'version_id':row['document_version_id'],
       'parse_run_id':rec['storage']['parse_run_id'],'shared_report_id':receipt['shared_report_id']}
  assert projections['department']['document_id']!=projections['unit']['document_id']
  assert projections['department']['shared_report_id']!=projections['unit']['shared_report_id']
  assert projections['unit']['parse_run_id']!=143
  old=json.loads((OUT/'before/a296c928fbd69d0b173a5050667e3b6e/fact_snapshots/rev-20261026T124826-f5caa922.json').read_text())
  await verify_persisted_facts(c,parse_run_id=143,facts=[FiscalFact.from_dict(f) for f in old['raw_context']['facts']])
  archived_head=json.loads((OUT/'retired_projection_receipt.json').read_text())
  assert await c.fetchval('SELECT COUNT(*) FROM legacy_projection_archives WHERE document_version_id=300 AND payload_hash=$1',archived_head['head_archive_hash'])==1
 finally:await c.close()
 assert sha(RT/'data/organizations.json')==sha(OUT/'before/data/organizations.json')
 original_catalog=json.loads((OUT.parent/'runtime_snapshot.json').read_text())['organization_catalog_sha256']
 assert sha(ROOT/'data/organizations.json')==original_catalog
 history=[]
 for label,rec in receipts.items():
  old_dir=OUT/'before'/rec['job_id'];current=RT/'uploads'/rec['job_id']
  for p in old_dir.glob('*.pdf'):assert sha(p)==sha(current/p.name)
  preserved=0
  for p in (old_dir/'analysis_versions').rglob('*'):
   if p.is_file() and p.name!='current.json':
    assert sha(p)==sha(current/'analysis_versions'/p.relative_to(old_dir/'analysis_versions'))
    preserved+=1
  history.append({'job_id':rec['job_id'],'preserved_history_files':preserved})
 protected=[]
 for kind in ('main_ai','pdf'):
  source=json.loads((OUT.parent/f'{kind}.input.json').read_text());base=Path(source['path'])
  assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=base,text=True).strip()==source['head']
  changed=[name for name,meta in source['files'].items() if not (base/name).is_file() or sha(base/name)!=meta['sha256']]
  assert not changed,(kind,changed)
  protected.append({'source':kind,'unchanged':True,'files':len(source['files'])})
 UserStore().update_user('codex_local_validator',is_active=False)
 health={}
 with httpx.Client(timeout=15) as client:
  for port,path in [(3000,'/login'),(18743,'/health'),(3013,'/login'),(18746,'/health')]:
   response=client.get(f'http://127.0.0.1:{port}{path}');assert response.status_code==200;health[str(port)]=200
 head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=WT,text=True).strip()
 assert subprocess.check_output(['git','diff','HEAD','--name-only'],cwd=WT,text=True).strip()==''
 files=subprocess.check_output(['git','ls-files'],cwd=WT,text=True).splitlines()
 manifest={'head':head,'branch':subprocess.check_output(['git','branch','--show-current'],cwd=WT,text=True).strip(),
    'source_sha256':{name:sha(WT/name) for name in files if (WT/name).is_file()},
    'receipts':receipts,'projections':projections,'old_run_143_raw_facts_unchanged':True,
    'original_pdf_unchanged':True,'analysis_history':history,'original_worktrees':protected,
    'organization_catalog_unchanged':True,'health':health,'temporary_validator_disabled':True,
    'tests':{'pytest_passed':4250,'pytest_skipped':189,'pytest_failed':0,'frontend_unit':'passed','typescript':'passed','eslint':'passed','mypy':'passed','ruff':'passed','build':'passed'},
    'release_status':'targeted_defects_repaired_local_acceptance_only'}
 (OUT/'repair_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2,default=str))
 lines=[]
 for label,rec in receipts.items():
  ai=rec.get('ai_coverage') or {}
  lines.append(f"| {'部门件' if label=='department' else '单位件'} | {rec['organization_name']} | `{rec['analysis_revision']}` | {rec['status']} | {ai.get('effective_completed_calls',ai.get('succeeded_calls'))}/{ai.get('planned_calls')} |")
 report=f'''# 信访办公室部门、单位材料归属与处理失败修复

已在 3013 验收实例完成修复。原任务 ID 保持不变，不需要重新上传；3000 及原数据库未切换。

## 修复结果

| 材料 | 当前关联节点 | 当前分析版本 | 处理状态 | AI 有效完成 |
| --- | --- | --- | --- | --- |
{chr(10).join(lines)}

- 部门任务：http://127.0.0.1:3013/task/bdba38fab5eab35c2e2c03539ed7036c
- 单位任务：http://127.0.0.1:3013/task/a296c928fbd69d0b173a5050667e3b6e
- 单位报告栏目：http://127.0.0.1:3013/reports/8c0ba1fafea0?year=2024
- 部门下属单位：http://127.0.0.1:3013/reports/5e26afdec7b9?tab=units&year=2024

“待人工复核”是分析已完成、仍有问题或检查义务需要人工处理，不是处理失败。单位原件没有明确写出汇总口径；归属节点已按用户确认纠正，原文缺少证据的检查口径仍保留待确认，没有由文件名或组织层级猜测补齐。

## 原因与处理

1. 上传补齐表单错误地只调用 `selectDepartmentOptions`，不显示单位。已允许选择部门与单位，并显示层级、上级，避免同名混淆。
2. 批量预设会把原件部门/单位范围未确定的文件直接补成部门，显示校验通过。已改为逐份确认，这类文件在选定组织前不能提交；其他正常批量预设继续生效。真实 Chrome 已复现并验证新行为。
3. 两份文件原先都人工关联部门节点，成为同一年度版本序列，部门件被 `not_latest_version` 拦截。已把单位件归回既有单位节点，并建立独立材料槽位。旧错误归档版本 300、其事实与分析历史保留；取消它对部门当前材料的占位，部门当前版本恢复为 299。旧复核会话按版本变更标记失效，意见记录不删除。
4. 复验发现旧单位件的兼容投影发布指针仍占部门文档。旧行、共享投影及发布指针均已归档并回读，退出错误的当前发布位置。正常的“禁止旧任务覆盖新事实”保护没有放宽。
5. 复验又发现：相同 PDF 改主体后，确定性的解析运行 UID 会跨文档碰撞，触发“原始事实主体与快照不一致”。已将组织 ID、名称、年度、文种、检查主体纳入运行与缓存身份；跨文档 UID 冲突现在直接拒绝复用。原始格、文字、金额和文件哈希不改写。新的单位解析运行与原运行 143 分开，旧运行全部原始事实回读核验未变。共享发布同时透传确认的组织 ID，不再按同名猜归属；已关联的目录名称不再作为文件名清洗。

## 验证与版本

- 修复分支：`{manifest['branch']}`；最终提交：`{head}`。
- 最终全量 pytest：4250 通过、189 跳过、0 失败。
- 前端全部单元脚本、TypeScript、改动文件 ESLint、Ruff、Mypy 和生产构建通过。
- 真实 Chrome：单位可选；批量部门预设不能解除范围待确认；逐文件选择单位后可提交。
- 两个原任务：实际规则/AI、事实及证据入库、数据库往返、JSON/CSV 同 revision、结果页及报告栏目核对通过。部门和单位的文档、当前材料槽位、解析运行与共享报告投影相互独立。
- 原件哈希、既有分析历史、旧原始事实、组织目录及原工作区均核对未变。临时验证账号已停用。
- 两份材料的最终规则、AI 和事实发布在 `feb4b35c` 下重新生成；最终提交 `277f8d25` 仅为读取视图补充组织层级，页面标题及栏目已在该提交复验。解析、事实与 AI 代码未变化，没有新增模型调用或用历史回放冒充实跑。

## 证据与回退

`repair_manifest.json` 汇总最终版本、源码哈希、两任务回执、投影分离、旧事实与历史保护及四端口健康状态。`department/`、`unit/` 包含结果、导出、入库与页面证据；`upload_browser_receipt.json`、`result_browser_receipt.json` 为真实浏览器断言。

`before/` 为修复前数据库及两任务完整备份，权限受限，不要外发。旧错误投影在数据库 `legacy_projection_archives` 中保留，校正动作已写本地审计日志。旧冻结版本及两个原工作区仍保留，不需要回退原库。若需撤销本地数据校正，应停 3013 进程后恢复这份独立备份；不要对 3000 原数据库执行恢复。

本轮仅修复这两项实测缺陷及直接关联的身份缓存问题，不等于全量 OCR、全量 PDF 或 AI 准确率正式验收通过。
'''
 (OUT/'修复验收记录.md').write_text(report)
 handoff=OUT.parent/'本地验收交接.md'
 notice='> 3013 已更新到信访办部门/单位修复版；最新版本及结果见 [本轮修复记录](letters_visit_fix_20261026/修复验收记录.md)。下面保留初始整合验收记录。\n\n'
 if not handoff.read_text().startswith(notice):handoff.write_text(notice+handoff.read_text())
 print(json.dumps({'head':head,'projections':projections,'old_facts_and_history_unchanged':True,'health':health},ensure_ascii=False,indent=2))

asyncio.run(main())
