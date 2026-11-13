"""Write the R17 report only from the final checked, immutable service evidence."""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
checks=read(OUT/'self_check_final_delivery.json')
assert checks['status']=='passed_for_remediation_development_scope; entire_PLAN_not_accepted'
receipt=read(OUT/'final_delivery_services/service_receipt.json')
assert receipt['status']=='passed' and receipt['cleanup']=='complete'
corpus=read(OUT/'shanghai/final_delivery/final_development_audit.json')
assert corpus['technical_integrity_passed'] and corpus['complete']
scan=next(x for x in receipt['scenarios'] if x['scenario']=='original_scanned_budget')
mixed=next(x for x in receipt['scenarios'] if x['scenario']=='original_mixed_final')
plan=read(OUT/'plan_matrix.json')
fixed=read(OUT/'findings.json')
backend=checks['backend']
new=checks['real_shanghai_development']
coverage=new['completed_obligations']/new['applicable_obligations']
rates=[x['completed']/x['applicable'] for x in corpus['documents']]
baseline=read(OUT/'shanghai/final_development_audit.json')
previous=sum(x['completed'] for x in baseline['documents'])
views=checks['services']['exports_independently_reopened_views']
prep=read(OUT/'independent_intake_final_delivery/preparation_receipt.json')
assert checks['ocr_runtime_cleanup']['status']=='complete'
assert all(x['status']=='engineering_verified_in_final_live_scope' for x in fixed)

def cell(v):
    return str(v if v is not None else '未识别到').replace('|','\\|').replace('\n',' ')

lines=[
'# GovBudgetChecker 第十七轮整改与真实材料复验报告',
'',
'日期：2026-10-07。依据：系统整改 PLAN、验收 Gate A–F、当前 AGENTS.md v3_3_portable。',
'',
'## 1. 审校与验收结论',
'',
'**最终冻结代码的本轮工程自检通过；整个系统仍未通过正式验收。**',
'本轮已继续修复真实材料暴露的解析、取数、口径、证据和导出缺陷，并在修复后重新跑完真实服务。工程剩余项和独立业务验收均在下文列明。',
'',
'材料类型为决算公开；桌面原件共 20 份、678 页，封面年度均为 2025。发布单位均已从封面原文提取并与实际原页核对，未使用文件名猜测。',
'例如文件名“海虹桥国际中央商务区管理委员会.pdf”少首字，系统仍按封面识别“上海虹桥国际中央商务区管理委员会”。来源链接与下载时间未提供，保留为空。',
f'20 份全部经真实 API、Worker、PostgreSQL 入库与四格式导出。检查义务合计完成 **{new["completed_obligations"]}/{new["applicable_obligations"]}（{coverage:.2%}）**，逐份范围 {min(rates):.2%}–{max(rates):.2%}；同一批原件上，修复前是 {previous}/860。',
'完成率的分子是已执行并能出具结果的检查义务，分母是当前适用义务；它不是识别准确率。本次服务验证明确关闭外部 AI 调用，20 项 AI 语义复核未执行，另 297 项因确定性取数不足未完成；不能用服务成功替代完整审校。',
f'系统导出问题候选 **{new["candidate_issues"]}** 条，另有信息提示 {sum(d.get("tip_count",0) for d in corpus["documents"])} 条；其中高严重度且证据未完整绑定的候选 {new["unverified_high_severity_candidates"]} 条仍受待复核门禁约束。候选均未取得独立人工裁决，不等于确认的材料错误。',
'',
'独立人员、未暴露的分层材料、封存真值和授权揭盲尚未提供。正式精确率、召回率、重大问题漏报率均未评估；上海同城同年决算不能替代预算/决算、部门/单位、文本/扫描的八分层盲测。',
'',
'## 2. 修复问题与检查结果',
'',
'### 原第十四轮五项问题',
'',
'| 问题 | 最终复验结果与实际边界 |',
'|---|---|',
f'| 21 页原始扫描 600 秒超时 | 完整原件 1–21 物理页全部实际 OCR 成功、失败页 0；耗时 {scan["pipeline_budget"]["elapsed_seconds"]:.2f} 秒，有限页数预算 {scan["pipeline_budget"]["total_seconds"]} 秒。未截页、未关闭 OCR；已避免固定 600 秒中断，但没有达到“整份 600 秒内”的性能结论。 |',
f'| 校正缓存读回入库失败却标通过 | 35 页混合原件真实金额 {mixed["correction"]["original_raw_value"]} → {mixed["correction"]["corrected_raw_value"]}；新版本实际入库完成，缓存命中、JSONB provenance 解码、数据库原始行、覆盖层账本及当前 FactContext 一致；旧四格式导出 SHA 不变。失败场景不会被验收脚本计为通过。 |',
'| 实际扫描表被报缺表 | 最终原始 21 页结果和 PDF/DOCX 中没有确定性“缺失预算表”；无法确定的表结构和金额校验保留识别风险及待复核。 |',
'| OCR 页号未完整绑定 | 可用金额事实逐一核对原件 SHA、原始物理页和原格引用；页级 OCR、区域复识别及候选身份留档。关键身份不完整不得成为操作数。 |',
'| 表头栏次编号成为金额 | 原始扫描和混合原件事实均没有“栏次/行次”操作数；序号与真正整数金额正负例受测。 |',
'',
f'完整 21 页扫描当前只有 **{checks["services"]["scan_eligible_amount_facts"]}** 个金额事实通过操作数准入。它证明局部金额可用，不能据此宣称整表勾稽完成或 OCR 准确率通过。',
'',
'### 本轮自查新增并修复的缺陷',
'',
'新增修复覆盖：独立行封面单位、标准表题选择、原格空值缓存、多层表头金额列、三公预算/决算分列、左右科目、重复格与跨页续行、合计角色、原文证据和四格式主证据页码。',
'生成解释单独保存；来源认证只绑定原件唯一字面片段，或本版实际消费的完整公式操作数。纵向勾稽须包含父项与全部直接子项；证据不足的公式不会凭父项片段升级为已认证。',
'',
'| 修复编号 | 问题 | 修复后行为 |',
'|---|---|---|',
*[f'| {cell(x["id"])} | {cell(x["title"])} | {cell(x["after"])} |' for x in fixed],
'',
'### 最终工程验证',
'',
f'- 后端：{backend["passed"]} 通过、142 跳过、0 失败；另在真实 PostgreSQL 执行 140 项全部通过、0 跳过。合计实际执行 {backend["ordinary_and_pg_executed_total"]} 项通过。',
'- 剩余两项本机平台相关检查分别在原生 Linux 和实际 OCR 镜像复核；后者只证明 OpenCV PNG/multipart 协议兼容，不是模型识别精度评测。',
f'- 冻结源码 {checks["final_frozen_source"]["file_count"]} 件；最终 27 个真实服务场景全部通过，运行前后源码指纹一致。三条真实校正链通过；{views} 个视图独立重开 JSON/CSV/DOCX/PDF 对账通过，共 {views*4} 个导出构件。',
f'- 前端 23 组 unit 脚本、lint、类型检查、生产构建及 70 项生产浏览器夹具通过，0 重试。前端和端到端夹具 {checks["frontend_frozen_file_count"]} 件源码使用当前版本重新验证。网络拦截的浏览器夹具与真实 API 浏览器验证分别记录。',
'- 实际生产 Next 页面在真实 API/PG 上完成登录、应收 CSV 预览、幂等导入、模板下载与台账访问；未拦截网络。属于自动化页面验证，尚无实际业务用户签收。',
'- 全量备份恢复至另一临时数据库和空目录：所有数据库表的行数/有序内容摘要、上传文件 SHA、审计与应用状态 SHA 一致；重启后登录、组织和阻塞复核状态一致，四格式导出 SHA 一致。含认证状态的恢复归档保留私有权限。',
'- 依赖、环境对账、日志消息与改动静态检查通过；R15/R16 清册记录的 729/1748 件输出留档 SHA 复核未变（清册自身另记 SHA）。',
'- 一次性服务、前端、数据库及本轮 OCR 容器已回收；桌面原件逐份 SHA 复核未变。',
'',
'两条旧样本“未完成总数”测试作了有证据的基线修正：仅三项 V242 空白结转金额不再被当作 0，明确改为待复核，其余未完成项仍遵守原上限。原件和历史产物未改写。',
'自检过程中发现的失败和中断试跑全部留档，未计入最终通过回执；其中四格式页码不一致由原验收门禁拦下，修复共享报告字段后重新全链复跑，未降低对账标准。',
'',
'### 上海真实材料逐份结果',
'',
'以下候选数不含信息提示，不是人工确认错误数。“入库通过”仅指技术链；本轮业务验收均未完成。',
'',
'| 原件 | 页数 | 原始事实 / 可用操作数 | 问题候选 | 已完成 / 适用检查 | 状态 |',
'|---|---:|---:|---:|---:|---|',
*[f'| {cell(x["filename"])} | {x["pages"]} | {x["facts"]} / {cell(x["operand_count"])} | {x["issue_count"]} | {x["completed"]} / {x["applicable"]} | {x["status"]}，入库通过 |' for x in corpus['documents']],
'',
'可导出问题清单在 `shanghai/final_delivery/real_candidate_issues.json/csv`，保留原规则、严重度、年度、单位、物理页、bbox、表名/章节、片段、期望/实际/差额、原因、建议、置信度和证据状态。无法识别的字段保持 null；bbox 缺失不得补造。',
'',
'## 3. 待人工复核与尚未完成工作',
'',
'需逐项人工复核：系统候选是否为真实材料错误、条件性空表与无预算说明的口径、跨页列宽漂移、金额单位/低置信识别、证据定位及全部重大遗漏。源引用认证只证明“来自此原件此页”，不代表业务判断已经签认。',
'',
'根据 PLAN V2.0 的 WP4–WP6，这些全量迁移和基准比较依赖 WP-C2 正式精度基线；当前人工真值尚未冻结，不能据开发复测宣称其验收完成。',
'工程上仍有全量 Decimal/根因迁移、少数跨页列身份与缺金额拒答、全量规则事实消费、8 个 checker 的真实反例基准及大型规则单体工程债。历史 450 个任务的处置须结合当前实际台账与业务决定，未批量删除。',
'',
'| 工作包 | 当前状态 | 已验证范围 | 尚未完成 |',
'|---|---|---|---|',
*[f'| {cell(x["work_package"])} | {cell(x["status"])} | {cell(x["evidence_scope"])} | {cell(x["remaining"])} |' for x in plan],
'',
'| Gate | 当前结论 |',
'|---|---|',
'| A 数据与状态 | 当前身份、年份、版本、CSV 自动化验证通过；历史实际台账签收待完成。 |',
f'| B 完整性 | 未完成检查被明确阻断；{new["completed_obligations"]}/{new["applicable_obligations"]} 不能称完整自动审校。 |',
'| C 误报与证据 | 全体未通过：无独立精确率，部分候选定位/公式证据不足。 |',
'| D 漏报 | 未评估：无完整冻结人工真值及真实反例基准。 |',
'| E 审核闭环 | 迁移、锁定和重启阻断技术通过；完整人工复核签收未完成。 |',
'| F 可靠性 | 本次全链与全量恢复演练通过；部署目标机资源和业务用户签收未完成。 |',
'',
'## 4. 交付与独立盲测交接',
'',
'最终证据目录：`outputs/completion_20261007_round17/`。此前目录是自查留档，最终结论只引用下面这一组：',
'',
'- `self_check_final_delivery.json` 与 `final_delivery_services/service_receipt.json`：最终工程自检与 27 场景真实回执。',
'- `pytest_final_delivery.log`、`pytest_postgres_final_delivery.log`：完整后端与真实数据库检查。',
'- `shanghai/source_manifest.json`、`metadata_observation.json`：桌面原件身份及字面观察。',
'- `shanghai/final_delivery/`：20 份逐文件审计、实际候选及同原件 v8/v10 覆盖差异，未生成业务准确率。',
'- `findings.json/csv`、`plan_matrix.json/csv`：修复行为和剩余工作逐项状态。',
'- `shanghai/final_delivery/manual_review.pending.json/csv` 与 `obligations_review.pending.json/csv`：实际候选与 860 个适用检查的人工待填清单，裁决/人员/签认均为空；信息提示另列。',
'- `export_self_check_final_delivery.json`、`visual_qa/final_delivery/`：四格式重开对账与最新 PDF 渲染抽查。当地 Poppler 对继承注释报 CJK CMap 警告，另保存 PyMuPDF 渲染；未据此宣称所有阅读器兼容。',
'- `technical_preflight_final_delivery/`：完整原始扫描、实际缓存读回、原始 DB 查询、JSONL 账本和当前 FactContext 的技术前置附件。',
'- `independent_intake_final_delivery/`：最终冻结源码、开发材料排除、64 个待填槽及角色/真值/签认模板。`independent_handoff_gate_final_delivery.json` 保持未通过，未读取封存真值。',
'- `artifact_manifest.json`：本轮构件 SHA 清册；敏感归档只记录字节身份，不展示认证内容。',
'',
f'冻结代码清册 SHA-256：`{prep["code_manifest_sha256"]}`。原件、校正、入库、事实消费和导出均可按最终回执交叉复核。',
'',
'独立交接详见《第十七轮独立验收交接补充_20261007.md》。实际独立人员须提供新分层完整原件及官方来源、角色签认、近重复/分歧裁决、人工缺陷组和适用分母、封存真值、同一冻结身份的两次运行与授权揭盲指标；当前附件没有这些输入，未填写姓名、签字或通过结论。',
'建议业务人员先裁决本轮实际候选并补核遗漏；独立盲测另外选择未向开发会话暴露的完整材料。全部工程范围与 Gate A–F 达标后才可正式验收。',
]
target=ROOT/'docs/GovBudgetChecker_第十七轮整改与真实材料复验报告_20261007.md'
target.write_text('\n'.join(lines)+'\n')
print(target)
