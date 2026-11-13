from pathlib import Path
import csv
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
REPORT = ROOT / 'docs/GovBudgetChecker_第十四轮整改复核报告_20261006.md'


def load(p):
    return json.loads(p.read_text())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


service = load(OUT / 'live_services/service_receipt.json')
runtime = load(OUT / 'ocr_service_review.json')
integrity = load(OUT / 'live_service_integrity.json')
exports = load(OUT / 'production_export_gate.json')
assert service['status'] == runtime['status'] == 'failed'
assert len(service['scenarios']) == 7 and len(exports['bundles']) == 20
before = load(OUT / 'source_snapshot_before.json')
after = {p: sha(ROOT / p) for p in before['files']}
diff = subprocess.check_output(['git', 'diff', '--binary'], cwd=ROOT)
source_unchanged = after == before['files'] and hashlib.sha256(diff).hexdigest() == before['diff_sha256']
assert source_unchanged
write('source_snapshot_after.json', {**before, 'files': after, 'source_unchanged': source_unchanged})

fresh_budget = load(ROOT / 'outputs/remediation_20261006_round13/services_final/original_scanned_budget/report.json')
fresh_facts = load(ROOT / 'outputs/remediation_20261006_round13/services_final/original_scanned_budget/fact_context.json')
fresh_status = load(ROOT / 'outputs/remediation_20261006_round13/services_final/original_scanned_budget/status.json')
fresh_mixed = load(OUT / 'live_services/original_mixed_final/fact_context.json')
header_facts = [f for f in fresh_mixed['facts'] if f.get('table_code') == 'FIN_02_income' and f.get('subject_name') == '栏次']
assert len(fresh_facts['facts']) == 22
assert all(f['recognition_status'] not in {'verified', 'unverified'} for f in fresh_facts['facts'])
assert len(header_facts) == 5 and all(f['recognition_status'] == 'unverified' for f in header_facts)
guard = fresh_status['meta']['evidence_completeness']
assert guard['formal_issue_total'] == 11 and guard['verified_count'] == 0

findings = [
    {
        'id': 'R14-01', 'priority': 1, 'severity': 'high', 'status': 'open',
        'title': '扫描识别未完成被断言为缺表，结构检查仍记完成',
        'file': 'src/engine/budget_rules.py', 'line': 2361,
        'related_files': ['api/main.py:1324', 'src/services/evidence_guard.py:618', 'src/engine/check_obligations.py:1238'],
        'document_type': 'budget', 'year': 2024,
        'organization': '金华市婺城区新狮街道社区卫生服务中心（AI目视确认，系统未识别）',
        'page': [12, 13, 14], 'bbox': None,
        'table_or_section': '单位收支预算总表、单位收入预算总表、单位支出预算总表',
        'snippet': '表1：2024年单位收支预算总表；表2：2024年单位收入预算总表；表3：2024年单位支出预算总表',
        'expected': '识别覆盖不足时返回insufficient_data/manual_review，不断言缺表；结构义务不计完成。',
        'actual': '原件表存在，最终输出11条BUD-001提示；已目视确认至少3条缺表判断不成立。正式计数标签11、认证0。',
        'reason': '金额操作数资格与结构视图共用准入；空规则视图没有识别前提就判缺失。',
        'suggestion': '分离结构存在性与金额资格；向结构规则传入页覆盖/候选/unknown状态；补单位收支标题别名邻例并同步回执、计数与四格式。',
        'confidence': 0.99, 'evidence_scope': '实际原件页图、真实服务及当前生产函数重放；未生成独立人审真值',
        'evidence_files': ['rules_audit/rules_audit.json', 'rules_audit/rules_audit.json', 'pdf_qa/claimed_missing_table.png'],
    },
    {
        'id': 'R14-02', 'priority': 2, 'severity': 'medium', 'status': 'open',
        'title': 'OCR候选物理页未与原始渲染和请求页绑定',
        'file': 'src/services/ocr_qualification.py', 'line': 49,
        'related_files': ['src/services/ocr_fact_promotion.py:306'],
        'document_type': 'final', 'year': 2025, 'organization': '测试局（合成契约）',
        'page': 2, 'bbox': None, 'table_or_section': '合成两页OCR资格边界',
        'snippet': '唯一变化candidate.page_number:1→2；coordinate_transform_id仍为ocr_render_page_1',
        'expected': '候选页/变换页/请求页不一致应拒绝；不产生可计算事实。',
        'actual': '仍qualified，2个可计算事实值100贴第2页，但input_sha256属于第1页；第2页已存响应值900。',
        'reason': '点框由coordinate_transform_id计算，页码另由candidate.page_number物化，两者没有同页检查。',
        'suggestion': '显式保存并校验physical_page及源/渲染/响应/请求身份，由一致的原始页物化事实；加入只改页号负例。',
        'confidence': 0.99, 'evidence_scope': '合成响应+实际300dpi两页渲染；正常生产流水线未观察到自行错页，未获扫描认证',
        'evidence_files': ['context_audit/context_audit.json', 'root_ocr_page_probe.json'],
    },
    {
        'id': 'R14-03', 'priority': 2, 'severity': 'medium', 'status': 'open',
        'title': '原生表栏次编号被物化为财政金额事实',
        'file': 'src/services/fiscal_fact_materializer.py', 'line': 265,
        'related_files': ['src/services/fiscal_fact_materializer.py:189', 'src/engine/fact_context.py:295', 'scripts/run_isolated_service_validation.py:478'],
        'document_type': 'final', 'year': 2024, 'organization': '金华市福利彩票发行中心',
        'page': 7, 'bbox': None, 'table_or_section': '2024年度收入决算表（分单位）',
        'snippet': '栏次1/2/3/4/5/6/7；下一行合计746.62',
        'expected': '栏次/行次/表头作为结构信息，不成为收入等财政金额操作数；实际746.62合计应保留。',
        'actual': 'FIN_02的栏次1/2/4/5/7五条进入definite_facts；真实通用校正选择了栏次1→11.0。',
        'reason': '首个含数字行被视为数据起点，未排除栏次/is_header；原生事实标unverified后允许进入操作数集合。',
        'suggestion': '在解析及物化层明确行角色并排除表头；验证驱动选择真实金额角色及核验框，保留真实零值/整数金额。',
        'confidence': 0.99, 'evidence_scope': '真实原件、持久化事实、通用校正回执；当前合计解析仍取746.62，未证明这些表头已造成实际错算/漏报',
        'evidence_files': ['rules_audit/header_probe.json', 'live_services/original_mixed_final/fact_context.json'],
    },
]
findings.extend([
    {**findings[0], 'id': 'R14-04', 'priority': 1, 'title': '完整原始扫描预算超过Worker固定600秒预算而失败', 'file': 'api/main.py', 'line': 1030, 'related_files': ['api/main.py:1041', 'scripts/run_isolated_service_validation.py:153'], 'page': None, 'table_or_section': '完整21页扫描处理链', 'snippet': 'pipeline_timeout_600s', 'expected': '完整扫描在声明支持的资源与时限内完成；阶段预算、取消和恢复策略与OCR页数/区域请求成本一致。', 'actual': '本轮同源同模型完整原件触发600秒Worker超时；3600秒服务等待参数并不改变Worker限时；任务error且无有效revision，整次failed。', 'reason': 'API/Worker完整管线默认600秒，包含逐页OCR/区域请求；等待端的timeout是另一个预算。', 'suggestion': '显式记录两种限时并按页数/阶段成本管理；优化OCR，支持可取消的分段进度及失败恢复；在资源上限内重跑完整原件，不能只延长UI等待。', 'evidence_files': ['original_scan_timeout_audit.json', 'live_services/service_receipt.json', 'ocr_service_review.json'], 'evidence_scope': '本轮真实失败；第十三轮曾成功不抹去，不声称每次必失败或OCR本身无模型'},
    {**findings[2], 'id': 'R14-05', 'priority': 1, 'severity': 'high', 'title': '校正重审缓存JSONB读回异常，服务验收漏检入库失败', 'file': 'src/services/parse_artifact_store.py', 'line': 446, 'related_files': ['src/services/structured_ingest_runner.py:262', 'scripts/run_isolated_service_validation.py:684'], 'snippet': 'dict(row.get(ocr_provenance) or {}) → ValueError', 'expected': '缓存原生/OCR单元格JSONB正确解码；校正重审的当前版本入库完成，服务验收核对ingest状态及当前版本事实。', 'actual': '实际原始混合决算校正后structured_ingest.status=error，日志有ValueError；场景service_validation_passed仍true。隔离PG原生{}和嵌套JSONB默认均读为str，当前表达式均报错。', 'reason': '缓存加载只对其他JSON字段json.loads，却对ocr_provenance直接dict；验证谓词只检查任务/结果存在和导出，未检查入库状态。', 'suggestion': '统一解码str/dict/null并检查对象形态；增加真实PG持久化→缓存读回→校正重审验证；验收必须断言原版/校正版ingest成功及本次版本事实证据与数据库一致。', 'evidence_files': ['cache_ingest_audit.json', 'pg_jsonb_probe.json', 'live_services/original_mixed_final/corrected/status.json'], 'evidence_scope': '真实服务异常+隔离PG类型复现；未在容器删除后直接查询金额行值，不泛称所有旧运行缓存均失败'},
])
write('review_findings.json', {'business_acceptance': 'not_passed', 'business_metrics': None, 'findings': findings})
with (OUT / 'review_findings.csv').open('w', encoding='utf-8-sig', newline='') as f:
    keys = list(findings[0])
    writer = csv.DictWriter(f, fieldnames=keys)
    writer.writeheader()
    for row in findings:
        writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})

ledger = [
    {'id': 'R12-01', 'status': 'closed_technical', 'basis': '逐引用认证旧反例及生产契约通过；不等于真实独立人审认证'},
    {'id': 'R12-02', 'status': 'partially_closed', 'basis': '原rows与点框篡改均拒绝；物理页绑定残留R14-02'},
    {'id': 'R12-03', 'status': 'closed_core', 'basis': '正常/正文异常顺序、后台写入测试及本轮真实7场景清理通过；低优先级注入边界另记'},
    {'id': 'R12-04', 'status': 'closed_contract', 'basis': '模板/文档/路径/SHA装配及反例通过；真实认证材料仍未提供'},
    {'id': 'R12-05', 'status': 'open_business', 'basis': '官方完整扫描已实跑；原始扫描22候选、0自动操作数，未获独立认证；另有R14-01'},
    {'id': 'R12-06', 'status': 'open_business', 'basis': '64空席位、0冻结材料、0人审缺陷组，不是32+32/100真值；正式门禁exit2'},
] + [{'id': f['id'], 'status': 'open', 'basis': f['title']} for f in findings]
write('review_ledger.json', ledger)
write('pdf_visual_qa.json', {
    'source_pages_visually_inspected': {'zhejiang_budget': [12, 13, 14], 'zhejiang_final': [7]},
    'source_page_method': 'MuPDF fresh renders plus independent subreview Poppler P12-P14',
    'output_pages_visually_inspected': {'claimed_original_scan_report': 22, 'fresh_corrected_synthetic_report': 2},
    'output_page_method': 'Poppler', 'unicode_math': '1010.0≠1000.0 and 99.01%≠100% visibly preserved',
    'render_limit': 'Poppler reported missing Adobe-GB1/china-s for retained source PDF fonts; inspected appended report text remains legible. MuPDF source renders inspected separately.',
    'final_delivery': 'Markdown/JSON/CSV review; rendered PNGs and PDFs used for QA are not new business truth',
})
write('parallel_review_status.json', {
    'authorization': 'AGENTS.md默认并行检查A-E',
    'completed': ['context_review10:OCR身份/物化/证据', 'acceptance_review10:清理/装配/独立准备', 'ratio_review10:原始结构/规则/表头'],
    'root': '完整回归、全部交接SHA、旧反例、断网真实模型推理、完整7场景实跑（整次failed）、四格式与页图核验、真实PG类型与缓存读回审计',
    'business_source_mutations': False,
    'additional_cache_subreview': 'not_completed_due_to_workspace_credits; root independently completed actual service/PG/cache audit',
})

report = f'''# GovBudgetChecker 第十四轮整改复核报告

日期：2026年10月6日。复核对象：第十三轮整改后的实际工作区，分支 `{before['branch']}`，HEAD `{before['head']}`。本轮只新增复核报告、复现程序和证据，业务源码、测试及既有交接证据保持不变。

**本轮工程修复有实质进展，但整体业务验收仍未通过。上轮四项核心反例均通过；R12-01、R12-03、R12-04可按相应技术范围核销，R12-02原金额/格框问题已修复，物理页绑定仍有残留。本轮确认5项可代码化问题，其中原始扫描超时、缺表误报，以及缓存读回/入库验收缺口直接影响使用。**

## 一、独立复核结果

| 检查 | 本轮独立结果 | 能证明的范围 |
|---|---|---|
| 全量pytest | 2582通过、137跳过、0失败，9条警告；64.37秒 | 当前回归通过，不能替代真实误报/漏报验收 |
| 整改测试 | 365通过、2跳过、0失败 | 跳过为主机未配置测试PG URL、主机未安装cv2；实际隔离PG及OCR容器另行验证 |
| Ruff全仓库、环境一致性、日志安全 | 全部通过 | 工程检查 |
| 原规则复现 | 原9例、37邻近例、0.50/0.51金额边界均符合预期 | 原R10规则修复未回退 |
| 交接身份 | 894项当前文件与第十三轮源码副本逐项相符；1825项注册产物SHA/大小全部相符 | 核对实际未提交源码及交接实物，未以HEAD替代被测身份 |
| OCR断网冷启动及真实推理 | models_ready，真实控制输入/原始响应SHA相符，pip check通过 | 已有本地组合镜像、固定模型、CPU=2；未验证Dockerfile从零构建 |
| 新隔离API→独立Worker→PG→OCR | {service['elapsed_seconds']}秒；整次failed。脚本标6/7通过，原始扫描预算error | 混合校正另有入库error而脚本漏检，不能把6个true笼统当完整服务通过 |
| 四格式、下载与版本 | 第十三轮10组视图+本轮10组视图，20组视图核验：19组可计算且一致，扫描失败版1组不可计算；重复/历史下载字节核对通过 | 实际导出的一致性，不能证明内容正确 |
| 正式空包门禁 | exit2、not_passed，业务指标not_computable | 没有冻结业务真值时保持不可计算 |

服务退出后独立核对新API/Worker进程停止、PG容器已删除、临时目录不存在、日志关闭，运行期间源码一致。输出截图已目视核对：缺表提示确实进入PDF；金额符号`≠`和99.01%保留。Poppler对保留原件字体有Adobe-GB1/china-s警告，原件另用MuPDF核对；附录文本正常可读。

## 二、上轮问题核销

| 项目 | 结论 | 核销边界 |
|---|---|---|
| R12-01 认证跨格借用 | 技术核销 | 同页同金额不同格仍逐条认证；混合引用整体unverified，逐引用正式门禁为true/false。模型阳性与外部人审认证继续区分。 |
| R12-02 候选金额/点框与原始预测脱节 | 部分核销 | 单改100→900、单改点框[1,1,2,2]均rejected；物化保留原始100且不进操作数。新增页号反例见R14-02，不能整体宣布身份绑定闭合。 |
| R12-03 临时目录清理竞态 | 核心核销 | service_lifetime先停止/wait后台进程、关闭日志，再退出TemporaryDirectory；正常/正文异常及持续写入测试通过，本轮整次业务运行失败，但进程/PG/目录/日志清理通过。 |
| R12-04 正式扫描认证装配 | 契约核销 | golden字段、相对路径、SHA及文档已补；缺字段/错SHA/绝对路径/越界拒绝，完整模拟装配可认证。没有把模拟材料变成真实人审认证。 |
| R12-05 原始扫描业务闭环 | 未核销，有进展 | 原始政府完整扫描和完整混合件已实跑；第十三轮已完成的原始扫描无可用操作数、无独立认证，本轮完整复跑又超时，不能沿用“没有实跑原始扫描”的旧理由，也不能因此宣布可用。 |
| R12-06 独立冻结业务验收 | 未核销 | 已有诚实准备工具和交接操作，但本轮准备包仍为64空席位、0冻结材料、0人审缺陷组。 |

清理模块另留一项低优先级注入观察：Docker删除命令自身抛OSError时最终失败回执未保存（脚本709行）。实际运行未发生，不作为本轮业务阻断；建议保护清理命令异常并保存失败及清理状态，见`acceptance_audit/acceptance_audit.json`。

## 三、本轮确认的代码问题

### R14-01 高优先级：扫描识别不足被断言为缺表

原件为金华市婺城区新狮街道社区卫生服务中心2024年单位预算，21页完整扫描，SHA `5978b569d1f4bf58d07281db4150e72b9cf867fa45a051da4976aabdc6a5eec7`。物理页12、13、14分别明确存在单位收支、收入、支出预算总表；印刷页码是11、12、13。页图由主审及结构子审分别检查，没有编造bbox。

系统因OCR资格未通过，使规则文本视图全空，仍输出7条缺表error和4条缺章节warn。已独立确认至少上述3条缺表判断不成立，其余提示未逐项全部核为误报。第十三轮真实服务保留11条BUD-001提示，本轮当前生产函数独立重放与其逐条一致；本轮完整服务因600秒超时没有生成该分析结果。第十三轮已存PDF附录仍显示“缺失预算表”。

触发链为`api/main.py:1324`按金额资格决定是否纳入OCR结构视图；`budget_rules.py:835`忽略空文本；`budget_rules.py:2361`没有识别前提就把无锚点判为表缺失。BUD-001被记fail，核心表义务随之记completed，尽管实际识别尚未完成。

计数也须读准：系统字段`formal_issue_total=11`、`rule_warning_count=11`、`verified_count=0`。`evidence_guard.py:470`只排除degraded_missing_evidence，未认证规则提示仍进入“正式问题”计数标签。这是当前系统分类，**不代表11条已认证问题**。整体结论仍诚实地保持review_required/incomplete；问题在缺表明细及该结构义务的结论与完成状态。

整改应将“表/章节存在性识别”与“数字可作为计算操作数”分开，给结构规则传入实际覆盖及unknown状态。未覆盖、无文本、候选未完成者应输出insufficient_data/manual_review和页图复核入口；真实缺失只有在完整、可用结构视图下才判定。必要邻例还包括“单位收支预算总表”：只传入实际目视标题的辅助探针目前不能命中BUD_T1，收入/支出标题则可命中；该探针不是生产OCR或人审真值。

验收标准：本原件P12—P14不得再报对应缺表；识别不足时结构检查不计完成；完整原生材料真实缺表仍能检出；单位/部门常见标题别名、横向页、空表和条件性表均覆盖；JSON、CSV、Word、PDF的结论、计数和原因一致。

### R14-02 中优先级：物理页与原始渲染/请求身份未绑定

在合成两页契约中，两页预先均有正常同主体/年度/单位锚，300dpi实际渲染SHA不同，第1页响应表值100，第2页已存响应表值900。仅改`candidate.page_number:1→2`，仍qualified并形成2个可计算事实，金额100贴在第2页，输入SHA却属于第1页。

`ocr_qualification.py:49`按coordinate_transform_id重算点框，没有检查变换归属与候选页一致；物化又按候选页号贴页。应把原件/物理页/渲染/原始响应/逐页请求日志固定成同一身份，由已绑定页构建事实。

这是资格输入边界反例。正常生产创建页号与变换ID一致，本轮**没有观察到官方扫描自行错页**；反例也未得到独立扫描认证。但错页事实已进入definite集合，后续认证守卫不能代替操作数资格的同页检查。

验收标准：仅改页号或混用页变换/响应时rejected且无可计算事实；两页合法候选各自维持原金额及页框；原100→900/点框篡改负例和真实合成OCR阳性继续通过。

### R14-03 中优先级：栏次编号成为财政金额事实

金华市福利彩票发行中心2024年决算原件物理页7的收入决算表中，“栏次”1—7是列编号，下一行真实合计为746.62万元。当前FIN_02将栏次1、2、4、5、7物化为total_actual/fiscal_allocation等5条财政事实，均进入definite_facts。本轮新服务再次保存同样的5条表头事实。

`fiscal_fact_materializer.py:265`把第一个含数字的行视为数据起点，189—239行没有排除栏次/is_header；原生条目被标unverified，FactContext允许其成为操作数。通用验证选择器也确实选中了栏次，把1改为11.0；该流程只能证明校正传递，不能作为金额校正阳性。

限制：当前合计角色解析仍正确选择746.62，已保存问题和检查回执对这5个表头ID的操作数引用为0；本轮没有证明它们已经导致实际错算或漏报。第十三轮另做746.62→756.62专用工程注入，记录与身份一致，但受影响关系状态未变，也未核销业务金额重算阳性。

整改应在原生解析和物化层固定行角色，将栏次/行次/表头留作结构信息，禁止进入财政金额集合。验证驱动须选择可核对的真实金额角色，明确校正前后关系及操作数变化。验收须保留真实746.62、真实零值及合法整数金额，不能靠排除所有整数粗糙处理。

### R14-04 高优先级：完整原始扫描600秒超时

本轮同源码、同模型、同原件的完整21页预算任务`881bdb5149844d1748b822b89bd40ab8`以`pipeline_timeout_600s`结束，Worker没有完成事件、analysis_revision为空，整次服务验证failed。验证端传入3600秒只扩大等待，`api/main.py:1030—1041`的完整管线仍默认600秒。第十三轮曾成功，本轮失败说明这项完整材料仍有时限/性能稳定性问题，不能用旧成功覆盖。

应同时记录等待与Worker阶段预算，按声明支持的页数、图像质量、区域请求成本进行性能验证，并提供可取消的分段进度和恢复。优化/合理配置之后重跑完整原件，保存实际耗时、阶段完成、当前版本及清理回执；不能以截页、跳OCR或只改UI等待作为通过。不要取消总资源上限。

### R14-05 高优先级：缓存JSONB读回错误被验收漏过

原始混合决算任务`7be34a3fe28e2fa3f6d75f38634c35ea`校正重审后，实际`meta.structured_ingest.status=error`，Worker日志定位`parse_artifact_store.py:446`：`dict(row.get("ocr_provenance") or {{}})`抛ValueError。缓存命中由`structured_ingest_runner.py:262`恢复解析对象，当前单元格JSONB没有像其他字段一样解码。主审另用同镜像隔离PostgreSQL16实测：空原生对象和嵌套OCR对象均由asyncpg读成str，当前表达式都报错，json.loads可正确还原。临时PG已清理。

服务脚本684行验收谓词只检查任务/结果存在、导出一致及校正传递，没有检查本次结构化入库状态，因此此场景仍标service_validation_passed=true。必须把缓存解码和验收谓词一起修：兼容str/dict/null、拒绝非法对象；真实PG完整持久化→缓存读回→校正重审→当前版本事实证据核验。原版/校正版ingest错误必须使场景失败，不能借此前数据库里存在的行数充当本次入库完成。

本轮没有在容器清理后直接查询原生金额行的最终数据库值，不声称已证明金额存成旧值；已证明的是缓存恢复异常、结构化入库失败及验证脚本漏检。第十三轮最终日志未含同一错误，仍保留其历史证据。

## 四、实际原始材料与可用性边界

| 材料 | 本轮观察 | 可用性结论 |
|---|---|---|
| 21页官方原始扫描预算 | 第十三轮21页OCR成功，22候选/0操作数；本轮真实复跑600秒超时，未完成分析 | 技术稳定性及自动数字审校均未闭合；历史成功和本轮失败分别保留 |
| 35页官方原始混合决算 | P1扫描封面、P2—P35原生正文；版本/导出通过，但本轮校正重审缓存加载导致结构化入库error | 不能宣称完整持久化通过；同时存在原生栏次事实污染 |
| 合成扫描控制 | 真实模型形成2个可计算操作数，证据仍unverified；1000→1010后金额/比例关系变fail，比例99.01% | 保留真实模型阳性与金额重算工程证据；不计官方扫描、独立人审或盲测 |

第十三轮已存扫描预算的资格原因包含主体未识别、关键识别分数不足及区域复识别未完成/未绑定。安全地拒绝计算是正确保护，但不能用“操作数为0”包装成可自动审校。下一步应核对主体/单位/阶段识别、横向扫描页与图像预处理、格映射及真实区域请求，再用独立开发真值校准阈值；不应单纯调低阈值把候选硬转为可靠事实。扫描续表自动合并、人工位置覆盖仍为已有能力限制。

本轮核对本地原件字节、页图、交接来源URL及SHA，没有重新下载，也没有将AI观察注册为人审真值。两份原件已用于开发验证，不能再改标为未接触盲测。

## 五、下一批整改与业务验收

1. **先修R14-04、R14-05，再收口R14-01、R14-02、R14-03。** 同步修生产结构状态、事实集合、关系回执和四格式明细；用本轮原件及有限反例验收，保留真实缺表、合法金额和真实模型正例。低优先级清理回执保护可同批收尾。
2. **完成原始扫描数字阳性和独立认证。** 由实际人员复核开发扫描件的主体、金额、物理页及区域，按golden路径/SHA装配真实认证。至少证明完整原件的核心金额能正确进入操作数，并通过一次真实金额覆盖产生预期关系结果；低质量输入保持人工复核，不冒充完成。
3. **完成冻结业务验收。** 实际保管人/标注人/复核人提供开发、盲测各32份，预算/决算×部门/单位×原生/扫描8层各至少4份；盲测至少100个真实独立缺陷组。补原件SHA、近重复与泄漏审查、完整适用分母、区域真值、分歧复核、访问隔离、冻结/解封签认及正式四场景。

门槛继续为precision≥98%、recall≥95%、重大问题零漏报、精确定位≥98%、正式证据真实性100%、越界0、确定性适用检查完成≥95%、状态/原因覆盖100%、四格式和同版本重复运行100%一致。同一冻结源码/模型/配置/原件及覆盖层运行两次，先冻结系统输出再解封真值。本轮7场景整次failed、19组可计算导出一致，不转换成这些业务指标。

修复实际运行失败后，可在人工核验原件、明确未完成项的条件下辅助试用，尚不能承诺“任意预算/决算上传后都自动判断正确并精确定位”。能诚实识别无法完成的检查，也是可用性的必要条件。

## 六、证据与复跑入口

主证据目录：`outputs/reacceptance_20261006_round14/`。完整源文件匹配和产物匹配分别见`source_handoff_integrity.json`、`artifact_handoff_integrity.json`；测试见各log及run.json；旧反例见`scan_guard_probe.json`、`ocr_qualification_probe.json`、`root_ocr_page_probe.json`；真实模型与服务见`ocr_service_review.json`、`live_services/service_receipt.json`、`live_service_integrity.json`、`production_export_gate.json`。

结构/数字/口径/证据/交付按AGENTS.md并行检查，三条子审均完成，证据分别为`rules_audit/rules_audit.json`及`header_probe.json`、`context_audit/context_audit.json`、`acceptance_audit/acceptance_audit.json`。其边界与主审实跑分开记录；没有使用历史失败的子审作为完成证据。服务失败后追加缓存子审因额度不足未完成；R14-04/05由主审独立完成，未把失败委派算成已审。

结构化交付为`review_findings.json`、`review_findings.csv`和`review_ledger.json`。`source_snapshot_before/after.json`证明本轮{len(before['files'])}项受保护文件及业务diff未改；`artifact_manifest.json`记录本轮证据与报告SHA。复跑应使用新目录保留旧证据；`run_ocr_service_review.py`只删除自身创建的OCR容器，验证脚本只清理自身API/Worker/PG。
'''
REPORT.write_text(report)
files = [{'path': str(p.relative_to(OUT)), 'sha256': sha(p), 'bytes': p.stat().st_size} for p in sorted(OUT.rglob('*')) if p.is_file() and p != OUT / 'artifact_manifest.json' and '__pycache__' not in p.parts]
write('artifact_manifest.json', {'scope': 'round14 independent review, engineering evidence only; no fabricated human truth', 'files': files, 'report_path': str(REPORT.relative_to(ROOT)), 'report_sha256': sha(REPORT)})
print(json.dumps({'report': str(REPORT), 'source_unchanged': source_unchanged, 'finding_count': len(findings), 'registered_artifacts': len(files), 'service_status': service['status'], 'business_acceptance': 'not_passed'}, ensure_ascii=False))
