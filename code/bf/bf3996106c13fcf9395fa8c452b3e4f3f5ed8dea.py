"""Write finite AI visual observations, never scan attestations or frozen goldens."""
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import pymupdf
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
SOURCE = OUT.parent / "original_materials"
PDFS = {name: pymupdf.open(SOURCE / f"zhejiang_{name}.pdf") for name in ("budget", "final")}
D = Decimal


def native(page, quote, section):
    rectangles = [list(rect) for rect in PDFS["final"][page - 1].search_for(quote)]
    return {
        "physical_page": page,
        "printed_page": page - 3,
        "quote": quote,
        "table_or_section": section,
        "bbox": [round(min(r[0] for r in rectangles), 3), round(min(r[1] for r in rectangles), 3),
                 round(max(r[2] for r in rectangles), 3), round(max(r[3] for r in rectangles), 3)] if rectangles else None,
        "bbox_regions": rectangles,
        "bbox_basis": "PDF native text search; relevant page rendered and visually inspected",
        "evidence_status": "AI observation, not independent human certification",
    }


def scan(page, quote, section, pixels=None):
    pdf_page = PDFS["budget"][page - 1]
    png = OUT / "numeric_pages" / f"budget-{page:02}.png"
    width, height = Image.open(png).size
    bbox = None if pixels is None else [round(pixels[0] * pdf_page.rect.width / width, 3),
                                      round(pixels[1] * pdf_page.rect.height / height, 3),
                                      round(pixels[2] * pdf_page.rect.width / width, 3),
                                      round(pixels[3] * pdf_page.rect.height / height, 3)]
    return {
        "physical_page": page,
        "printed_page": page - 1 if page > 1 else None,
        "quote": quote,
        "table_or_section": section,
        "bbox": bbox,
        "bbox_basis": "Coarse visual region selected on the actual 110 dpi Poppler render; not a cell bbox" if pixels else "No exact cell bbox; inspect the saved full-page render",
        "render_path": str(png.relative_to(ROOT)),
        "render_sha256": sha256(png.read_bytes()).hexdigest(),
        "evidence_status": "AI visual transcription, not OCR service output and not independent human certification",
    }


def ratio(numerator, denominator):
    return str((D(numerator) / D(denominator) * 100).quantize(D("0.01")))


checks = [
    {"check_id": "B-C-001", "document": "budget", "status": "consistent_observed", "rule_id": "C-003", "description": "收入总额与两项来源一致", "calculation": "346.22 + 2199.70 = 2545.92 万元", "evidence": [scan(4, "一般公共预算346.22万元，占13.60%；事业收入2,199.70万元，占86.40%", "收入预算安排情况说明"), scan(12, "财政拨款346.22；事业收入2,199.70；本年收入合计2,545.92", "表1：2024年单位收支预算总表", [80, 120, 840, 700])]},
    {"check_id": "B-C-002", "document": "budget", "status": "rounding_tolerance", "rule_id": "C-003", "description": "功能分类一级分项与总额存在0.01万元尾差，未判定错误", "calculation": "85.30 + 2345.72 + 114.89 = 2545.91；列示总额2545.92；差0.01万元 < 0.5万元容差", "evidence": [scan(4, "社会保障和就业支出85.30万元、卫生健康支出2,345.72万元、住房保障支出114.89万元", "支出预算安排情况说明"), scan(12, "本年支出合计2,545.92", "表1", [80, 120, 840, 700])]},
    {"check_id": "B-C-003", "document": "budget", "status": "consistent_observed", "rule_id": "C-002", "description": "全口径支出用途分项合计及比例一致；不混入一般公共预算基本支出0的口径", "calculation": "1048.09 + 96.61 + 1401.22 = 2545.92；41.17%、3.79%、55.04%（重算取两位）", "evidence": [scan(5, "人员支出1,048.09万元，占41.17%；日常公用支出96.61万元，占3.79%；项目支出1,401.22万元，占55.04%", "支出用途分类说明"), scan(14, "合计2,545.92；人员支出1,048.09；公用经费96.61；项目支出1,401.22", "表3：2024年单位支出预算总表")]},
    {"check_id": "B-C-004", "document": "budget", "status": "consistent_observed", "rule_id": "C-004", "description": "财政拨款收支总表与一般公共预算支出表一致", "calculation": "234.46 + 111.76 = 346.22 万元；表4与表5合计均346.22", "evidence": [scan(15, "一般公共预算346.22；乡镇卫生院234.46；基本公共卫生服务111.76", "表4：2024年单位财政拨款收支预算总表", [80, 75, 850, 445]), scan(16, "合计346.22；项目支出346.22", "表5：2024年单位一般公共预算支出预算表", [95, 95, 840, 365])]},
    {"check_id": "B-D-001", "document": "budget", "status": "consistent_observed", "description": "一般公共预算基本支出为空表，正文明确0.00，并有空表说明", "evidence": [scan(6, "一般公共预算基本支出0.00万元；人员经费0.00万元；公用经费0.00万元", "一般公共预算基本支出情况说明"), scan(17, "本单位没有一般公共预算基本支出拨款安排的支出，故本表无数据", "表6：2024年单位一般公共预算基本支出预算表", [90, 85, 825, 335])]},
    {"check_id": "B-D-002", "document": "budget", "status": "consistent_observed", "rule_id": "C-001", "description": "三公三项正文均0.00；表格为空且有不安排说明", "calculation": "0 + 0 + 0 = 0 万元；没有把表中空白假写为已识别数值0", "evidence": [scan(6, "2024年“三公”经费预算金额为0.00万元", "一般公共预算“三公”经费预算情况说明"), scan(7, "公务接待费预算0.00万元；公务用车购置及运行维护费预算0.00万元", "三公分项说明"), scan(18, "本单位没有一般公共预算安排的“三公”经费支出，故本表无数据", "表7：2024年单位一般公共预算“三公”经费支出预算表", [95, 65, 835, 430])]},
    {"check_id": "B-D-003", "document": "budget", "status": "consistent_observed", "description": "政府性基金与国有资本经营预算均有空表和正文无安排说明", "evidence": [scan(6, "2024年没有使用政府性基金预算安排的支出；没有使用国有资本经营预算拨款安排的支出", "政府性基金／国有资本说明"), scan(19, "本单位没有政府性基金预算拨款安排的支出，故本表无数据", "表8", [95, 80, 835, 340]), scan(20, "本单位没有国有资本经营预算拨款安排的支出，故本表无数据", "表9", [85, 60, 845, 290])]},
    {"check_id": "B-D-004", "document": "budget", "status": "consistent_observed", "description": "同比比例与给定变化量可自洽，原因文字已出现；未取到上年度原件，不能独立核验基期金额", "calculation": "1458.82 / (2545.92 + 1458.82) = 36.43%；496.04 / (346.22 + 496.04) = 58.89%", "evidence": [scan(4, "比上年执行数减少1,458.82万元，下降36.43%，主要原因是因主管部门部分项目年初还未进行分配", "收入／支出同比说明"), scan(5, "比上年执行数减少496.04万元，下降58.89%", "一般公共预算当年拨款规模变化情况")]},
    {"check_id": "B-D-005", "document": "budget", "status": "manual_review", "description": "绩效说明存在，但提及详见表11；所提供21页PDF止于表10。需核对发布页独立绩效附件", "evidence": [scan(8, "预算绩效情况说明（详见表11）；涉及资金1,401.22万元，一级项目1个", "预算绩效情况说明"), scan(21, "表10：2024年单位项目支出预算表；医疗卫生事务1,401.22；一般公共预算346.22；单位资金1,055.00", "文件末页表10")]},
    {"check_id": "F-C-001", "document": "final", "status": "consistent_observed", "rule_id": "D-001", "description": "收入支出总计、收入表、支出表及正文主数一致；结余分配栏空白，仅按未列示金额处理", "calculation": "270.18 + 476.44 = 746.62；支出总计746.62，本年支出746.62，年末结余0.00", "evidence": [native(6, "746.62", "收入支出决算总表续页"), native(7, "746.62", "收入决算表（分单位）"), native(9, "270.18", "支出决算表（分单位）"), native(19, "本年支出合计746.62万元", "支出决算情况说明")]},
    {"check_id": "F-C-002", "document": "final", "status": "consistent_observed", "rule_id": "D-003", "description": "基本支出／项目支出结构比例一致", "calculation": "270.18 / 746.62 = 36.19%；476.44 / 746.62 = 63.81%", "evidence": [native(19, "基本支出270.18万元", "支出决算情况说明"), native(19, "占36.19%", "支出决算情况说明"), native(19, "项目支出476.44 万元", "支出决算情况说明"), native(19, "占63.81%", "支出决算情况说明")]},
    {"check_id": "F-C-003", "document": "final", "status": "consistent_observed", "rule_id": "D-007", "description": "三公合计、三项和正文金额／结构一致；同比77.24%按给定两位金额重算为77.27%，差0.03个百分点未判错误", "calculation": "0 + 0 + 0.20 = 0.20；0.20 / 0.20 = 100%；0.68 / (0.20 + 0.68) = 77.27%", "evidence": [native(18, "0.20", "财政拨款“三公”经费支出决算表"), native(21, "支出决算为0.20 万元", "三公经费总体情况说明"), native(21, "下降77.24%", "公务接待同比说明")]},
    {"check_id": "F-D-001", "document": "final", "status": "consistent_observed", "description": "政府性基金收入／支出746.62，基本270.18、项目476.44；基金占支出100%；一般公共预算和国有资本无安排说明存在", "evidence": [native(16, "476.44", "政府性基金预算财政拨款收入支出决算表"), native(20, "占本年支出合计的100.00%", "政府性基金总体情况说明"), native(20, "本单位2024 年度无一般公共预算财政拨款收支安排", "一般公共预算情况说明"), native(21, "本单位2024 年度无国有资本经营预算财政拨款收支安", "国有资本情况说明")]},
    {"check_id": "F-D-002", "document": "final", "status": "consistent_observed", "description": "彩票市场管理项目145／138.25执行率95.34，与自评表资金行一致；年度总体目标文本另列95.17需人工复核", "calculation": "138.25 / 145 = 95.34%；若金额取整数138，138 / 145 = 95.17%", "evidence": [native(24, "项目全年预算数为145 万元，执行数为138.25 万元", "彩票市场管理自评综述"), native(26, "95.34%", "彩票市场管理绩效自评表年度资金行"), native(26, "95. 17%（138/145）", "彩票市场管理年度总体目标实际完成情况")]},
]

issues = [
    {"issue_id": "AI-F-001", "document": "final", "rule_id": "R004", "severity": "critical", "status": "observed_material_inconsistency_pending_independent_review", "title": "财政拨款总体说明用零年初预算计算完成率，且与基金口径冲突", "year": 2024, "organization": "金华市福利彩票发行中心", "actual": "财政拨款支出年初预算0.00万元，完成年初预算0.00%，无财政拨款支出安排；同页基金年初预算975.79，决算746.62", "expected": "零分母不能计算完成率；财政拨款总体应与政府性基金财政拨款口径衔接，或明确仅指一般公共预算", "reason": "财政拨款包含政府性基金，正文总体没有限定一般公共预算；976级基金预算与总体零预算及无安排说明并列冲突", "suggestion": "核对财政拨款总体年初预算及标题口径，并按实际基数修改完成率／无安排表述", "confidence": 0.99, "evidence": [native(19, "财政拨款支出年初预算数0.00 万元", "财政拨款收入支出决算总体情况说明"), native(19, "完成年初预算的", "同段跨页句"), native(20, "0.00%，主要原因是无财政拨款支出安排。", "跨页续句"), native(20, "975.79 万元，支出决算为746.62 万元", "政府性基金具体情况说明")]},
    {"issue_id": "AI-F-002", "document": "final", "rule_id": "D-008", "severity": "high", "status": "observed_material_inconsistency_pending_independent_review", "title": "政府性基金绩效自评资金占比与项目支出总额不一致", "year": 2024, "organization": "金华市福利彩票发行中心", "actual": "453.32万元，占政府性基金预算项目支出总额100%", "expected": "若分母使用公开09表项目支出476.44万元，占比应为95.15%；如另有不需自评资金，应说明分母范围", "difference": "23.12万元；4.85个百分点", "reason": "公开09表基金项目支出476.44，与绩效管理段指称的政府性基金项目支出总额口径直接相关", "suggestion": "复核自评覆盖金额及可适用分母，补明排除资金依据或修正占比", "confidence": 0.96, "evidence": [native(24, "共涉及资金453.32", "预算绩效管理工作开展情况"), native(24, "占政府性基金预算项目支出总额的100%", "预算绩效管理工作开展情况"), native(16, "476.44", "公开09表政府性基金项目支出")]},
    {"issue_id": "AI-F-003", "document": "final", "rule_id": "C-003", "severity": "high", "status": "observed_material_inconsistency_pending_independent_review", "title": "宣传促销绩效综述与资金表、执行率算式存在多处内部不一致", "year": 2024, "organization": "金华市福利彩票发行中心", "actual": "综述全年预算253.43、执行42.20、完成17.5%；资金表年初253.43、全年213、执行42.2、19.81%；年度目标文本17.5%（138/213）", "expected": "42.20 / 253.43 = 16.65%；42.20 / 213 = 19.81%；138 / 213 = 64.79%。应选择并注明真实预算口径，同步金额、比例、算式及指标栏", "reason": "同项目同年度公开金额不支持17.5%；138也与本项目执行资金42.2不同。资金表19.81%与213/42.2一致", "suggestion": "统一年初预算／调整后全年预算和实际执行数，修正文段、实际完成情况和全年预算完成率指标", "confidence": 0.99, "evidence": [native(27, "项目全年预算数为253.43 万元，执行数为42.20 万元", "宣传促销费用自评综述"), native(27, "完成预算的17.5%", "同段"), native(28, "253.43", "宣传促销绩效自评表年初预算列"), native(28, "213", "全年预算列及总体目标公式"), native(28, "42.2", "全年执行列"), native(28, "19.81%", "资金行执行率列"), native(28, "17. 5%（138/213）", "年度总体目标实际完成情况")]},
]

manual_review = [
    {"item_id": "AI-M-001", "document": "budget", "description": "全文件无文本层，上述数字仅AI读取实际渲染图片；没有独立人审来源认证，任何自动OCR事实仍应按服务资格／证据守卫处理", "pages": list(range(1, 22))},
    {"item_id": "AI-M-002", "document": "budget", "description": "绩效说明引用表11，当前原始附件止于表10；核查发布页其他独立绩效附件，不仅凭单附件直接确认公开缺失", "pages": [8, 21]},
    {"item_id": "AI-M-003", "document": "final", "description": "彩票市场管理实际完成情况95.17%（138/145）与资金行95.34%（138.25/145）有0.17个百分点差异；可能使用整数取整后的执行额，要求补明口径", "pages": [24, 26], "evidence": [native(26, "95. 17%（138/145）", "年度总体目标实际完成情况")]},
    {"item_id": "AI-M-004", "document": "final", "description": "政府性基金绩效自评占比是否排除某些无需自评项目、宣传促销调整后预算的真实依据，均须原始财务／审核材料确认；本报告仅确认PDF内部文字数字不一致", "pages": [16, 24, 27, 28]},
    {"item_id": "AI-M-005", "document": "both", "description": "上年度同比主数没有对应上年度原件。只验证给定现值与增减数算式，未确认历史金额或资金调整依据"},
]

result = {
    "schema": "gbc.original_material.ai_observations.v1",
    "purpose": "C/D parallel line engineering auxiliary review of actual original materials",
    "rule_set": "v3_3_portable",
    "generated_by": "AI",
    "independent_human_review": False,
    "frozen_golden": False,
    "eligible_as_scan_attestation": False,
    "eligible_as_blind_truth": False,
    "summary": {"overall_assessment": "有限区域技术复核已完成；预算观察范围内基本自洽，混合决算有三组内部数字／口径冲突，整体业务验收仍需独立材料及人审认证", "confirmed_business_pass": False, "material_issue_groups_observed": 3},
    "documents": [
        {"doc_id": "zhejiang_budget", "path": str((SOURCE / "zhejiang_budget.pdf").relative_to(ROOT)), "sha256": sha256((SOURCE / "zhejiang_budget.pdf").read_bytes()).hexdigest(), "document_type": "预算公开", "year": 2024, "organization": "金华市婺城区新狮街道社区卫生服务中心", "physical_page_count": 21, "text_mode": "all 21 pages have no native text layer", "visually_inspected_physical_pages": [1, 4, 5, 6, 7, 8, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21], "coverage": "关键总表、收入、用途分类、一般预算功能分类、基本/三公/两基金空表说明、项目总额、绩效说明；非逐格审计全部金额"},
        {"doc_id": "zhejiang_final", "path": str((SOURCE / "zhejiang_final.pdf").relative_to(ROOT)), "sha256": sha256((SOURCE / "zhejiang_final.pdf").read_bytes()).hexdigest(), "document_type": "决算公开", "year": 2024, "organization": "金华市福利彩票发行中心", "physical_page_count": 35, "text_mode": "cover scan; pages 2-35 have native text", "visually_inspected_physical_pages": [6, 7, 9, 16, 18, 19, 20, 21, 24, 26, 27, 28], "coverage": "关键收入、支出、基金、三公总数、主结构占比、零预算口径及两项绩效资金表。未完成所有绩效指标/评分、附件32-35全部审计；正文取文本只作辅助，结果仅引用已实际渲染查看页"},
    ],
    "location_convention": "All page values are 1-based physical PDF pages; printed_page is separately recorded. Bboxes are PDF point coordinates; scan bboxes are coarse observed regions, not certified cell locations.",
    "checks": checks,
    "issues": issues,
    "manual_review": manual_review,
    "next_actions": ["由独立复核人员重新查看列示页及可适用分母，形成真实签署/冻结材料", "运行原件真实OCR服务链并比较候选、raw预测、位置与物化事实；AI观察不能替代可信认证", "查询预算发布页是否另附表11绩效目标材料；保留该文件为开发原件，不纳入独立盲测"],
}

for item in checks + issues:
    for ref in item.get("evidence", []):
        if ref["bbox"] is not None:
            rect = PDFS[item["document"]][ref["physical_page"] - 1].rect
            assert 0 <= ref["bbox"][0] < ref["bbox"][2] <= rect.width + 0.01
            assert 0 <= ref["bbox"][1] < ref["bbox"][3] <= rect.height + 0.01

(OUT / "numbers.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
(OUT / "numbers.md").write_text("""# 原始材料 C/D 线有限区域技术复核

这是 AI 对实际 PDF 渲染页的观察，供技术验收与下一会话重核。不是独立人审结果，不是扫描来源认证，不是冻结真值，不能计入盲测缺陷数或业务验收通过指标。全部页码为 PDF 物理页。

预算：2024 年金华市婺城区新狮街道社区卫生服务中心，21 页均无文本层。已查看封面、4-8 页说明和 12-21 页表格；关键收支总额 2,545.92 万元，一般公共预算 346.22，事业收入 2,199.70，三者一致。用途分类 1,048.09 + 96.61 + 1,401.22 = 2,545.92；一般预算 234.46 + 111.76 = 346.22。功能分类分项合计与总数仅差 0.01 万元，未判错误。基本支出、三公、政府性基金、国有资本表均为空表且有无数据说明，避免直接判缺失。第 8 页绩效文字提到表 11，文件止于第 21 页表 10，需要核对发布页独立附件。

决算：2024 年金华市福利彩票发行中心，35 页，封面扫描，正文表格具有原生文本。视觉核对 6、7、9、16、18-21、24、26-28 页。收入支出 746.62 万元，基本 270.18、项目 476.44，两项比例 36.19% / 63.81% 一致；三公合计及接待 0.20 万元，另外两项 0，一致。发现三组可重核内部冲突：

1. 第 19-20 页财政拨款总体写年初预算 0、完成 0% 和无拨款支出；第 20-21 页同年度基金预算 975.79、决算 746.62。零分母完成率及总体口径需要修正。
2. 第 24 页自评资金 453.32、占基金项目支出 100%；第 16 页项目支出 476.44。按此表重算为 95.15%，若分母排除某些项目须明确依据。
3. 第 27 页宣传促销全年预算 253.43、执行 42.20、完成 17.5%；第 28 页全年预算 213、执行 42.2、19.81%。42.20/253.43=16.65%，42.20/213=19.81%。同页实际完成情况还写 17.5%（138/213），其算式不能支持17.5%。

彩票市场管理资金行 138.25/145=95.34% 与正文一致，第 26 页总体目标另用取整后的 138/145=95.17%，列待人工复核。未持有上年度原件，仅验证现值及给定变化量的内部算式；未穷举所有绩效指标、评分和后续附件。扫描 bbox 仅为实际 PNG 上观察的粗区域，不是单元格认证位置。详见 numbers.json 中逐条引文、页码和定位来源。

下一步需独立人员复核并记录适用口径，同时由主审完成真实 OCR 原件链路验证；本批已用于开发验收，应从盲测材料候选中排除。
""", encoding="utf-8")
print(json.dumps({"checks": len(checks), "observed_issue_groups": len(issues), "manual_review": len(manual_review), "output": str(OUT / "numbers.json")}, ensure_ascii=False))
