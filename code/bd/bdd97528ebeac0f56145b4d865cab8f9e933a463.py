"""Build review artifacts only; never edit application source."""
import csv
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent

def ref(path, line):
    return {"file": str(ROOT / path), "line": line}

rows = [
    ("R5-01", "P1", "code_defect", ["RC-04", "RC-06"], "占比常用表达漏报，已报错时最终回执仍全部通过", [ref('src/engine/rules_v33.py',1887),ref('src/engine/rules_v33.py',2455),ref('src/engine/check_obligations.py',1430)], "收入及财政拨款均1000万元；同口径说明占99.89%/占比99.89%", "两者均检出0.11个百分点差异，并由比例关系失败回执引用问题", "占99.89%无问题且pass；占比99.89%规则fail但最终三条回执均pass", "补比例独立关系与叙述/分子/分母事实；聚合不得覆盖遗漏失败关系；修正±1pct文案", "两种99.89%均fail；100%和99.90%保持pass；问题ID与最终回执一致", "rules_audit.json", "current_percentage_receipt_probe"),
    ("R5-02", "P1", "code_defect", ["RC-01", "RC-04", "RC-09"], "V33三规则作者缺关系/事实/证据身份", [ref('src/engine/rules_v33.py',2397),ref('src/engine/rules_v33.py',2570),ref('src/engine/rules_v33.py',5261)], "生产legacy payload，合法FactContext，真实数值不一致", "V33-103/104/214每个问题保留真实关联ID，失败回执引用问题", "问题relation=null、fact/evidence=[]；fail回执链接0/2、0/1、0/1", "在作者比较分支携带操作数和证据身份，贯穿所有构建与导出入口", "每个失败关系有真实problem refs；改事实重算可追溯且无伪造证据", "rules_audit.json", "current_source_extended_probes.result.production_legacy_payload_identity"),
    ("R5-03", "P1", "code_defect", ["RC-01", "RC-04", "RC-06"], "三公混合取数仅一个事实ID仍声明事实计算完成", [ref('src/engine/common_rules.py',1100),ref('src/engine/fact_context.py',1236)], "仅接待Fact=10；其他三项旧表；旧表合计30改31", "公式四个操作数均绑定，或诚实声明混合/旧表迁移未完成", "总额关系pass变fail；两次仍fiscal_fact/complete；required/operand仅reception", "逐操作数登记来源；叙述事实化；缺完整事实时不声明complete", "改变任一实际操作数能追溯依赖；四项事实全齐才complete", "rules_audit.json", "current_source_extended_probes.result.mixed_source"),
    ("R5-04", "P1", "code_defect", ["RC-02", "RC-08"], "解析语义变化未升级指纹，旧v4缓存覆盖新续页结果", [ref('src/services/pdf_parser.py',92),ref('src/services/structured_ingest_runner.py',250)], "合法同标题续页，总计100+事业40；续页财政60；装入第四轮旧缓存", "旧两实例产物失效，重新生成当前单实例新parse_run", "相同v4指纹命中run88；恢复old-run两个实例，未持久化新产物，返回done", "升级语义/产物版本并迁移或重建旧v4缓存", "真实旧缓存到新版重新解析，再次复用新缓存保真", "parse_audit.json", "reproductions.round4_v4_parser_semantics_cache_replay"),
    ("R5-05", "P1", "code_defect", ["RC-03"], "教育局等两字机构简称未排除，串用外单位金额", [ref('src/engine/budget_rules.py',1310),ref('src/engine/budget_rules.py',1318),ref('src/engine/budget_rules.py',1499)], "先教育局2026年金额213012，后本单位328661.81；本单位表文一致", "忽略他单位叙述，或主体不清时转人工复核", "教育局未列排除名单；选213012，实际BUD107两条误报", "基于元数据、全称/简称、句子归属识别主体，取消多值默认首个", "教育局/财政局等简称误报消失，本单位合法简称和真实差异保留", "parse_audit.json", "reproductions.other_org_unprefixed_two_character_name_boundary"),
    ("R5-06", "P1", "code_defect", ["RC-11"], "AI契约拒绝项或空证据数值项仍进入agreement", [ref('src/services/ai_findings.py',356),ref('src/services/merge_findings.py',23),ref('src/services/merge_findings.py',196)], "同一候选known+invented事实；非法fact_candidates.value；numeric relation无evidenceIDs", "rejected/缺证据项持续隔离并留拒绝原因，不作agreement佐证", "契约真实调用后rejected，但保留ID子集后与强规则agreement，候选/待复核为空；空证据项同样agreement", "merger执行资格检查，绑定fact-evidence关系；保持确定性规则原件", "三个反例不可agreement；合法完整候选正常配对；强规则保持", "evidence_audit.json", "production_ai_candidate_contract"),
    ("R5-07", "P1", "code_defect", ["RC-12", "RC-13"], "实际PDF导出与门禁字段、分页及行边界不兼容", [ref('api/routes/reports.py',1674),ref('api/routes/reports.py',1711),ref('scripts/run_zc07_acceptance.py',1367)], "保存五版实际下载JSON/CSV/DOCX/PDF，原样运行export agreement", "实际完整正例通过；互换/删字段反例按ID拒绝", "五版均not_computable；10个unfinished_check类型未打印；另1长文本被分页页眉插入；严重度在ISSUE_ID前", "统一每记录可见语义契约、ID分段与分页规范化，采用真实下载作正例", "真实五版均一致；交换金额/严重度/定位/决定按记录拒绝；不依赖全局tokens", "production_export_gate.json", "01_current/02_current/03_historical/04_historical/05_current; evidence_audit.actual_pdf_export_field_diagnosis"),
    ("R5-08", "P1", "code_defect", ["RC-10"], "人工定位校正未消费但整体汇总applied", [ref('src/engine/fact_context.py',1054),ref('api/main.py',594),ref('api/main.py',1394)], "合法manual_location-only correction，经实际main重算发布新revision", "新事实/证据定位应用并重核，或明确未应用和人工复核", "请求bbox[205,155,315,195]；仍[200,195,320,235]；status=applied/applied=0；blocking=false；无overlay复核原因", "实现版本化人工定位与原件认证，未支持时不汇总为已应用", "新版四格式定位一致，旧版不变；越界/假引文不能verified", "evidence_audit.json", "legal_manual_location_main_boundary.results.manual_location_only_actual_main"),
    ("R5-09", "P2", "verification_gap", ["RC-07"], "自动可信OCR操作数闭环仍未证明", [ref('src/services/ocr_fact_promotion.py',290)], "可物化OCR候选及低分单位/身份歧义对照；传输替身", "真实CPU OCR按格置信度、身份和证据认证，可信候选可计算，弱候选隔离", "固定0.75，low_confidence，操作数0；安全摘要review_required正确", "实跑离线模型并验证晋升；不能提高固定置信度冒充识别真实性", "真实扫描和混合PDF完成计算与精确格认证，低分对照仍降级", "parse_audit.json", "reproductions.ocr_promotion_score_semantic_ambiguity"),
    ("R5-10", "P1", "verification_gap", ["RC-14"], "冻结盲测与真实服务全链验收未交付", [ref('scripts/run_zc07_acceptance.py',1)], "当前ZC07 preflight；无冻结manifest与真实E2E证据", "冻结32+32与100缺陷组、四场景真实服务链后再计算达标指标", "exit2/not_passed；29登记、1真值文件、3缺陷组、0原始PDF哈希认证；Docker daemon不可达，模型清单缺失", "交付独立盲测及隔离PG/Worker/OCR/下载证据、资源与版本记录", "既定precision98/recall95/location98、真实性100、重大无漏、完成95、状态100、导出/重复100达标", "zc07_preflight.json", "inventory/dataset/environment/e2e/gates/business_acceptance"),
]
issues = []
for row in rows:
    ident,priority,kind,rc,title,locations,repro,expected,actual,suggestion,accept,artifact,key=row
    issues.append({"finding_id":ident,"priority":priority,"severity":"high" if priority=='P1' else "medium","status":"open","type":kind,"related_rc":rc,"title":title,"source_locations":locations,"page":None,"bbox":None,"page_bbox_reason":"系统代码复核，未指定实际政府材料；代码位置另列","reproduction":repro,"expected":expected,"actual":actual,"suggestion":suggestion,"acceptance_criteria":accept,"evidence":{"artifact":str(OUT/artifact),"key":key},"confidence":0.98 if kind=='code_defect' else None})
summary={"assessment":"工程回归通过，业务验收未通过","audit_type":"系统代码整改复核","round":5,"date":"2026-10-04","head":"9c71ae6c47caecf959dca9b9d805d7f1c49c2dbd","source_diff_sha256":"d1c7348cdb1dbd125dcd6354bcce336f5dd2d7eb099fc4996b4417b7b8d6d257","full_pytest":{"passed":2371,"skipped":137,"failed":0},"remediation_pytest":{"passed":155,"skipped":2,"failed":0},"code_defects":8,"verification_gaps":2,"business_acceptance":"not_passed","real_business_metrics":{"precision":None,"recall":None,"exact_location":None,"evidence_authenticity":None,"required_check_completion":None,"export_agreement":None,"repeatability":None},"scope_note":"合成/替身结果不能作真实业务指标；拒绝AI进入agreement未使确定性规则原件消失；PDF门禁缺字段不等于丢11条结论"}
(OUT/'findings.json').write_text(json.dumps({"schema_version":1,"summary":summary,"findings":issues},ensure_ascii=False,indent=2))
fields=['finding_id','priority','severity','status','type','related_rc','title','source_locations','reproduction','expected','actual','suggestion','acceptance_criteria','evidence','page','bbox']
with (OUT/'findings.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
    for item in issues:
        value=dict(item)
        for k in ['related_rc','source_locations','evidence']:value[k]=json.dumps(value[k],ensure_ascii=False)
        writer.writerow(value)

before=json.loads((OUT/'source_snapshot_before.json').read_text())
files={path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in before['files']}
diff=subprocess.check_output(['git','diff','--binary'],cwd=ROOT)
after={'client_date':'2026-10-04','head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'branch':subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip(),'diff_sha256':hashlib.sha256(diff).hexdigest(),'files':files,'status':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True),'changed_monitored_files':[path for path,sha in files.items() if sha!=before['files'][path]]}
after['original_tracked_diff_unchanged']=after['diff_sha256']==before['diff_sha256']
(OUT/'source_snapshot_after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2))
assert not after['changed_monitored_files'],after['changed_monitored_files']
assert after['original_tracked_diff_unchanged']
print(json.dumps({'findings':len(issues),'code_defects':8,'verification_gaps':2,'monitored_files':len(files),'tracked_diff_unchanged':True},ensure_ascii=False))
