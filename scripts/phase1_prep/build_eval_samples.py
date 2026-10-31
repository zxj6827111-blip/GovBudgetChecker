"""阶段 1 评判标准准备 v3：桌面语料母体盘点 + 抽样生成器（只读，可复现）。

用法：
  python scripts/phase1_prep/build_eval_samples.py --source <桌面语料根目录> \
      [--uploads <uploads目录>] [--out <输出目录>] [--seed 20261030]

v3 相对 v2 的变化（逐条对应需求）：
1. 核心评判范围 = 2023/2024/2025；2022 材料单独输出 challenge_2022.csv
   （附加挑战集，全量列，不进核心样本与覆盖率）。
2. 抽样顺序反转：先随机——从每个年度的去重母体各抽 30 份（盐 "rnd"+年份，
   共 90）；再定向——从未抽中者补定向（盐值语义不变，但候选池排除已抽中者，
   保证与随机零重叠）。随机池 = 该年度全部去重材料，不按文种 unknown、
   text_ok 预先排除；抽中后若文种待定/不可读，保留原抽样记录并附状态列，
   单独计数报告，不悄悄替换。
3. 定向网格：3 年度 × (department|unit) 各 4，池 = 该年度去重母体中 lvl2
   线索匹配且未被抽中者；缺格如实记录，不跨格补位。特殊格按可靠资格：
   - 扫描候选格：text_ok=False 的探针候选，状态列标"待核验"；
   - 区级口径格：用上海市 16 区真实区名白名单匹配文件名或封面文本，
     商务区/开发区/新片区/园区/自贸区一律不算（旧 district_hint 启发式
     仅保留为线索列，不作为该格资格判据）；
   - 跨页候选格：无可靠离线判据时如实留空（格计数 0 并写明原因）。
   特殊格总数不硬凑。
4. 随机/定向两组哈希零重叠（脚本内断言）；复标从核心样本（随机+定向并集）
   按 3 年度 × 层级六格各 2（盐 "rea"+格名），格内候选不足如实记录，
   不跨格补位；复标清单状态列一律"候选（待原件资格核对）"。
5. 冻结保护：默认输出目录 outputs/phase1_prep/v3（绝不指向 v2）；--out 指向
   已存在且非空目录时直接报错退出（退出码非 0），不写任何文件；实际流程 =
   先写同级隐藏 staging 目录，全部生成 + 自校验通过后原子改名发布；
   失败则删除 staging，不留半成品。
6. 完整性凭据：发布目录内含 integrity.json——算法版本（"v3"）、种子、参数、
   脚本自身 sha256、desktop+uploads 两个源母体清单哈希（逐字段 digest）、
   每个产物的 sha256（按发布目录实际文件计算）。
7. summary.json 与产物一致：各阶段候选池大小、资格条件说明、盐值、排除/
   缺格原因、随机样本的母体定义、随机抽中里的 unknown/不可读计数；不写任何
   与产物矛盾的说明（v2 摘要"仅 2 份 ≥60 页"与实际最大 46 页矛盾的教训）。
8. 规则工作单 rule_worksheet.csv：全部注册规则 + 5 条计划规则
   （CMM-028~032，状态=待实施/待评价，说明列注明未实现），适用文种优先取
   义务清单映射（依据列=obligation-catalog），查不到再按编号前缀约定
   （依据列=code-prefix-convention），不允许空。
9. 预算核查队列 budget_check_queue.csv：kind_hint=unknown 的全部材料 +
   文件名命中预算线索的材料（status=文件名明确命中预算线索）各占一行，
   人工列（实际文种/依据页码/备注）留空。
10. 空白工作单：qualification_worksheet.csv（仅标识列+空白确认列，不含任何
    启发式线索列）、annotation_worksheet.csv、dispute_log.csv。

- 全程只读：只计算 SHA-256、页数与文本层提示，不运行检查引擎、不访问
  业务库、不产出系统判定；启发式标签仅用于抽样分层（管理侧）。
- 选样为确定性哈希排序：对每条候选把 seed、盐值、sha256 三个字段逐个送入
  SHA-256，按十六进制串升序取前 N 条；不导入 random 模块，重复运行一致。
- 所有输入路径先经规范化校验（拒绝含 ".." 的路径段、必须是已存在目录），
  防止路径穿越；输出只写入 staging → 发布目录下的 CSV/JSON。
- 页码口径：PDF 物理页码从 1 起；印刷页码另列（见 annotation_worksheet.csv）。
"""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

SEED_DEFAULT = 20261030
ALGO_VERSION = "v3"
# 核心评判范围只含这三个年度；2022 是附加挑战集，单独成文件，不进核心样本。
YEARS_CORE = ("2023", "2024", "2025")
CHALLENGE_YEAR = "2022"
RANDOM_PER_YEAR = 30
GRID_PER_CELL = 4
SPECIAL_PER_CELL = 2
REA_PER_CELL = 2
SCAN_HINT_CHARS = 60
LONG_DOC_PAGES = 60
PROBE_PAGE_CAP = 80
JOB_RE = re.compile(r"^[0-9a-f]{32}$")
YEAR_RE = re.compile(r"^20\d{2}$")
EMPTY_RE = re.compile(
    r"本表无数据|无[^。\n]{0,8}(?:政府性基金|国有资本经营)"
    r"|(?:政府性基金|国有资本经营)预算[^。\n]{0,12}(?:无|为零|安排)")
# 旧启发式：任意"XX区"字样仅作为线索列保留（不作为区级口径格资格判据）。
DISTRICT_RE = re.compile(r"[一-鿿]{1,4}区")
# 上海市 16 区真实区名白名单（区级口径格的唯一资格判据）：
# 商务区/开发区/新片区/园区/自贸区等均不在名单内，一律不算区级材料。
SHANGHAI_DISTRICTS = (
    "黄浦", "徐汇", "长宁", "静安", "普陀", "虹口", "杨浦", "闵行",
    "宝山", "嘉定", "浦东新区", "金山", "松江", "青浦", "奉贤", "崇明")
LEVELS = ("department", "unit")
REA_NOTE = "独立复标集（仅原件与空白工作单，不含系统输出）"
REA_STATUS = "候选（待原件资格核对）"
SCAN_STATUS = "待核验"
DISTRICT_STATUS = "区级口径候选"
CHALLENGE_STATUS = "附加挑战集（不进核心样本与覆盖率）"
UNKNOWN_STATUS = "文件名未命中预算/决算线索（文种待定）"
BUDGET_STATUS = "文件名明确命中预算线索"
RANDOM_POOL_DEFINITION = "该年度全部去重原件，含文种待定与低文本材料（不按文种 unknown、text_ok 预先排除）"
PAGE_NOTE = "PDF 物理页码从 1 起；印刷页码另列（annotation_worksheet.csv 分列物理页码与印刷页码）"
PLAN_RULE_NOTE = "方案 v3 阶段 2/4 计划规则，未实现"
PLAN_RULE_STATUS = "待实施/待评价"
# 计划中规则（尚未实现）：规则编号 → 注册表风格描述原文。
PLAN_RULES = (
    ("CMM-028", "固定语句改动"),
    ("CMM-029", "必备章节段落缺失或顺序错误"),
    ("CMM-030", "二选一未选多选残留"),
    ("CMM-031", "与上年真实数据不一致"),
    ("CMM-032", "与当年预算公开数据对照"),
)
SAMPLING_NOTES = [
    "抽样顺序反转：先随机（每年 30，盐 rnd+年份，池=该年度全部去重材料），"
    "再定向（特殊格→网格，池排除已抽中者，保证与随机零重叠）",
    "选样为确定性哈希排序（seed:salt:sha256 逐字段送入 SHA-256 后按十六进制升序），"
    "无随机模块，重复运行结果一致",
    "lvl2 为文件名+所属机构目录的层级线索（复排序），真值以标注 L1 为准",
    "district_hint 为旧启发式线索列（任意'XX区'字样），不作为区级口径格资格判据；"
    "区级口径格只用上海市 16 区真实区名白名单",
    "每份去重材料在核心样本中至多出现一次（随机/特殊格/网格互斥）",
]

DESKTOP_FIELDS = ["rel_path", "file", "sha256", "pages", "text_ok", "scanned_hint",
                  "empty_table_hint", "district_hint", "district_match", "long_doc",
                  "year", "kind_hint", "level_hint", "org_dir"]
DESKTOP_DUP_FIELDS = DESKTOP_FIELDS + ["duplicate_of"]
CHALLENGE_FIELDS = DESKTOP_FIELDS + ["状态"]
CORE_FIELDS = ["group", "cell", "状态", "rel_path", "file", "sha256", "pages", "year",
               "kind_hint", "lvl2", "org_dir", "empty_table_hint", "scanned_hint",
               "long_doc", "district_hint"]
REA_FIELDS = ["group", "cell", "状态", "rel_path", "file", "sha256", "pages", "year",
              "kind_hint", "lvl2", "org_dir", "empty_table_hint", "scanned_hint",
              "long_doc", "district_hint", "rea_note"]
QUEUE_FIELDS = ["rel_path", "file", "sha256", "pages", "year", "org_dir", "kind_hint",
                "状态", "实际文种", "依据页码", "备注"]
# 资格工作单：仅标识列（抽样来源标识）+ 空白确认列；kind_hint/lvl2/scanned_hint
# 等启发式线索列一律不进入本表（层级真值由人工在"层级确认"列核对）。
QUALIFICATION_FIELDS = ["rel_path", "file", "sha256", "pages", "year", "group", "cell",
                        "年度确认", "文种确认", "层级确认", "扫描页", "混合页",
                        "跨页表格", "材料不适用", "核验人", "日期", "备注"]
ANNOTATION_FIELDS = ["rule_id", "物理页码(从1起)", "印刷页码", "原文引文", "期望/实际",
                     "类别", "备注"]
DISPUTE_FIELDS = ["分歧ID", "两侧标注", "裁定人", "裁定", "理由"]
RULE_FIELDS = ["rule_id", "family", "desc", "适用文种", "依据", "状态",
               "正例实例数", "负例材料数", "达标", "说明"]
UPLOADS_FIELDS = ["source", "rel_path", "file", "sha256", "year", "job_id",
                  "org_name", "selected_org_level"]
UPLOADS_DUP_FIELDS = UPLOADS_FIELDS + ["duplicate_of"]


def safe_directory(raw_value, role):
    """输入目录规范化校验（安全要求，防止路径穿越）：
    1) 拒绝任何含 ".." 路径段的参数，确保只访问显式给出的目录树；
    2) 参数必须指向一个已存在的目录。
    本工具为只读盘点：校验通过后仅对该目录做只读遍历，不创建、不修改输入文件。
    """
    value = str(raw_value).strip()
    if not value:
        raise SystemExit(role + " 路径为空")
    candidate = Path(value)
    if ".." in candidate.parts:
        raise SystemExit(role + " 拒绝包含 '..' 的路径: " + value)
    if not candidate.is_dir():
        raise SystemExit(role + " 不是已存在的目录: " + value)
    return candidate


def safe_output_target(raw_value, role):
    """输出目标目录校验（v3 冻结保护）：
    1) 拒绝任何含 ".." 路径段的参数（防路径穿越）；
    2) 目标已存在且非空 → 直接报错退出（SystemExit 退出码非 0），
       不写任何文件，保护已发布产物（v2/v1 等历史目录绝不被触碰）；
    3) 本函数不创建、不写入目录——实际写入走同级隐藏 staging 目录，
       全部生成 + 自校验通过后原子改名发布；失败则删除 staging，不留半成品。
    """
    value = str(raw_value).strip()
    if not value:
        raise SystemExit(role + " 路径为空")
    candidate = Path(value)
    if ".." in candidate.parts:
        raise SystemExit(role + " 拒绝包含 '..' 的路径: " + value)
    if candidate.exists():
        if not candidate.is_dir():
            raise SystemExit(role + " 已存在且不是目录: " + value)
        if any(candidate.iterdir()):
            raise SystemExit(role + " 已存在且非空，拒绝覆盖（冻结保护）: " + value)
    return candidate


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def script_self_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def selection_rank(seed, salt, sha_value):
    """确定性选样排名：把 seed、盐值、候选 sha256 三个字段逐个送入 SHA-256
    （逐字段写入，避免任何拼接歧义），返回十六进制串；同池内按该串升序取前 N。
    不导入 random 模块——同一种子、同一盐、同一候选集合下结果必然一致。"""
    digest = hashlib.sha256()
    digest.update(str(seed).encode("utf-8"))
    digest.update(b":")
    digest.update(salt.encode("utf-8"))
    digest.update(b":")
    digest.update(sha_value.encode("utf-8"))
    return digest.hexdigest()


def kind_hint(name):
    if "预算" in name:
        return "budget"
    if "决算" in name:
        return "final"
    return "unknown"


def level_hint_of_name(name):
    """文件名层级线索（抽样属性，真值以标注 L1 为准）。"""
    if "部门决算" in name:
        return "department"
    if ("单位决算" in name or "本级" in name or name.endswith("单位.pdf")
            or re.search(r"单位20\d{2}年度?", name)):
        return "unit"
    return "unknown"


def refine_level(row):
    """复排序层级：文件名"部门决算/单位决算/本级"优先；否则比较文件名主干
    （去掉"20xx年度"后缀）与所属机构目录 org_dir——主干以 org_dir 为前缀但更
    长的视为下属单位（unit），主干与 org_dir 相同的视为部门本级（department）；
    都不满足时保留按文件名判断的 level_hint 兜底（同名规则）。"""
    name = row["file"]
    if "部门决算" in name:
        return "department"
    if "单位决算" in name or "本级" in name:
        return "unit"
    stem = re.split(r"20\d{2}年度?", name)[0]
    org_dir = row.get("org_dir", "")
    if org_dir and stem != org_dir and stem.startswith(org_dir):
        return "unit"
    if stem == org_dir:
        return "department"
    return level_hint_of_name(name)


def district_whitelist_match(name_text, cover_text):
    """上海市 16 区真实区名白名单匹配（区级口径格的唯一资格判据）。
    匹配对象：文件名与封面文本（第 1 页去空白）。商务区/开发区/新片区/
    园区/自贸区等不在白名单内，一律不算区级材料。返回命中的区名或空串。"""
    for district in sorted(SHANGHAI_DISTRICTS, key=len, reverse=True):
        if district in name_text or district in cover_text:
            return district
    return ""


def probe_pdf(path):
    """读取页数与文本层提示（只读）。扫描判定：前 80 页文本去空白后不足
    60 字符视为扫描件；个别坏件 MuPDF 会向 stderr 刷警告，打开失败按 0 页、
    无文本处理，不中断整体盘点。district_match 为 16 区白名单命中结果。"""
    pages = 0
    cover = ""
    chunks = []
    try:
        import pymupdf

        document = pymupdf.open(path)
        try:
            pages = len(document)
            if pages:
                cover = document[0].get_text()
            for index in range(min(pages, PROBE_PAGE_CAP)):
                chunks.append(document[index].get_text())
        finally:
            document.close()
    except Exception:  # 损坏/加密等坏件：按无文本处理，保持盘点可完成
        pages = 0
        cover = ""
        chunks = []
    joined = "".join(chunks)
    compact = re.sub(r"\s+", "", joined)
    cover_compact = re.sub(r"\s+", "", cover)
    head_chars = len(compact[:2000])
    text_ok = head_chars >= SCAN_HINT_CHARS
    return {
        "pages": pages,
        "text_ok": text_ok,
        "empty_table_hint": bool(EMPTY_RE.search(joined)),
        # 旧启发式线索：任意"XX区"字样（仅线索列，不作区级口径格判据）
        "district_hint": bool(DISTRICT_RE.search(cover_compact)),
        # 区级口径格资格判据：16 区白名单命中文件名或封面文本
        "district_match": district_whitelist_match(Path(path).name, cover_compact),
        "long_doc": pages >= LONG_DOC_PAGES,
    }


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def inventory_desktop(source_root):
    """桌面母体盘点：<根>/<年份目录>/**.pdf；稳定排序后按 SHA-256 去重，
    同哈希第一条为保留件，其余 duplicate_of 直指保留件 rel_path（无链）。"""
    rows = []
    year_dirs = sorted(
        (p for p in source_root.iterdir() if p.is_dir() and YEAR_RE.fullmatch(p.name)),
        key=lambda p: p.name)
    for year_dir in year_dirs:
        pdfs = sorted(year_dir.rglob("*.pdf"),
                      key=lambda p: str(p.relative_to(source_root)))
        for pdf in pdfs:
            relative = pdf.relative_to(source_root)
            parts = relative.parts
            probe = probe_pdf(pdf)
            rows.append({
                "rel_path": str(relative),
                "file": pdf.name,
                "sha256": sha256_of(pdf),
                "pages": probe["pages"],
                "text_ok": probe["text_ok"],
                "scanned_hint": not probe["text_ok"],
                "empty_table_hint": probe["empty_table_hint"],
                "district_hint": probe["district_hint"],
                "district_match": probe["district_match"],
                "long_doc": probe["long_doc"],
                "year": year_dir.name,
                "kind_hint": kind_hint(pdf.name),
                "level_hint": level_hint_of_name(pdf.name),
                "org_dir": parts[1] if len(parts) > 2 else "",
            })
    rows.sort(key=lambda row: (row["sha256"], row["rel_path"]))
    kept, dropped = [], []
    first_by_sha = {}
    for row in rows:
        twin = first_by_sha.get(row["sha256"])
        if twin is None:
            first_by_sha[row["sha256"]] = row
            kept.append(row)
        else:
            dropped.append(dict(row, duplicate_of=twin["rel_path"]))
    return kept, dropped


def load_org_directory(data_dir):
    """组织目录（只读）：job_org_links.json 提供 job_id -> org_id 关联；
    organizations.json 提供 org_id -> (层级, 名称)。文件缺失时按空目录处理。"""
    links, levels = {}, {}
    links_path = data_dir / "job_org_links.json"
    if links_path.is_file():
        for item in load_json(links_path).get("links", []):
            job_id = str(item.get("job_id", ""))
            org_id = str(item.get("org_id", ""))
            if job_id and org_id:
                links[job_id] = org_id
    orgs_path = data_dir / "organizations.json"
    if orgs_path.is_file():
        for item in load_json(orgs_path).get("organizations", []):
            org_id = str(item.get("id", ""))
            if org_id:
                levels[org_id] = (str(item.get("level") or ""),
                                  str(item.get("name") or ""))
    return links, levels


def inventory_uploads(uploads_root, org_links, org_levels):
    """uploads 历史子集盘点：遍历 32 位十六进制任务目录，status.json 提供
    report_year/fiscal_year 与组织回退名；同哈希组内 selected_org_level 非空者
    优先保留，被剔除件的 duplicate_of 直指最终保留件 job_id（禁止指向中间件）。"""
    rows = []
    for entry in sorted(uploads_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or not JOB_RE.fullmatch(entry.name):
            continue
        pdfs = sorted(entry.glob("*.pdf"))
        if not pdfs:
            continue
        status_path = entry / "status.json"
        status = load_json(status_path) if status_path.is_file() else {}
        level, org_name = org_levels.get(org_links.get(entry.name, ""), ("", ""))
        year = status.get("report_year") or status.get("fiscal_year")
        pdf = pdfs[0]
        rows.append({
            "source": "uploads",
            "rel_path": entry.name,
            "file": pdf.name,
            "sha256": sha256_of(pdf),
            "year": str(year) if year else "",
            "job_id": entry.name,
            "org_name": org_name or str(status.get("organization_name") or ""),
            "selected_org_level": level or "",
        })
    rows.sort(key=lambda row: (row["sha256"], row["job_id"]))
    by_sha = {}
    for row in rows:
        by_sha.setdefault(row["sha256"], []).append(row)
    kept, dropped = [], []
    for sha_value in sorted(by_sha):
        group = by_sha[sha_value]
        linked = [row for row in group if row["selected_org_level"]]
        winner = (linked or group)[0]
        kept.append(winner)
        for row in group:
            if row is not winner:
                dropped.append(dict(row, duplicate_of=winner["job_id"]))
    kept.sort(key=lambda row: (row["sha256"], row["job_id"]))
    return kept, dropped


def sample_status_flags(row):
    """随机/定向网格抽中记录的状态列取值：文种待定与不可读（低文本）如实
    标注（两者都有则并列），正常的记录留空。不悄悄替换、不剔除。"""
    parts = []
    if row["kind_hint"] == "unknown":
        parts.append("文种待定")
    if not row["text_ok"]:
        parts.append("不可读（低文本）")
    return "；".join(parts)


def sample_v3(kept, seed):
    """v3 抽样：随机（每年 30，池=该年度全部去重材料）→ 定向特殊格（按可靠
    资格，总数不硬凑）→ 定向网格（3 年度 × 层级各 4，池=未被抽中者）→
    六格复标（从核心样本并集各格取 2）。全流程确定性：每格按 selection_rank
    升序取前 N，缺格如实记录，不跨格补位；selected 映射保证每份材料至多
    出现一次（随机/定向零重叠在此结构上成立，另有断言复核）。"""
    selected = {}  # sha256 -> (group, cell)
    status_by_sha = {}  # sha256 -> 状态列文字

    def rank_sorted(pool, salt):
        return sorted(pool, key=lambda row: selection_rank(seed, salt, row["sha256"]))

    core_pool = [row for row in kept if row["year"] in YEARS_CORE]

    # ---- 第 1 步：随机样本（先随机）。池 = 该年度全部去重材料，不按文种
    # unknown、text_ok 预先排除；抽中后文种待定/不可读者保留并附状态列。
    random_rows, random_pools = [], {}
    for year in YEARS_CORE:
        pool = [row for row in core_pool if row["year"] == year]
        random_pools[year] = len(pool)
        picked = rank_sorted(pool, "rnd" + year)[:RANDOM_PER_YEAR]
        for row in picked:
            selected[row["sha256"]] = ("随机", year)
            status_by_sha[row["sha256"]] = sample_status_flags(row)
        random_rows.extend(picked)

    # ---- 第 2 步：定向特殊格（按可靠资格，不硬凑）。
    # 跨页候选格：跨页表格的可靠判据需要表格线/版面分析，离线探针给不出，
    # 按需求如实留空（候选池 0、抽中 0），原因写入 summary。
    special_defs = (
        ("扫描候选", lambda row: not row["text_ok"], "sp扫描候选", SCAN_STATUS,
         "text_ok=False 的探针候选（扫描件风险），状态列标'待核验'"),
        ("区级口径", lambda row: bool(row.get("district_match")), "sp区级口径",
         DISTRICT_STATUS,
         "上海市 16 区真实区名白名单命中文件名或封面文本；商务区/开发区/新片区/园区/自贸区一律不算"),
        ("跨页候选", lambda row: False, "sp跨页候选", "跨页候选",
         "跨页表格无可靠离线判据，格留空（候选池 0、抽中 0），不硬凑"),
    )
    special_rows, special_report = [], {}
    for cell, predicate, salt, status, basis in special_defs:
        pool = [row for row in core_pool
                if predicate(row) and row["sha256"] not in selected]
        picked = rank_sorted(pool, salt)[:SPECIAL_PER_CELL]
        for row in picked:
            selected[row["sha256"]] = ("定向", cell)
            status_by_sha[row["sha256"]] = status
        special_rows.extend(picked)
        special_report[cell] = {
            "候选池": len(pool),
            "抽中": len(picked),
            "盐": salt,
            "资格条件": basis,
            "状态列": status,
        }

    # ---- 第 3 步：定向网格。池 = 该年度去重母体中 lvl2 线索匹配且未被
    # （随机与特殊格）抽中者——排除所有已抽中者是"每份材料至多出现一次"
    # 的结构保证，比需求文字的"未被随机抽中"更强，如实写进资格条件。
    grid_rows, grid_report = [], {}
    for year in YEARS_CORE:
        for level in LEVELS:
            cell = year + "|" + level
            pool = [row for row in core_pool
                    if row["year"] == year and row["lvl2"] == level
                    and row["sha256"] not in selected]
            picked = rank_sorted(pool, "grid" + cell)[:GRID_PER_CELL]
            for row in picked:
                selected[row["sha256"]] = ("定向", cell)
                status_by_sha[row["sha256"]] = sample_status_flags(row)
            grid_rows.extend(picked)
            grid_report[cell] = {
                "候选池": len(pool),
                "抽中": len(picked),
                "盐": "grid" + cell,
                "资格条件": "该年度去重母体中 lvl2 线索匹配且未被随机与特殊格抽中（每份材料至多出现一次）",
            }

    # ---- 第 4 步：六格复标。候选 = 核心样本（随机+定向并集）内按年度 ×
    # lvl2 分格；格内候选不足 2 时如实记录，不跨格补位。
    core_rows = random_rows + special_rows + grid_rows
    rea_rows, rea_report = [], {}
    for year in YEARS_CORE:
        for level in LEVELS:
            cell = year + "|" + level
            pool = [row for row in core_rows
                    if row["year"] == year and row["lvl2"] == level]
            picked = rank_sorted(pool, "rea" + cell)[:REA_PER_CELL]
            rea_report[cell] = {"候选": len(pool), "抽中": len(picked),
                                "盐": "rea" + cell}
            rea_rows.extend(picked)

    return {
        "selected": selected,
        "status_by_sha": status_by_sha,
        "random_rows": random_rows,
        "random_pools": random_pools,
        "special_rows": special_rows,
        "special_report": special_report,
        "grid_rows": grid_rows,
        "grid_report": grid_report,
        "core_rows": core_rows,
        "rea_rows": rea_rows,
        "rea_report": rea_report,
    }


def manifest_sha256(rows):
    """源母体清单指纹：对每行按 rel_path、制表符、sha256、换行四个字段依次
    送入 SHA-256（逐字段写入，避免任何拼接歧义）。desktop 与 uploads 各算一个。"""
    digest = hashlib.sha256()
    for row in rows:
        digest.update(str(row.get("rel_path", "")).encode("utf-8"))
        digest.update(b"\t")
        digest.update(str(row.get("sha256", "")).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def rule_worksheet_rows(repo_root):
    """规则工作单 v3：全部注册规则（rule_registry.rule_catalog，含 desc 注册表
    原文、适用文种、依据、状态=已实现）+ 5 条计划规则（CMM-028~032）。
    适用文种优先取义务清单（check_obligations.OBLIGATION_CATALOG）的
    checker→文种映射（依据列=obligation-catalog）；查不到再按编号前缀约定
    （依据列=code-prefix-convention：BUD-*=预算、CMM-*=通用、V33-*=决算为主），
    不允许空。正例/负例计数留空待标注，达标列固定"待标注"。
    规则注册表会间接引用仓库根下的 api 包，因此把仓库根与 src/ 都加入
    sys.path（只加路径，不改包）。"""
    root_path = str(repo_root)
    if root_path not in sys.path:
        sys.path.insert(0, root_path)
    src_path = str(repo_root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    from engine.check_obligations import OBLIGATION_CATALOG
    from engine.rule_registry import rule_catalog
    from engine import budget_rules, common_rules, rules_v33

    # 义务清单的 checker→适用文种映射（同一规则可能被多个义务、多个文种引用）。
    kinds_by_checker = {}
    for obligation in OBLIGATION_CATALOG:
        for kind, codes in obligation.checkers_by_kind.items():
            for code in codes:
                kinds_by_checker.setdefault(code, set()).add(kind)

    # desc 注册表原文：从注册的生产规则类上读取（与 rule_catalog 同一批清单）。
    desc_by_id = {}
    for rule in (*budget_rules.ALL_BUDGET_RULES, *rules_v33.ALL_RULES,
                 *common_rules.ALL_COMMON_RULES):
        code = str(getattr(rule, "code", ""))
        desc_by_id[code] = str(getattr(rule, "desc", "") or "")

    catalog = rule_catalog()
    rows = []
    basis_tally = {"obligation-catalog": 0, "code-prefix-convention": 0}
    for item in catalog.get("rules", []):
        rule_id = str(item.get("rule_id", ""))
        family = rule_id.split("-", 1)[0] if "-" in rule_id else ""
        kinds = kinds_by_checker.get(rule_id, set())
        if kinds:
            basis = "obligation-catalog"
            if kinds == {"budget"}:
                applicability = "预算"
            elif kinds == {"final"}:
                applicability = "决算"
            else:
                applicability = "通用（预算+决算）"
        else:
            basis = "code-prefix-convention"
            applicability = {"BUD": "预算", "CMM": "通用（预算+决算）",
                             "V33": "决算为主"}.get(family, "待定")
        if not applicability.strip():
            raise SystemExit("规则工作单出现空适用文种: " + rule_id)
        basis_tally[basis] += 1
        rows.append({
            "rule_id": rule_id,
            "family": family,
            "desc": desc_by_id.get(rule_id, ""),
            "适用文种": applicability,
            "依据": basis,
            "状态": "已实现",
            "正例实例数": "",
            "负例材料数": "",
            "达标": "待标注",
            "说明": "",
        })
    for rule_id, plan_desc in PLAN_RULES:
        rows.append({
            "rule_id": rule_id,
            "family": "CMM",
            "desc": plan_desc,
            "适用文种": "通用（预算+决算）",
            "依据": "code-prefix-convention",
            "状态": PLAN_RULE_STATUS,
            "正例实例数": "",
            "负例材料数": "",
            "达标": "待标注",
            "说明": PLAN_RULE_NOTE,
        })
    return rows, basis_tally


def count_by(rows, key):
    tally = {}
    for row in rows:
        value = row[key]
        tally[value] = tally.get(value, 0) + 1
    return tally


def write_csv(path, fields, rows):
    """CSV 统一 utf-8-sig、csv.DictWriter、newline=""，便于 Excel 直接打开。"""
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def read_csv_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def self_check(staging, registered_total, had_source):
    """发布前自校验：读回 staging 产物逐项断言，全部通过才允许发布。
    覆盖 v3 需求的断言：随机∩定向=∅、复标⊆核心样本、核心样本无 2022、
    challenge_2022=2022 全量、随机池=各年度去重母体大小、随机抽中 unknown/
    不可读计数如实、规则工作单行数与计划规则状态、预算核查队列规模、
    summary 与产物一致。任一失败抛 SystemExit（退出码非 0），staging 删除。"""

    def fail(message):
        raise SystemExit("自校验失败: " + message)

    rules = read_csv_rows(staging / "rule_worksheet.csv")
    with open(staging / "summary.json", encoding="utf-8") as fh:
        summary = json.load(fh)

    # ---- 规则工作单：行数、空适用文种、计划规则状态（任何模式都必查）----
    expected_rules = registered_total + len(PLAN_RULES)
    if len(rules) != expected_rules:
        fail("规则工作单行数 %d ≠ %d" % (len(rules), expected_rules))
    for row in rules:
        if not row["适用文种"].strip():
            fail("规则工作单存在空适用文种: " + row["rule_id"])
    planned = [row for row in rules if row["状态"] != "已实现"]
    if ([row["rule_id"] for row in planned]
            != [rule_id for rule_id, _ in PLAN_RULES]):
        fail("计划规则编号或顺序不一致")
    if any(row["状态"] != PLAN_RULE_STATUS for row in planned):
        fail("计划规则状态列不正确")
    if any(row["说明"] != PLAN_RULE_NOTE for row in planned):
        fail("计划规则说明列不正确")
    if summary.get("规则工作单", {}).get("合计行数") != len(rules):
        fail("summary 规则工作单行数与产物不一致")

    tally = {
        "core": 0, "random": 0, "directed": 0, "rea": 0,
        "rules": len(rules), "queue": 0,
        "unknown_in_random": 0, "lowtext_in_random": 0,
    }
    if not had_source:
        return tally

    inventory = read_csv_rows(staging / "desktop_corpus_inventory.csv")
    duplicates = read_csv_rows(staging / "desktop_duplicates.csv")
    challenge = read_csv_rows(staging / "challenge_2022.csv")
    core = read_csv_rows(staging / "desktop_core_sample.csv")
    rea = read_csv_rows(staging / "desktop_reannotation_12.csv")
    queue = read_csv_rows(staging / "budget_check_queue.csv")

    # ---- 集合关系断言 ----
    random_rows = [row for row in core if row["group"] == "随机"]
    directed_rows = [row for row in core if row["group"] == "定向"]
    random_shas = {row["sha256"] for row in random_rows}
    directed_shas = {row["sha256"] for row in directed_rows}
    if random_shas & directed_shas:
        fail("随机与定向两组出现重叠: " + str(sorted(random_shas & directed_shas)))
    core_shas = {row["sha256"] for row in core}
    rea_shas = {row["sha256"] for row in rea}
    if not rea_shas <= core_shas:
        fail("复标样本超出核心样本并集")
    if len(rea_shas) != len(rea):
        fail("复标样本内部重复")
    for row in core:
        if row["year"] not in YEARS_CORE:
            fail("核心样本混入非核心年度: " + row["rel_path"])
    for row in rea:
        if row["year"] not in YEARS_CORE:
            fail("复标样本混入非核心年度: " + row["rel_path"])

    # ---- challenge_2022 = 2022 全量去重材料，且核心/复标不含 2022 ----
    challenge_shas = {row["sha256"] for row in challenge}
    expected_2022 = {row["sha256"] for row in inventory if row["year"] == CHALLENGE_YEAR}
    if challenge_shas != expected_2022:
        fail("challenge_2022 与 2022 年去重母体不一致")
    if any(row["year"] == CHALLENGE_YEAR for row in core + rea):
        fail("2022 材料出现在核心样本或复标清单")

    # ---- 随机池 = 各年度去重母体大小；每年抽中 30（池不足时如实取池大小）----
    year_counts = count_by(inventory, "year")
    random_report = summary.get("随机样本", {})
    for year in YEARS_CORE:
        picked = [row for row in random_rows if row["year"] == year]
        expected = min(RANDOM_PER_YEAR, year_counts.get(year, 0))
        if len(picked) != expected:
            fail("随机样本年度 %s 数量 %d ≠ 期望 %d" % (year, len(picked), expected))
        if random_report.get(year, {}).get("候选池") != year_counts.get(year, 0):
            fail("随机样本年度 %s 候选池与去重母体大小不一致" % year)

    # ---- 随机抽中 unknown/不可读计数如实 ----
    flag_tally = count_by(random_rows, "状态") if random_rows else {}
    if summary.get("随机抽中状态计数") != flag_tally:
        fail("随机抽中状态计数与核心样本不一致")
    unknown_in_random = sum(1 for row in random_rows if row["kind_hint"] == "unknown")
    lowtext_in_random = sum(1 for row in random_rows if row["scanned_hint"] == "True")
    if unknown_in_random != sum(n for key, n in flag_tally.items() if "文种待定" in key):
        fail("随机抽中文种待定计数不一致")
    if lowtext_in_random != sum(n for key, n in flag_tally.items() if "不可读" in key):
        fail("随机抽中不可读计数不一致")

    # ---- 定向网格与特殊格数量一致 ----
    grid_report = summary.get("定向网格", {})
    for cell, info in grid_report.items():
        actual = sum(1 for row in directed_rows if row["cell"] == cell)
        if actual != info["抽中"]:
            fail("定向网格格 %s 抽中数与核心样本不一致" % cell)
    special_report = summary.get("定向特殊格", {})
    for cell, info in special_report.items():
        actual = sum(1 for row in directed_rows if row["cell"] == cell)
        if actual != info["抽中"]:
            fail("定向特殊格 %s 抽中数与核心样本不一致" % cell)

    # ---- 复标六格：每格 ≤2、状态列统一、候选数如实 ----
    rea_report = summary.get("复标六格", {})
    for cell, info in rea_report.items():
        picked = [row for row in rea if row["cell"] == cell]
        if len(picked) != info["抽中"] or len(picked) > REA_PER_CELL:
            fail("复标格 %s 抽中数与清单不一致" % cell)
        if info["抽中"] > info["候选"]:
            fail("复标格 %s 抽中数超过候选数" % cell)
    if any(row["状态"] != REA_STATUS for row in rea):
        fail("复标清单状态列不统一")

    # ---- 预算核查队列：unknown 全量 + 预算线索行 ----
    unknown_count = sum(1 for row in inventory if row["kind_hint"] == "unknown")
    budget_count = sum(1 for row in inventory if row["kind_hint"] == "budget")
    if len(queue) != unknown_count + budget_count:
        fail("预算核查队列行数与 unknown/budget 材料数不一致")
    queue_budget = [row for row in queue if row["kind_hint"] == "budget"]
    if len(queue_budget) != budget_count:
        fail("预算核查队列缺少预算线索行")
    if any(row["状态"] != BUDGET_STATUS for row in queue_budget):
        fail("预算线索行状态列不正确")
    if any(row["实际文种"] or row["依据页码"] or row["备注"] for row in queue):
        fail("预算核查队列人工列应留空")

    # ---- summary 与产物总量一致 ----
    if summary.get("文件总数") != len(inventory) + len(duplicates):
        fail("summary 文件总数与产物不一致")
    if summary.get("去重后") != len(inventory):
        fail("summary 去重后数量与产物不一致")
    if summary.get("核心样本", {}).get("合计") != len(core):
        fail("summary 核心样本合计与产物不一致")
    if summary.get("复标合计") != len(rea):
        fail("summary 复标合计与产物不一致")
    if summary.get("budget_check_queue", {}).get("合计") != len(queue):
        fail("summary 预算核查队列合计与产物不一致")

    # ---- 资格工作单：行数与核心样本一致、确认列空白、无启发式线索列 ----
    qualification = read_csv_rows(staging / "qualification_worksheet.csv")
    if len(qualification) != len(core):
        fail("资格工作单行数与核心样本不一致")
    if any(row["年度确认"] or row["文种确认"] or row["层级确认"] or row["扫描页"]
           or row["混合页"] or row["跨页表格"] or row["材料不适用"]
           or row["核验人"] or row["日期"] or row["备注"] for row in qualification):
        fail("资格工作单确认列应为空白")
    for clue in ("kind_hint", "lvl2", "scanned_hint", "empty_table_hint",
                 "district_hint", "district_match", "状态"):
        if clue in qualification[0]:
            fail("资格工作单不得包含启发式线索列: " + clue)

    tally.update({
        "core": len(core),
        "random": len(random_rows),
        "directed": len(directed_rows),
        "rea": len(rea),
        "queue": len(queue),
        "unknown_in_random": unknown_in_random,
        "lowtext_in_random": lowtext_in_random,
    })
    return tally


def write_integrity(staging, summary, desktop_manifest, uploads_manifest):
    """完整性凭据：对发布目录（staging）内除本文件外的全部产物按实际文件
    计算 sha256 与字节数；输出目录路径不写入凭据（保证可复现字节一致）。"""
    products = {}
    for entry in sorted(staging.iterdir(), key=lambda p: p.name):
        if entry.is_file() and entry.name != "integrity.json":
            products[entry.name] = {
                "sha256": sha256_of(entry),
                "bytes": entry.stat().st_size,
            }
    payload = {
        "algorithm_version": ALGO_VERSION,
        "seed": summary.get("seed"),
        "args": summary.get("args"),
        "script_sha256": summary.get("脚本自身sha256"),
        "source_manifest_sha256": {"desktop": desktop_manifest,
                                   "uploads": uploads_manifest},
        "products": products,
        "note": "products 为发布目录内除本文件外的全部产物，按实际文件字节计算；"
                "输出目录路径不写入本凭据，保证两次独立运行字节级一致",
    }
    with open(staging / "integrity.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return payload


def verify_integrity(staging, payload):
    """发布前复核完整性凭据：逐个产物重算 sha256，并确认没有漏登记的产物。"""
    for name, info in payload["products"].items():
        if sha256_of(staging / name) != info["sha256"]:
            raise SystemExit("完整性自校验失败: " + name)
    actual_files = sorted(entry.name for entry in staging.iterdir()
                          if entry.is_file() and entry.name != "integrity.json")
    if actual_files != sorted(payload["products"]):
        raise SystemExit("完整性自校验失败: 产物清单与实际文件不一致")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="阶段1评判标准准备 v3：盘点桌面语料母体与 uploads 历史子集，"
                    "生成核心样本/2022挑战集/复标/规则工作单/预算核查队列/空白"
                    "工作单（只读输入，只写 CSV/JSON，先 staging 后原子发布）")
    parser.add_argument("--source", default=None,
                        help="桌面语料根目录（可选；按年份子目录组织）")
    parser.add_argument("--uploads", default=None,
                        help="uploads 历史任务目录（默认仓库 uploads/）")
    parser.add_argument("--out", default=None,
                        help="输出目录（默认仓库 outputs/phase1_prep/v3/；"
                             "已存在且非空时拒绝）")
    parser.add_argument("--seed", type=int, default=SEED_DEFAULT,
                        help="选样种子（默认 20261030）")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    uploads_arg = args.uploads if args.uploads else str(repo_root / "uploads")
    uploads_root = safe_directory(uploads_arg, "--uploads")
    out_arg = args.out if args.out else str(repo_root / "outputs" / "phase1_prep" / "v3")
    out_root = safe_output_target(out_arg, "--out")
    out_root.parent.mkdir(parents=True, exist_ok=True)
    # staging 为发布目录的同级隐藏目录；只由本脚本创建与清理，若上次运行
    # 中断留下残留，先整体删除再重建，保证发布内容全新。
    staging = out_root.parent / (".staging_" + out_root.name)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        summary = {
            "algorithm_version": ALGO_VERSION,
            "seed": args.seed,
            # 输出目录路径不写入 summary/integrity（两次独立运行须字节一致）。
            "args": {"source": "", "uploads": str(uploads_root)},
        }
        had_source = bool(args.source)
        desktop_manifest = ""
        if args.source:
            source_root = safe_directory(args.source, "--source")
            summary["args"]["source"] = str(source_root)
            kept, dropped = inventory_desktop(source_root)
            for row in kept:
                row["lvl2"] = refine_level(row)
            result = sample_v3(kept, args.seed)
            write_csv(staging / "desktop_corpus_inventory.csv", DESKTOP_FIELDS, kept)
            write_csv(staging / "desktop_duplicates.csv", DESKTOP_DUP_FIELDS, dropped)
            # 2022 附加挑战集：全量列，不进核心样本与覆盖率。
            challenge_rows = []
            for row in kept:
                if row["year"] == CHALLENGE_YEAR:
                    item = dict(row)
                    item["状态"] = CHALLENGE_STATUS
                    challenge_rows.append(item)
            write_csv(staging / "challenge_2022.csv", CHALLENGE_FIELDS, challenge_rows)
            # 核心样本（随机+定向），附状态列。
            core_out = []
            for row in result["core_rows"]:
                group, cell = result["selected"][row["sha256"]]
                core_out.append({
                    "group": group, "cell": cell,
                    "状态": result["status_by_sha"].get(row["sha256"], ""),
                    "rel_path": row["rel_path"], "file": row["file"],
                    "sha256": row["sha256"], "pages": row["pages"],
                    "year": row["year"], "kind_hint": row["kind_hint"],
                    "lvl2": row["lvl2"], "org_dir": row["org_dir"],
                    "empty_table_hint": row["empty_table_hint"],
                    "scanned_hint": row["scanned_hint"],
                    "long_doc": row["long_doc"],
                    "district_hint": row["district_hint"],
                })
            core_out.sort(key=lambda item: (item["group"], item["cell"], item["sha256"]))
            write_csv(staging / "desktop_core_sample.csv", CORE_FIELDS, core_out)
            # 复标清单：状态列一律"候选（待原件资格核对）"；cell 为复标分格
            # （年度|lvl2），与 summary 的复标六格一一对应。
            rea_out = []
            for row in result["rea_rows"]:
                rea_out.append({
                    "group": "", "cell": row["year"] + "|" + row["lvl2"],
                    "状态": REA_STATUS,
                    "rel_path": row["rel_path"], "file": row["file"],
                    "sha256": row["sha256"], "pages": row["pages"],
                    "year": row["year"], "kind_hint": row["kind_hint"],
                    "lvl2": row["lvl2"], "org_dir": row["org_dir"],
                    "empty_table_hint": row["empty_table_hint"],
                    "scanned_hint": row["scanned_hint"],
                    "long_doc": row["long_doc"],
                    "district_hint": row["district_hint"],
                    "rea_note": REA_NOTE,
                })
            rea_out.sort(key=lambda item: (item["cell"], item["sha256"]))
            write_csv(staging / "desktop_reannotation_12.csv", REA_FIELDS, rea_out)
            # 预算核查队列：unknown 全量 + 预算线索行（人工列留空）。
            queue_budget = sorted(
                (row for row in kept if row["kind_hint"] == "budget"),
                key=lambda row: (row["sha256"], row["rel_path"]))
            queue_unknown = sorted(
                (row for row in kept if row["kind_hint"] == "unknown"),
                key=lambda row: (row["sha256"], row["rel_path"]))
            queue_out = []
            for row in queue_budget:
                queue_out.append({
                    "rel_path": row["rel_path"], "file": row["file"],
                    "sha256": row["sha256"], "pages": row["pages"],
                    "year": row["year"], "org_dir": row["org_dir"],
                    "kind_hint": row["kind_hint"], "状态": BUDGET_STATUS,
                    "实际文种": "", "依据页码": "", "备注": ""})
            for row in queue_unknown:
                queue_out.append({
                    "rel_path": row["rel_path"], "file": row["file"],
                    "sha256": row["sha256"], "pages": row["pages"],
                    "year": row["year"], "org_dir": row["org_dir"],
                    "kind_hint": row["kind_hint"], "状态": UNKNOWN_STATUS,
                    "实际文种": "", "依据页码": "", "备注": ""})
            write_csv(staging / "budget_check_queue.csv", QUEUE_FIELDS, queue_out)
            # 资格工作单：仅标识列+空白确认列，不含任何启发式线索列
            #（group/cell 仅为抽样分层标识；层级真值由人工在"层级确认"核对）。
            qualification_out = []
            for row in core_out:
                qualification_out.append({
                    "rel_path": row["rel_path"], "file": row["file"],
                    "sha256": row["sha256"], "pages": row["pages"],
                    "year": row["year"], "group": row["group"],
                    "cell": row["cell"],
                    "年度确认": "", "文种确认": "", "层级确认": "", "扫描页": "",
                    "混合页": "", "跨页表格": "", "材料不适用": "", "核验人": "",
                    "日期": "", "备注": ""})
            write_csv(staging / "qualification_worksheet.csv",
                      QUALIFICATION_FIELDS, qualification_out)

            random_status_tally = count_by(
                [{"状态": result["status_by_sha"].get(row["sha256"], "")}
                 for row in result["random_rows"]], "状态")
            max_pages = max((row["pages"] for row in kept), default=0)
            shortfalls = {}
            for cell, info in result["grid_report"].items():
                if info["抽中"] < GRID_PER_CELL:
                    shortfalls["网格 " + cell] = ("候选池仅 %d，不足每格 %d，不跨格补位"
                                                 % (info["候选池"], GRID_PER_CELL))
            for cell, info in result["special_report"].items():
                if cell == "跨页候选":
                    continue  # 跨页候选格按设计留空，原因已在资格条件中说明
                if info["抽中"] < SPECIAL_PER_CELL:
                    shortfalls["特殊格 " + cell] = ("候选池仅 %d，不足每格 %d，不跨格补位"
                                                   % (info["候选池"], SPECIAL_PER_CELL))
            for cell, info in result["rea_report"].items():
                if info["抽中"] < REA_PER_CELL:
                    shortfalls["复标 " + cell] = ("核心样本中该格候选仅 %d，不足 %d，不跨格补位"
                                                 % (info["候选"], REA_PER_CELL))

            summary["source_root"] = str(source_root)
            desktop_manifest = manifest_sha256(kept)
            summary["source_manifest_sha256"] = desktop_manifest
            summary["文件总数"] = len(kept) + len(dropped)
            summary["去重后"] = len(kept)
            summary["重复剔除"] = len(dropped)
            summary["kind_hint"] = count_by(kept, "kind_hint")
            summary["level_hint"] = count_by(kept, "level_hint")
            summary["年份"] = count_by(kept, "year")
            summary["scanned_hint"] = sum(1 for row in kept if row["scanned_hint"])
            summary["text_ok"] = sum(1 for row in kept if row["text_ok"])
            summary["lvl2"] = count_by(kept, "lvl2")
            summary["全母体最大页数"] = max_pages
            summary["抽样顺序"] = "先随机后定向（随机→特殊格→网格→复标），随机/定向哈希零重叠有断言"
            summary["随机样本"] = {
                year: {"候选池": result["random_pools"][year],
                       "抽中": sum(1 for row in result["random_rows"] if row["year"] == year),
                       "盐": "rnd" + year,
                       "母体定义": RANDOM_POOL_DEFINITION}
                for year in YEARS_CORE}
            summary["随机抽中状态计数"] = random_status_tally
            summary["定向特殊格"] = result["special_report"]
            summary["定向网格"] = result["grid_report"]
            summary["缺格说明"] = shortfalls
            summary["核心样本"] = {
                "合计": len(core_out),
                "随机": sum(1 for row in core_out if row["group"] == "随机"),
                "定向": sum(1 for row in core_out if row["group"] == "定向"),
            }
            summary["复标六格"] = result["rea_report"]
            summary["复标合计"] = len(rea_out)
            summary["复标状态列"] = REA_STATUS
            summary["challenge_2022"] = {
                "份数": len(challenge_rows),
                "定位": "附加挑战集（不进核心样本与覆盖率）",
                "母体": CHALLENGE_YEAR + " 年度全部去重原件，全量列",
            }
            summary["budget_check_queue"] = {
                "文种待定（unknown）材料数": len(queue_unknown),
                "文件名命中预算线索": len(queue_budget),
                "合计": len(queue_out),
                "说明": "未经核查前只能说文件名命中预算线索 %d 份" % len(queue_budget),
                "人工列": "实际文种/依据页码/备注留空待人工填写",
            }
            summary["qualification_worksheet"] = {
                "行数": len(qualification_out),
                "确认列": ["年度确认", "文种确认", "层级确认", "扫描页", "混合页",
                           "跨页表格", "材料不适用", "核验人", "日期", "备注"],
                "说明": "仅标识列+空白确认列，不含任何启发式线索列；"
                        "group/cell 仅为抽样分层标识（管理侧），"
                        "层级真值以人工'层级确认'为准",
            }
            summary["annotation_worksheet"] = {
                "类别取值": "问题|干净面|不适用|待复核",
                "说明": "空白标注工作单模板，由标注人逐行填写",
            }
            summary["dispute_log"] = {"说明": "分歧裁定模板，空白待填"}
            summary["页码口径"] = PAGE_NOTE
            print("桌面母体: 文件 " + str(len(kept) + len(dropped))
                  + "，去重后 " + str(len(kept))
                  + "，重复剔除 " + str(len(dropped)))
            print("随机 " + str(len(result["random_rows"]))
                  + "（文种待定 " + str(summary["随机抽中状态计数"].get("文种待定", 0)
                                     + summary["随机抽中状态计数"].get("文种待定；不可读（低文本）", 0))
                  + "，低文本 " + str(summary["随机抽中状态计数"].get("不可读（低文本）", 0)
                                   + summary["随机抽中状态计数"].get("文种待定；不可读（低文本）", 0))
                  + "），定向 " + str(len(result["special_rows"]) + len(result["grid_rows"]))
                  + "，核心 " + str(len(core_out))
                  + "，复标 " + str(len(rea_out))
                  + "，挑战集2022 " + str(len(challenge_rows)))

        # ---- 空白标注/分歧工作单（无论是否有 --source 都随发布目录提供）----
        write_csv(staging / "annotation_worksheet.csv", ANNOTATION_FIELDS, [])
        write_csv(staging / "dispute_log.csv", DISPUTE_FIELDS, [])

        # ---- 规则工作单 v3（117 注册 + 5 计划）；注册表导入失败即整体失败，
        # 不发布残缺工作单。----
        rule_rows, basis_tally = rule_worksheet_rows(repo_root)
        write_csv(staging / "rule_worksheet.csv", RULE_FIELDS, rule_rows)
        summary["规则工作单"] = {
            "注册规则数": len(rule_rows) - len(PLAN_RULES),
            "计划规则数": len(PLAN_RULES),
            "计划规则编号": [rule_id for rule_id, _ in PLAN_RULES],
            "计划规则状态": PLAN_RULE_STATUS,
            "合计行数": len(rule_rows),
            "适用文种依据统计": basis_tally,
            "适用文种空值数": 0,
            "页码口径": PAGE_NOTE,
        }
        print("规则工作单: " + str(len(rule_rows)) + " 条（注册 "
              + str(len(rule_rows) - len(PLAN_RULES)) + " + 计划 "
              + str(len(PLAN_RULES)) + "）")

        # ---- uploads 历史子集（与 v2 同口径）----
        org_links, org_levels = load_org_directory(repo_root / "data")
        uploads_kept, uploads_dropped = inventory_uploads(uploads_root, org_links, org_levels)
        sha_by_job = {row["job_id"]: row["sha256"] for row in uploads_kept}
        dangling = 0
        for row in uploads_dropped:
            target = row.get("duplicate_of", "")
            if target not in sha_by_job or sha_by_job[target] != row["sha256"]:
                dangling += 1
        write_csv(staging / "uploads_historical_inventory.csv", UPLOADS_FIELDS, uploads_kept)
        write_csv(staging / "uploads_dedupe_dropped_fixed.csv", UPLOADS_DUP_FIELDS,
                  uploads_dropped)
        uploads_manifest = manifest_sha256(uploads_kept)
        summary["uploads"] = {
            "任务总数": len(uploads_kept) + len(uploads_dropped),
            "去重后": len(uploads_kept),
            "重复剔除": len(uploads_dropped),
            "悬空引用条数": dangling,
            "source_manifest_sha256": uploads_manifest,
        }
        print("uploads: 任务 " + str(len(uploads_kept) + len(uploads_dropped))
              + "，去重后 " + str(len(uploads_kept))
              + "，重复剔除 " + str(len(uploads_dropped))
              + "，悬空引用 " + str(dangling))

        summary["说明"] = SAMPLING_NOTES
        summary["脚本自身sha256"] = script_self_sha256(Path(__file__).resolve())
        with open(staging / "summary.json", "w", encoding="utf-8", newline="\n") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=1)
            fh.write("\n")

        # ---- 发布前自校验：读回产物断言，失败即删除 staging 退出 ----
        tally = self_check(staging, len(rule_rows) - len(PLAN_RULES), had_source)
        print("自校验: 通过（核心 " + str(tally["core"])
              + "，随机∩定向=∅，复标 " + str(tally["rea"])
              + "，规则工作单 " + str(tally["rules"])
              + "，核查队列 " + str(tally["queue"]) + "）")
        # ---- 完整性凭据 + 逐产物复核，全部通过后原子改名发布 ----
        payload = write_integrity(staging, summary, desktop_manifest, uploads_manifest)
        verify_integrity(staging, payload)
        if out_root.is_dir():
            out_root.rmdir()  # safe_output_target 已拒绝非空目录，此处只会是空目录
        os.replace(staging, out_root)
        print("发布目录: " + str(out_root)
              + "（产物 " + str(len(payload["products"]) + 1) + " 个，含 integrity.json）")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


if __name__ == "__main__":
    main()
