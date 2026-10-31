"""阶段 1 评判标准准备 v2：桌面语料母体盘点 + 抽样生成器（只读，可复现）。

用法：
  python scripts/phase1_prep/build_eval_samples.py --source <桌面语料根目录> \
      [--uploads <uploads目录>] [--out <输出目录>] [--seed 20261030]

- 母体 = 桌面语料根目录（按年份子目录组织的原件 PDF）；uploads 仅作为
  "历史任务子集"单独盘点并修正 duplicate_of 指向。
- 全程只读：只计算 SHA-256、页数与文本层提示，不运行检查引擎、不访问
  业务库、不产出系统判定；启发式标签仅用于抽样分层（管理侧）。
- 选样为确定性哈希排序：对每条候选计算 sha256("seed:salt:sha256") 后按
  十六进制升序取前 N 条；不导入 random 模块，重复运行结果一致。
- 所有路径参数先经规范化校验（拒绝含 ".." 的路径段、必须是已存在目录），
  防止路径穿越；输出只写入 --out 目录下的 CSV/JSON。
"""
import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

SEED_DEFAULT = 20261030
YEARS = ("2023", "2024", "2025")
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
DISTRICT_RE = re.compile(r"[一-鿿]{1,4}区")
LEVELS = ("department", "unit")
REA_NOTE = "独立复标集（仅原件与空白工作单，不含系统输出）"
SAMPLING_NOTE = ("定向网格去掉失效的数据态维度（启发式无区分度，三态由标注L1记录）；"
                 "选样为确定性哈希排序（seed:salt:sha256），无随机模块")

DESKTOP_FIELDS = ["rel_path", "file", "sha256", "pages", "text_ok", "scanned_hint",
                  "empty_table_hint", "district_hint", "long_doc", "year", "kind_hint",
                  "level_hint", "org_dir"]
DESKTOP_DUP_FIELDS = DESKTOP_FIELDS + ["duplicate_of"]
BLIND_FIELDS = ["group", "cell", "rel_path", "file", "sha256", "pages", "year",
                "kind_hint", "lvl2", "org_dir", "empty_table_hint", "scanned_hint",
                "long_doc"]
REA_FIELDS = BLIND_FIELDS + ["rea_note"]
UPLOADS_FIELDS = ["source", "rel_path", "file", "sha256", "year", "job_id",
                  "org_name", "selected_org_level"]
UPLOADS_DUP_FIELDS = UPLOADS_FIELDS + ["duplicate_of"]
RULE_FIELDS = ["rule_id", "family", "desc", "正例实例数", "负例材料数", "达标"]


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


def safe_output_directory(raw_value, role):
    """输出目录规范化校验：同样拒绝 ".." 路径段（防路径穿越）；
    目录不存在时创建（只允许在 --out 指向的位置写 CSV/JSON 产物）。"""
    value = str(raw_value).strip()
    if not value:
        raise SystemExit(role + " 路径为空")
    candidate = Path(value)
    if ".." in candidate.parts:
        raise SystemExit(role + " 拒绝包含 '..' 的路径: " + value)
    candidate.mkdir(parents=True, exist_ok=True)
    if not candidate.is_dir():
        raise SystemExit(role + " 不是可用目录: " + value)
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
    （逐字段写入，避免任何拼接歧义），返回十六进制串；同池内按该串升序取前 N。"""
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


def probe_pdf(path):
    """读取页数与文本层提示（只读）。扫描判定：前 80 页文本去空白后不足
    60 字符视为扫描件；个别坏件 MuPDF 会向 stderr 刷警告，打开失败按 0 页、
    无文本处理，不中断整体盘点。"""
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
    head_chars = len(compact[:2000])
    text_ok = head_chars >= SCAN_HINT_CHARS
    return {
        "pages": pages,
        "text_ok": text_ok,
        "empty_table_hint": bool(EMPTY_RE.search(joined)),
        "district_hint": bool(DISTRICT_RE.search(re.sub(r"\s+", "", cover))),
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


def sample_all(kept, seed):
    """特殊格 → 定向网格 → 随机样本 → 六格复标；全流程确定性：
    每格按 selection_rank 升序取前 N，缺格如实记录，不跨格补位。
    网格/复标分层的层级用复排序层级（refine_level 的 lvl2）。"""
    selected = {}

    def pick(pool, count, salt):
        keyed = sorted(pool, key=lambda row: selection_rank(seed, salt, row["sha256"]))
        return keyed[:count]

    def take(group, cell, pool, count, salt):
        picked = pick(pool, count, salt)
        for row in picked:
            selected[row["sha256"]] = (group, cell)
        return picked

    qualified = {row["sha256"] for row in kept
                 if row["text_ok"] and row["kind_hint"] == "final"
                 and row["year"] in YEARS}
    # 特殊格：扫描候选 / 长材料跨页候选 / 区级写法提示（依次执行）。
    special_report = {}
    specials = (
        ("扫描候选", lambda row: row["scanned_hint"]),
        ("长材料跨页候选", lambda row: row["long_doc"]),
        ("区级写法提示", lambda row: row["district_hint"]),
    )
    for cell, predicate in specials:
        pool = [row for row in kept
                if predicate(row) and row["sha256"] not in selected]
        picked = take("定向", cell, pool, SPECIAL_PER_CELL, "sp" + cell)
        special_report[cell] = len(picked)
    # 定向网格：年度 × 部门/单位（复排序层级），每格 4 份。
    grid_report = {}
    for year in YEARS:
        for level in LEVELS:
            cell = year + "|" + level
            pool = [row for row in kept
                    if row["sha256"] in qualified and row["year"] == year
                    and row["lvl2"] == level and row["sha256"] not in selected]
            picked = take("定向", cell, pool, GRID_PER_CELL, "grid" + cell)
            grid_report[cell] = len(picked)
    # 随机样本：2023-2025 每年 30 份，出自剩余合格池。
    random_report = {}
    for year in YEARS:
        pool = [row for row in kept
                if row["sha256"] in qualified and row["year"] == year
                and row["sha256"] not in selected]
        picked = take("随机", year, pool, RANDOM_PER_YEAR, "rnd" + year)
        random_report[year] = len(picked)
    # 独立复标：对盲测集按年度 × 复排序层级分六格，各取 2，不跨格补位。
    blind_rows = [row for row in kept if row["sha256"] in selected]
    rea_rows, rea_report = [], {}
    for year in YEARS:
        for level in LEVELS:
            cell = year + "|" + level
            pool = [row for row in blind_rows
                    if row["year"] == year and row["lvl2"] == level]
            picked = pick(pool, REA_PER_CELL, "rea" + cell)
            rea_report[cell] = {"候选": len(pool), "抽中": len(picked)}
            rea_rows.extend(picked)
    return {
        "selected": selected,
        "grid_report": grid_report,
        "special_report": special_report,
        "random_report": random_report,
        "rea_report": rea_report,
        "rea_rows": rea_rows,
    }


def manifest_sha256(rows):
    """保留件清单指纹：对每行按 rel_path、制表符、sha256、换行四个字段依次
    送入 SHA-256（逐字段写入，避免任何拼接歧义）。"""
    digest = hashlib.sha256()
    for row in rows:
        digest.update(str(row.get("rel_path", "")).encode("utf-8"))
        digest.update(b"\t")
        digest.update(str(row.get("sha256", "")).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def rule_threshold_rows(repo_root):
    """规则门槛工作单：取 src.engine.rule_registry.rule_catalog() 全部规则；
    正例/负例计数留空待标注，达标列固定"待标注"。规则注册表会间接引用仓库根
    下的 api 包，因此把仓库根与 src/ 都加入 sys.path（只加路径，不改包）。"""
    root_path = str(repo_root)
    if root_path not in sys.path:
        sys.path.insert(0, root_path)
    src_path = str(repo_root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    from engine.rule_registry import rule_catalog

    catalog = rule_catalog()
    rows = []
    for item in catalog.get("rules", []):
        rule_id = str(item.get("rule_id", ""))
        rows.append({
            "rule_id": rule_id,
            "family": rule_id.split("-", 1)[0] if "-" in rule_id else "",
            "desc": str(item.get("desc", "")),
            "正例实例数": "",
            "负例材料数": "",
            "达标": "待标注",
        })
    return rows


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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="阶段1评判标准准备：盘点桌面语料母体与 uploads 历史子集，"
                    "生成盲测/复标抽样与规则门槛工作单（只读输入，只写 CSV/JSON）")
    parser.add_argument("--source", default=None,
                        help="桌面语料根目录（可选；按年份子目录组织）")
    parser.add_argument("--uploads", default=None,
                        help="uploads 历史任务目录（默认仓库 uploads/）")
    parser.add_argument("--out", default=None,
                        help="输出目录（默认仓库 outputs/phase1_prep/v2/）")
    parser.add_argument("--seed", type=int, default=SEED_DEFAULT,
                        help="选样种子（默认 20261030）")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    uploads_arg = args.uploads if args.uploads else str(repo_root / "uploads")
    uploads_root = safe_directory(uploads_arg, "--uploads")
    out_arg = args.out if args.out else str(repo_root / "outputs" / "phase1_prep" / "v2")
    out_root = safe_output_directory(out_arg, "--out")

    summary = {}
    if args.source:
        source_root = safe_directory(args.source, "--source")
        kept, dropped = inventory_desktop(source_root)
        for row in kept:
            row["lvl2"] = refine_level(row)
        result = sample_all(kept, args.seed)
        write_csv(out_root / "desktop_corpus_inventory.csv", DESKTOP_FIELDS, kept)
        write_csv(out_root / "desktop_duplicates.csv", DESKTOP_DUP_FIELDS, dropped)
        blind_out = []
        for row in kept:
            mark = result["selected"].get(row["sha256"])
            if mark is None:
                continue
            blind_out.append({
                "group": mark[0], "cell": mark[1],
                "rel_path": row["rel_path"], "file": row["file"],
                "sha256": row["sha256"], "pages": row["pages"],
                "year": row["year"], "kind_hint": row["kind_hint"],
                "lvl2": row["lvl2"], "org_dir": row["org_dir"],
                "empty_table_hint": row["empty_table_hint"],
                "scanned_hint": row["scanned_hint"],
                "long_doc": row["long_doc"],
            })
        blind_out.sort(key=lambda item: (item["group"], item["cell"], item["sha256"]))
        write_csv(out_root / "desktop_blind_sample.csv", BLIND_FIELDS, blind_out)
        rea_out = []
        for row in result["rea_rows"]:
            rea_out.append({
                "group": "", "cell": "",
                "rel_path": row["rel_path"], "file": row["file"],
                "sha256": row["sha256"], "pages": row["pages"],
                "year": row["year"], "kind_hint": row["kind_hint"],
                "lvl2": row["lvl2"], "org_dir": row["org_dir"],
                "empty_table_hint": row["empty_table_hint"],
                "scanned_hint": row["scanned_hint"],
                "long_doc": row["long_doc"],
                "rea_note": REA_NOTE,
            })
        write_csv(out_root / "desktop_reannotation_12.csv", REA_FIELDS, rea_out)
        summary["source_root"] = str(source_root)
        summary["source_manifest_sha256"] = manifest_sha256(kept)
        summary["文件总数"] = len(kept) + len(dropped)
        summary["去重后"] = len(kept)
        summary["重复剔除"] = len(dropped)
        summary["kind_hint"] = count_by(kept, "kind_hint")
        summary["level_hint"] = count_by(kept, "level_hint")
        summary["年份"] = count_by(kept, "year")
        summary["scanned_hint"] = sum(1 for row in kept if row["scanned_hint"])
        summary["text_ok"] = sum(1 for row in kept if row["text_ok"])
        summary["lvl2"] = count_by(kept, "lvl2")
        summary["seed"] = args.seed
        summary["脚本自身sha256"] = script_self_sha256(Path(__file__).resolve())
        summary["定向网格_年度层级每格" + str(GRID_PER_CELL)] = result["grid_report"]
        summary["定向特殊格"] = result["special_report"]
        summary["随机样本"] = result["random_report"]
        summary["盲测集合计"] = len(blind_out)
        summary["复标六格"] = result["rea_report"]
        summary["复标合计"] = len(rea_out)
        if result["special_report"].get("长材料跨页候选", 0) < SPECIAL_PER_CELL:
            long_total = sum(1 for row in kept if row["long_doc"])
            if long_total:
                summary["长材料不足格说明"] = ("全母体仅 " + str(long_total)
                                              + " 份 ≥60 页，均在网格/随机前被特殊格收入或池中冲突")
            else:
                summary["长材料不足格说明"] = "全母体无 ≥60 页材料，长材料跨页候选格空置"
        print("桌面母体: 文件 " + str(len(kept) + len(dropped))
              + "，去重后 " + str(len(kept))
              + "，重复剔除 " + str(len(dropped))
              + "，盲测 " + str(len(blind_out))
              + "，复标 " + str(len(rea_out)))

    try:
        threshold_rows = rule_threshold_rows(repo_root)
    except Exception as error:  # 规则清单只影响门槛工作单，不阻断抽样产物
        threshold_rows = []
        summary["规则门槛错误"] = str(error)
    summary["规则门槛条数"] = len(threshold_rows)
    summary["说明"] = SAMPLING_NOTE
    write_csv(out_root / "rule_thresholds.csv", RULE_FIELDS, threshold_rows)

    org_links, org_levels = load_org_directory(repo_root / "data")
    uploads_kept, uploads_dropped = inventory_uploads(uploads_root, org_links, org_levels)
    sha_by_job = {row["job_id"]: row["sha256"] for row in uploads_kept}
    dangling = 0
    for row in uploads_dropped:
        target = row.get("duplicate_of", "")
        if target not in sha_by_job or sha_by_job[target] != row["sha256"]:
            dangling += 1
    write_csv(out_root / "uploads_historical_inventory.csv", UPLOADS_FIELDS, uploads_kept)
    write_csv(out_root / "uploads_dedupe_dropped_fixed.csv", UPLOADS_DUP_FIELDS,
              uploads_dropped)
    summary["uploads"] = {
        "任务总数": len(uploads_kept) + len(uploads_dropped),
        "去重后": len(uploads_kept),
        "重复剔除": len(uploads_dropped),
        "悬空引用条数": dangling,
        "source_manifest_sha256": manifest_sha256(uploads_kept),
    }

    with open(out_root / "summary.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("uploads: 任务 " + str(len(uploads_kept) + len(uploads_dropped))
          + "，去重后 " + str(len(uploads_kept))
          + "，重复剔除 " + str(len(uploads_dropped))
          + "，悬空引用 " + str(dangling))
    print("规则门槛: " + str(len(threshold_rows)) + " 条")
    print("输出目录: " + str(out_root))


if __name__ == "__main__":
    main()
