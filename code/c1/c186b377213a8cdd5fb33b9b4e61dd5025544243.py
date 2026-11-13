from pathlib import Path
import csv,json
OUT=Path(__file__).resolve().parent
items=[
('R17-WF-01','忽略决定存在两套权威存储，可能跨版本误隐藏','统一到版本绑定的工作流决定；可绑定旧文件迁移，裸 ID 隔离留痕，完成锁继续生效','src/services/issue_workflow_store.py','tests/remediation/test_round17_closure.py'),
('R17-CSV-01','应收清单缺少可复验的 CSV 导入链路','新增模板、预览、哈希校验和原子导入；同名组织按 ID 隔离，冲突全批回滚，重放幂等','src/services/expected_material_import.py','tests/test_expected_material_import_pg.py'),
('R17-FACT-01','部分规则读取旧表值，校正投影未全程消费','已知九表兼容视图金额只取当前唯一事实，核对原件、版本、列、年度和阶段；记录实际消费者','src/engine/fact_table_projection.py','tests/remediation/test_round17_closure.py'),
('R17-OCR-01','原始扫描强金额单元格被低分邻格整体阻断','按原页原格真实区域复识别和关键字段证明准入；低分格继续阻断，保持原 98 分阈值','src/services/ocr_qualification.py','tests/remediation/test_round17_closure.py'),
('R17-PARSE-01','原生线框表和包含它的文本候选被重复建实例','同码、同页、几何包含的线框/文本候选去重，保留真正不同实例的拒答','src/services/pdf_parser.py','tests/remediation/test_round17_closure.py'),
('R17-ORG-01','API 新建组织后 Worker 仍使用旧目录，复核启动失败','跨进程目录签名刷新、锁定读改写和原子落盘，避免丢组织和任务关联','src/services/org_storage.py','tests/remediation/test_round17_closure.py'),
('R17-DR-01','恢复路径边界不严且遗漏组织和认证状态','归档全量预校验拒绝路径逃逸及链接；备份恢复组织、关联、用户状态，禁止覆盖非空目标','scripts/backup_all.py','tests/remediation/test_round17_closure.py'),
('R17-LINUX-01','Linux 解析守护子进程探测内存限额时再次派生子进程而失败','内存探测移至父进程，有限等待及取消回收；原生 Linux 超额分配被拒且无残留进程','src/services/pdf_parse_process.py','linux_native_before.log;linux_native_after.log'),
('R17-NUM-01','千分位逗号拆断金额并把 418 当作主体','保留金额内逗号；明确本年分母别名与年度继承/重置，异主体、上年度及基金口径仍阻断','src/engine/rules_v33.py','tests/remediation/test_round17_closure.py'),
('R17-NARR-01','已披露接待批次人数仍被提示缺失','接待说明范围内读取续段和数字前后词序；同时要求批次与人数，外宾专属计数不补国内披露','src/engine/rules_v33.py','tests/remediation/test_round17_closure.py'),
('R17-NARR-02','本年支出与含结转总计错比，并读取财政拨款章节','右侧总计与总体说明总计同口径比较；排除专项章节，缺原件事实不回退猜数','src/engine/rules_v33.py','tests/remediation/test_round17_closure.py'),
('R17-ROUND-01','收入横向汇总将 0.02 万元尾差报错','统一金额容差与显示舍入包络；10 万元等实际差额仍命中','src/engine/rules_v33.py','tests/remediation/test_round17_closure.py'),
('R17-COLUMN-01','跨页 7 列转 5 列时将项目支出当作合计','事实物化与兼容投影同时拒绝列宽漂移，保存低置信候选和原因；缓存版本升至 v8','src/services/fiscal_fact_materializer.py','tests/remediation/test_round17_closure.py'),
('R17-NARR-03','一般公共预算说明取数跨目录或其他章节','限定正文第五节，匹配实际金额原页，无法唯一定位时不填确定页码','src/engine/rules_v33.py','tests/remediation/test_round17_closure.py'),
('R17-CALIBER-01','决算收入与支出按科目被要求镜像相等','决算记录不适用及结转结余原因；预算镜像保留并采用统一金额容差','src/engine/common_rules.py','tests/test_rule_matrix_20260905.py;tests/remediation/test_round17_closure.py'),
('R17-NARR-04','接待费金额与下一章政府性基金金额错配','金额一致性只在同一数字条目与章节内比较，下一主章节截断','src/engine/rules_v33.py','tests/test_final_rule_alignment.py;tests/remediation/test_round17_closure.py')]
rows=[dict(id=i,title=t,status='fixed_in_release_candidate',after=a,source=s,regression=e,scope='engineering_remediation_not_human_business_truth') for i,t,a,s,e in items]
(OUT/'findings.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
with (OUT/'findings.csv').open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
print(len(rows))
