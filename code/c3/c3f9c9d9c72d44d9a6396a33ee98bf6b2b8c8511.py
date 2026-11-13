import json,pathlib,hashlib,datetime
root=pathlib.Path('outputs/real_intake_review_20261008');truth=json.loads((root/'structure_truth.json').read_text())
comp={'review_date':'2026-10-08','scope':'A+B 独立原件结构真值与本轮 native artifacts 对照；不代表全产品准确率或盲测','truth_file':str(root/'structure_truth.json'),'truth_sha256':hashlib.sha256((root/'structure_truth.json').read_bytes()).hexdigest(),'comparison_timestamp':datetime.datetime.now().astimezone().isoformat(),'documents':[],'false_positives':[],'missed_issues':[],'false_unresolved':[],'latent_parser_observations':[],'missing_features':[],'limitations':['只评估所选8份2025年上海市部门决算PDF','A+B线不独立复核所有数字勾稽规则','样本九张核心表及必备说明原件均存在，不能据此估计缺表/缺章识别能力','本轮是在已有多轮修复的开发环境诊断，不是独立盲测或全产品准确率']}
lookup={d['organization']:d for d in truth['documents']}
for d in sorted((root/'native').glob('document_*')):
 audit=json.loads((d/'audit.json').read_text());issues=json.loads((d/'issues.json').read_text());art=json.loads((d/'artifact.json').read_text());gt=lookup[audit['organization']]
 comparisons=[]
 for ti,t in enumerate(gt['tables'],1):
  match=[x for x in audit['tables'] if x['table_code'].startswith(f'FIN_{ti:02d}_')]
  pages=[p for x in match for z in x['tables'] for p in z['pages']]
  comparisons.append({'table_id':ti,'title':t['title'],'source_title_page':t['page'],'native_pages':pages,'title_page_recognized':t['page'] in pages,'native_instance_count':sum(x['instances'] for x in match)})
 item={'native_directory':str(d),'organization':gt['organization'],'source_sha256_matches_truth':audit['sha256']==gt['sha256'],'metadata_matches':audit['organization']==gt['organization'] and audit['year']==gt['year'] and audit['kind']=='final','year':audit['year'],'native_kind':audit['kind'],'physical_pages_match':audit['pages']==gt['page_count'],'core_tables':comparisons,'confirmed_core_table_presence':all(t['title_page_recognized'] for t in comparisons),'confirmed_missing_table_issue':False,'structural_issue_false_positive':False}
 comp['documents'].append(item)
 if audit['organization']=='上海市监狱管理局':
  low=truth['issues'][0]
  found=any('仍保留该表格式' in i.get('evidence_text','') or '模板' in i.get('message','') for i in issues)
  if not found:comp['missed_issues'].append({'classification':'missed_low_priority_feature','native_directory':str(d),'issue':low,'native_issue_match':None,'reason':'系统输出未检查/未报告公开材料中的编制操作提示语；低级文字/模板清理问题，不是核心表缺失'})
 if audit['organization']=='上海市财政局':
  u=[x for x in audit['rule_execution']['unresolved_rules'] if x['rule_id']=='CMM-001']
  blocks=[x for x in art['text_blocks'] if x['page_number']==28 and ('公务用车购置支出为' in x['text'] or '公务用车运行维护支出' in x['text'])]
  comp['false_unresolved'].append({'classification':'false_insufficient_data','organization':gt['organization'],'native_directory':str(d),'rule_id':'CMM-001','page':28,'native_unresolved':u,'source_evidence':[{'page':28,'bbox':[119.90,297.72,333.65,313.84],'snippet':'公务用车购置支出为0.00万元。','value':0.00,'unit':'万元'},{'page':28,'bbox':[119.90,328.92,505.51,345.05],'snippet':'公务用车运行维护支出30.00万元。','value':30.00,'unit':'万元'}],'parser_artifact_evidence':blocks,'native_page_state':art['page_states'][27],'expected':'同一artifact中已有完整说明数字，应形成购置费/运行费表文比较或准确记录无法绑定的具体原因','actual':'CMM-001将两个已披露字段列为“说明未披露”','reason':'原件与native artifact均有字段，规则下游说明语料/字段抽取未完整利用；本轮无法仅由audit判定具体代码路径','severity':'medium','confidence':1.0,'visual_checked':True,'not_counted_as_error_issue_false_positive':True})
 ghost=[t for t in art['logical_tables'] if t['page_start']==2]
 if ghost:comp['latent_parser_observations'].append({'organization':gt['organization'],'page':2,'classification':'toc_classified_as_table_in_artifact','native_logical_tables':ghost,'current_audit_effect':'audit选择真实九张表，目录伪表未进入本轮正式核心表实例或事实；未造成确定问题误报','source_evidence':{'page':2,'bbox':None,'snippet':'目录列出一至九张表名称，不是真实财政拨款收入支出决算总表'},'suggestion':'在artifact层标记目录角色并避免归为正式logical table，或明确保证所有下游入口过滤目录实例'})
comp['missing_features']=[{'priority':'P1','feature':'原件/解析artifact/说明规则共享完整页面语料及提取覆盖一致性回执','evidence':'财政局第28页artifact完整、CMM-001却声称未披露两项','goal':'避免同一PDF在表格/说明链中因提取器或语料路径不同而遗漏关系'}, {'priority':'P3','feature':'公开模板编制提示语清理检查','evidence':'监狱管理局第18/19页','goal':'以low级别单条合并提示编制指令残留'}, {'priority':'P2','feature':'目录角色识别在artifact层完成并在所有入口保持一致','evidence':'8份目录第2页均作为FIN_04伪logical table保留，但当前audit已过滤','goal':'降低换入口/导出时目录表污染风险；本轮未证实造成正式误报'}]
comp['summary']={'sample_documents':8,'metadata_correct_documents':sum(d['metadata_matches'] for d in comp['documents']),'nine_core_tables_found_documents':sum(d['confirmed_core_table_presence'] for d in comp['documents']),'confirmed_structural_issue_false_positives':len(comp['false_positives']),'confirmed_structure_missed_issues_grouped':len(comp['missed_issues']),'false_insufficient_data_rule_records':len(comp['false_unresolved']),'false_insufficient_data_relationships':2,'latent_toc_artifact_documents':len(comp['latent_parser_observations'])}
(root/'structure_comparison.json').write_text(json.dumps(comp,ensure_ascii=False,indent=2))
print(json.dumps(comp['summary'],ensure_ascii=False,indent=2))
