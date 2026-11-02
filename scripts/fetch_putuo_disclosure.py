#!/usr/bin/env python3
"""普陀区政府网站"财政预决算"采集器。

栏目接口: POST https://www.shpt.gov.cn/front/api/data/search
归档: 年 / 主管部门 / {公示标题}.pdf （下属单位的"单位预算/决算"并入其主管部门文件夹）

用法:
  python3 scripts/fetch_putuo_disclosure.py plan                 # 建索引 + 打印归档计划, 不下载
  python3 scripts/fetch_putuo_disclosure.py download             # 按默认年度下载
  python3 scripts/fetch_putuo_disclosure.py download --years 2027 --kinds 预算
  python3 scripts/fetch_putuo_disclosure.py download --limit 20  # 小样本试跑
"""
import argparse
import collections
import concurrent.futures as cf
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = "https://www.shpt.gov.cn"
API = BASE + "/front/api/data/search"
DIRECTORY = BASE + "/zhengwu/zfbm-czyjs/index.html"
DISTRICT_LEVEL = ("/zhengwu/qzf-czzj/index.html", "区政府预决算")   # 区本级栏目
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
DEFAULT_ROOT = Path.home() / "Desktop" / "普陀区"
DEFAULT_BUDGET_YEARS = range(2022, 2027)      # 预算 2022-2026
DEFAULT_ACCOUNT_YEARS = range(2022, 2026)     # 决算 2022-2025
LINK_RE = re.compile(r'href="(/zhengwu/[a-z0-9]+(?:-[a-z0-9]+)*/index\.html)"[^>]*title="([^"]*)"')
ANY_LINK_RE = re.compile(r'href="/zhengwu/([a-z0-9]+(?:-[a-z0-9]+)*)/index\.html"')
IDS_RE = re.compile(r"ids:\s*'(\d+)'")
PAGE_SIZE = 100
MAX_BYTES = 40 * 1024 * 1024

MAGIC = [(b"%PDF", ".pdf"), (b"PK\x03\x04", ".zip"), (b"\xd0\xcf\x11\xe0", ".doc"), (b"\x22\x3c\x3f\x78", ".xml")]


def http_get(url, tries=4, timeout=30):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": DIRECTORY})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:
            if i == tries - 1:
                raise RuntimeError(f"GET {url}: {exc}") from exc
            time.sleep(1.0 * (i + 1))


def http_post(payload, tries=4):
    body = json.dumps(payload).encode()
    for i in range(tries):
        try:
            req = urllib.request.Request(API, data=body, headers={
                "User-Agent": UA, "Content-Type": "application/json", "Referer": DIRECTORY})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except Exception as exc:
            if i == tries - 1:
                raise RuntimeError(f"POST {payload}: {exc}") from exc
            time.sleep(1.5 * (i + 1))


def channels():
    """目录页 -> 主管部门(含区本级) -> 下属单位，返回带 channel_id 的栏目清单。"""
    root_html = http_get(DIRECTORY).decode("utf-8", "ignore")
    roots = {}
    for href, title in LINK_RE.findall(root_html):
        slug = href.split("/zhengwu/")[1].split("/")[0]
        roots.setdefault(slug, title)
    href, title = DISTRICT_LEVEL
    roots["qzf-czzj"] = title

    def probe(slug, known_name=None):
        try:
            html = http_get(f"{BASE}/zhengwu/{slug}/index.html").decode("utf-8", "ignore")
        except RuntimeError:
            return None
        m = IDS_RE.search(html)
        page_title = re.search(r"<title>(.*?)</title>", html)
        fallback = (page_title.group(1) if page_title else slug).replace("_上海普陀", "").strip()
        anchors = {h.split("/zhengwu/")[1].split("/")[0]: t for h, t in LINK_RE.findall(html)}
        return {"slug": slug, "name": known_name or fallback, "channel_id": m.group(1) if m else None,
                "children": anchors}

    with cf.ThreadPoolExecutor(8) as ex:
        probed = [p for p in ex.map(probe, sorted(roots)) if p]
    root_slugs = {p["slug"] for p in probed}
    parent = {}
    child_names = {}
    for p in probed:
        if p["slug"] == "qzf-czzj":      # 区本级栏目只收自己的公开事项, 同级链接属"财政资金"其他专栏
            continue
        for c, cname in p["children"].items():
            if c in root_slugs or c.endswith(("-czyjs", "-czzj")):   # 面包屑/专栏导航页不是公开单位栏目
                continue
            parent.setdefault(c, set()).add(p["slug"])
            child_names.setdefault(c, cname or c)
    ambiguous = {c: v for c, v in parent.items() if len(v) > 1}
    if ambiguous:
        print(f"警告: {len(ambiguous)} 个下属栏目有多个上级, 归入名称排序第一个: "
              f"{list(ambiguous)[:3]}", file=sys.stderr)

    def root_of(slug):
        return sorted(parent[slug])[0]

    units = [c for c in sorted(parent) if c not in root_slugs]
    with cf.ThreadPoolExecutor(8) as ex:
        unit_rows = [u for u in ex.map(lambda s: probe(s, child_names.get(s)), units)
                     if u and u["channel_id"]]
    out = []
    for p in probed:
        if p["channel_id"]:
            grp = "区本级" if p["slug"] == "qzf-czzj" else p["name"]
            out.append({**p, "children": None, "group": grp, "is_group_root": p["slug"] != "qzf-czzj"})
    name_by_root = {p["slug"]: p["name"] for p in probed}
    for u in unit_rows:
        grp = name_by_root[root_of(u["slug"])]
        out.append({**u, "children": None, "group": grp, "is_group_root": False})
    return out


def fetch_items(chs, workers=5):
    def one(ch):
        rows, page, pages = [], 1, 1
        while page <= pages:
            d = http_post({"channelList": [ch["channel_id"]], "pageNo": page, "pageSize": PAGE_SIZE})
            if not d or d.get("code") != 0:
                raise RuntimeError(f"{ch['slug']} p{page}: {json.dumps(d, ensure_ascii=False)[:200] if d else 'null'}")
            data = d["data"]
            pages = max(int(data.get("totalPage") or 1), 1)
            for it in data["list"]:
                atts = it.get("attaches") or []
                ts = (it.get("display_date") or 0) / 1000
                rows.append({"channel_slug": ch["slug"], "channel_name": ch["name"], "group": ch["group"],
                             "title": (it.get("title") or "").strip(),
                             "publish_date": time.strftime("%Y-%m-%d", time.gmtime(ts + 8 * 3600)),
                             "article_url": it.get("url"),
                             "attachments": [{"name": a.get("name") or "", "url": a.get("url") or ""} for a in atts]})
            page += 1
        return rows

    with cf.ThreadPoolExecutor(workers) as ex:
        chunks = list(ex.map(one, chs))
    return [r for c in chunks for r in c]


def classify(title):
    """返回 (kind, 报告年度)。决算优先; 预算取最后一个"预算"前的年份(兼容"2025年预算执行情况和2026年预算草案")。
    年度写在关键词之后的("决算公开2023")回退取关键词后第一个年份。"""
    norm = re.sub(r"(?<=\d)\s+(?=\d)", "", title)
    for kind, occ in (("决算", "index"), ("预算", "rindex")):
        if kind not in norm:
            continue
        cut = getattr(norm, occ)(kind)
        head, tail = norm[:cut], norm[cut + len(kind):]
        yrs = re.findall(r"20\d{2}", head)
        if yrs:
            return (kind, int(yrs[-1]))
        yrs = re.findall(r"20\d{2}", tail)
        if yrs:
            return (kind, int(yrs[0]))
        return (kind, None)
    return ("其他", None)


def infer_year(kind, publish_date):
    """标题无年度时按发布日反推: 预算随发布年度, 决算是次年发布。"""
    y = int((publish_date or "")[:4])
    if not y:
        return None
    return y if kind == "预算" else y - 1


def safe_name(text, maxlen=120):
    name = re.sub(r"[\/\r\n\t]+", " ", text).strip().strip(".")
    name = re.sub(r"\s{2,}", " ", name)
    if len(name) > maxlen:
        name = name[:maxlen].rstrip()
    return name or "未命名"


def build_plan(items, budget_years, account_years):
    """规划落盘路径: 年/主管部门/标题.pdf ; 区本级单独一档。返回 (files, skipped)。"""
    wanted = {}
    for y in budget_years:
        wanted[("预算", y)] = True
    for y in account_years:
        wanted[("决算", y)] = True
    files, skipped = [], []
    for it in items:
        kind, year = classify(it["title"])
        inferred = False
        if kind == "其他":
            skipped.append({**it, "reason": "非预决算条目(绩效/说明等)"})
            continue
        if year is None:
            year = infer_year(kind, it["publish_date"])
            inferred = True
        if year is None:
            skipped.append({**it, "reason": "标题无年度且发布日无法反推"})
            continue
        if (kind, year) not in wanted:
            skipped.append({**it, "reason": f"年度或类别不在范围: {kind}{year}"})
            continue
        folder = f"区本级/{year}" if it["group"] == "区本级" else f'{kind}/{year}/{safe_name(it["group"])}'
        atts = it["attachments"]
        if not atts:
            atts = [{"name": "", "url": ""}]     # 需进正文提取, download 阶段处理
        for n, a in enumerate(atts, 1):
            ext = os.path.splitext(a["name"])[1].lower()
            files.append({
                "kind": kind, "year": year, "folder": folder, "unit": it["channel_name"],
                "title": it["title"], "publish_date": it["publish_date"],
                "year_inferred": inferred, "seq": n, "total_atts": len(atts),
                "attach_name": a["name"],
                "ext_hint": ext if ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx") else ".pdf",
                "url": a["url"], "article_url": it["article_url"],
            })
    # 命名: 公示标题为准(附件名不可靠); 多附件带序号; 仍同名再编号
    base_of = []
    for f in files:
        base = safe_name(f["title"])
        if f["total_atts"] > 1:
            base = f"{base}_{f['seq']}"
        ext = f["ext_hint"]
        name = base + (ext if ext != ".pdf" else ".pdf")
        key = (f["folder"], name)
        base_of.append([base, ext, name, key])
    counts = collections.Counter(k for _, _, _, k in base_of)
    bumped = collections.Counter()
    for i, (base, ext, name, key) in enumerate(base_of):
        if counts[key] > 1:
            bumped[key] += 1
            name = f"{base}_{bumped[key]}{ext}"
        files[i]["filename_base"] = base
        files[i]["filename"] = name
    return files, skipped


def extract_from_article(job):
    """无直挂附件时, 从公示正文里取 PDF 链接。"""
    html = http_get(job["article_url"]).decode("utf-8", "ignore")
    links = re.findall(r'href="([^"]+?\.(?:pdf|docx?|xlsx?))"', html, re.I)
    links = [u if u.startswith("http") else BASE + u for u in links]
    return [u for u in dict.fromkeys(links)]


def download(files, root, workers=6, limit=None, log=print):
    manifest_path = root / "_采集清单" / "manifest.csv"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    done = {}
    if manifest_path.exists():
        with manifest_path.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                path = row.get("path") or f'{row.get("folder")}/{row.get("filename")}'
                done[(path, row.get("url", ""))] = row.get("sha256", "")

    todo = []
    for f in files:
        rel = f"{f['folder']}/{f['filename']}"
        if (rel, f["url"]) in done and (root / rel).exists():
            continue
        todo.append((rel, f))
    if limit:
        todo = todo[:limit]
    log(f"待下载 {len(todo)} / 计划 {len(files)} (已存在跳过 {len(files) - len(todo) if not limit else 'n/a'})")

    existing_rows = []
    if manifest_path.exists():
        with manifest_path.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                row.setdefault("path", f'{row.get("folder")}/{row.get("filename")}')
                existing_rows.append(row)

    rows = {r["path"]: r for r in existing_rows}

    def flush():
        vals = list(rows.values())
        write_manifest(manifest_path, [x for x in vals if x["status"] == "ok"],
                       root / "_采集清单" / "failed.csv", [x for x in vals if x["status"] != "ok"])

    def work(item):
        rel, f = item
        target = root / rel
        try:
            url = f["url"]
            if not url:
                found = extract_from_article(f)
                if not found:
                    return {**f, "path": rel, "status": "no_pdf_in_article", "bytes": 0, "sha256": ""}
                url = found[0]
                f = {**f, "url": url, "filename": Path(rel).name,
                     "ext_hint": os.path.splitext(url)[1].lower() or ".pdf"}
                target = root / f"{f['folder']}/{f['filename_base']}{f['ext_hint']}"
                rel = str(target.relative_to(root))
            blob = http_get(url, tries=4, timeout=60)
            magic = next((ext for sig, ext in MAGIC if blob.startswith(sig)), None)
            if magic is None:
                return {**f, "path": rel, "status": f"not_a_document({blob[:8]!r})", "bytes": len(blob), "sha256": ""}
            if len(blob) > MAX_BYTES:
                return {**f, "path": rel, "status": "too_large", "bytes": len(blob), "sha256": ""}
            planned_ext = target.suffix.lower()
            if magic != planned_ext and (planned_ext == ".pdf" or not planned_ext):
                target = target.with_suffix(magic)          # 站点附件名后缀与实际内容不符时按魔数纠正
                rel = str(target.relative_to(root))
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".part")
            tmp.write_bytes(blob)
            os.replace(tmp, target)
            return {**f, "path": rel, "status": "ok", "bytes": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest()}
        except Exception as exc:
            return {**f, "path": rel, "status": f"error: {exc}"[:300], "bytes": 0, "sha256": ""}

    ok = fail = 0
    t0 = time.time()
    with cf.ThreadPoolExecutor(workers) as ex:
        for i, r in enumerate(ex.map(work, todo), 1):
            ok += r["status"] == "ok"
            fail += r["status"] != "ok"
            r["filename"] = Path(r["path"]).name
            rows[r["path"]] = r
            if i % 200 == 0 or i == len(todo):
                log(f"  {i}/{len(todo)} ok={ok} fail={fail} {time.time() - t0:.0f}s")
                flush()
    flush()
    return ok, fail


COLS = ["path", "kind", "year", "year_inferred", "folder", "unit", "group", "filename", "title",
        "publish_date", "url", "article_url", "bytes", "sha256", "status", "reason"]


def write_manifest(path, ok_rows, failed_path, failed_rows):
    def dump(p, rs):
        with p.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS, extrasaction="ignore")
            w.writeheader()
            for r in rs:
                w.writerow(r)
    dump(path, ok_rows)
    dump(failed_path, failed_rows)


def calendar_file():
    """唯一数据源：系统设置页「年度公开时限」写入的 data/disclosure-calendar.json。"""
    env = os.getenv("DISCLOSURE_CALENDAR_PATH")
    return Path(env) if env else Path(__file__).resolve().parents[1] / "data" / "disclosure-calendar.json"


def load_deadline(kind, year):
    """取当年通知的公开截止日。没填就返回 None —— 公开时限每年随人大批准/财政批复变动，
    区里年度通知又常只写编报时间，所以绝不允许回落到任何默认月日。"""
    path = calendar_file()
    if not path.exists():
        return None
    try:
        rows = json.loads(path.read_text()).get("rows", [])
    except (json.JSONDecodeError, AttributeError):
        return None
    out, source = {}, ""
    for row in rows:
        if str(row.get("kind")) == kind and int(row.get("year") or 0) == int(year) and row.get("deadline"):
            out[str(row.get("level"))] = str(row["deadline"])
            source = source or str(row.get("source") or "")
    if out.get("部门") and out.get("单位"):
        return {**out, "来源": source}
    return None


def day_diff(later, earlier):
    import datetime
    try:
        return f"{(datetime.date.fromisoformat(later) - datetime.date.fromisoformat(earlier)).days:+d}天"
    except (ValueError, TypeError):
        return ""


def cycle_gap(this, prev):
    """比同一发布周期内的先后(只看月日): 决算总在次年发布, 用绝对日期会恒差约一年。"""
    import datetime
    try:
        a = datetime.date(2001, int(this[5:7]), int(this[8:10]))
        b = datetime.date(2001, int(prev[5:7]), int(prev[8:10]))
    except (ValueError, IndexError, TypeError):
        return ""
    return f"{(a - b).days:+d}天"


def norm_org(name):
    s = re.sub(r"\s+", "", name or "")
    s = s.replace("(", "（").replace(")", "）")
    return s.replace("中国共产党", "中共")


def name_key(name):
    """比对用名: 去掉"（本部/本级/机关）"这类后缀差异, 避免把用词不同当成缺失主体。"""
    s = norm_org(name)
    s = re.sub(r"（[^（）]*）$", "", s)
    s = re.sub(r"(本部|本级|机关)$", "", s)
    return s


def directory_roster(org_json):
    """项目组织目录里登记的主管部门/单位, 作为站点名册之外的第二重对照。"""
    try:
        data = json.loads(Path(org_json).read_text())
    except Exception:
        return {}
    nodes = data.get("organizations") if isinstance(data, dict) else data
    out = {}
    for n in nodes or []:
        if n.get("level") in ("department", "unit") and n.get("name"):
            out[norm_org(n["name"])] = n["name"]
    return out


def load_roster(cache_dir, rebuild=False):
    p = cache_dir / "channels.json"
    if p.exists() and not rebuild:
        return json.loads(p.read_text())
    chs = channels()
    p.write_text(json.dumps(chs, ensure_ascii=False, indent=1))
    return chs


def year_items(items):
    """给每条公示补上 (kind, 报告年度)。"""
    out = []
    for it in items:
        k, y = classify(it["title"])
        if y is None:
            y = infer_year(k, it["publish_date"])
        out.append({**it, "kind": k, "report_year": y})
    return out


def deadline_gap(publish_date, deadline_date):
    """公开时间相对"当年通知截止日"的偏差; 没有通知日期就返回空, 不做假设。"""
    if not publish_date or not deadline_date:
        return ""
    return day_diff(publish_date, deadline_date)


def _cycle_days(s):
    try:
        return int(s["较上年周期偏差"].replace("天", ""))
    except ValueError:
        return None


def build_checklist(items, roster, kind, year, manifest_rows, dir_names, deadline=None):
    stamped = year_items(items)
    rows_by_ch = collections.defaultdict(list)
    prev_by_ch = collections.defaultdict(list)
    hist = collections.defaultdict(set)
    for it in stamped:
        if it["kind"] == kind and it["report_year"] == year:
            rows_by_ch[it["channel_slug"]].append(it)
        if it["kind"] == kind and it["report_year"] == year - 1:
            prev_by_ch[it["channel_slug"]].append(it)
        if it["report_year"]:
            hist[it["channel_slug"]].add((it["kind"], it["report_year"]))
    prev_pub = {s: sorted(v, key=lambda x: x["publish_date"])[-1]["publish_date"]
                for s, v in prev_by_ch.items()}
    url_state = {r["url"]: r for r in manifest_rows}

    dir_keys = {name_key(v) for v in dir_names.values()}
    alias_targets = {name_key(a) for v in dir_names.values()
                     for a in re.findall(r"（([^（）]+)）\s*$", norm_org(v))}

    def in_dir(name):
        return name_key(name) in dir_keys or name_key(name) in alias_targets

    subjects, missing = [], []
    for ch in roster:
        if ch["slug"] == "qzf-czzj":
            level = "区本级"
        elif ch.get("is_group_root"):
            level = "部门"
        else:
            level = "单位"
        got = sorted(rows_by_ch.get(ch["slug"], []), key=lambda x: x["publish_date"])
        if got:
            latest = got[-1]
            states = [url_state.get(a["url"], {}).get("status", "未入库") for it in got for a in (it["attachments"] or [{"url": ""}])]
            dl_key = "部门" if (level == "部门" or any(f"部门{kind}" in it["title"] for it in got)) else "单位"
            dl_date = (deadline or {}).get(dl_key, "")
            prev = prev_pub.get(ch["slug"], "")
            row = {
                "层级": level, "主管部门": ch["group"], "公开主体": ch["name"], "站点栏目": ch["slug"],
                "是否公开": "是", "公示条数": len(got), "公开时间": latest["publish_date"],
                "首次公开时间": got[0]["publish_date"],
                "上年公开时间": prev, "较上年周期偏差": cycle_gap(latest["publish_date"], prev) if prev else "",
                "通知截止日": dl_date, "距通知截止日": deadline_gap(latest["publish_date"], dl_date),
                "公示标题": " / ".join(it["title"] for it in got)[:300],
                "公开页URL": " / ".join(it["article_url"] or "" for it in got)[:500],
                "PDF链接": " / ".join(a["url"] for it in got for a in it["attachments"])[:600],
                "落地文件": " / ".join(url_state.get(a["url"], {}).get("path", "") for it in got for a in it["attachments"])[:600],
                "采集状态": "ok" if all(s == "ok" for s in states) else " / ".join(filter(None, set(states))),
                "缺失判定": "",
                "组织目录对照": "目录在册" if in_dir(ch["name"]) else "目录未见(建议补目录)",
            }
            subjects.append(row)
        else:
            other = "预算" if kind == "决算" else "决算"
            if (kind, year - 1) in hist[ch["slug"]]:
                why = f"上年({year - 1})有{kind}公开、本度无"
            elif (other, year) in hist[ch["slug"]]:
                why = f"本度{other}已公开、{kind}无"
            elif any(k == kind for k, _ in hist[ch["slug"]]):
                why = f"有{kind}公开史、本度无"
            else:
                why = f"该栏目无{kind}公开记录"
            row = {"层级": level, "主管部门": ch["group"], "公开主体": ch["name"], "站点栏目": ch["slug"],
                   "是否公开": "否", "公示条数": 0, "公开时间": "", "首次公开时间": "",
                   "上年公开时间": prev_pub.get(ch["slug"], ""), "较上年周期偏差": "",
                   "通知截止日": "", "距通知截止日": "",
                   "公示标题": "", "公开页URL": "", "PDF链接": "", "落地文件": "",
                   "采集状态": "", "缺失判定": why,
                   "组织目录对照": "目录在册" if in_dir(ch["name"]) else "目录未见(建议补目录)"}
            subjects.append(row)
            missing.append(row)

    # 组织目录在册、但站点"财政预决算"名册里找不到同名主体
    import difflib
    subj_keys = {}
    for ch in roster:
        subj_keys.setdefault(name_key(ch["name"]), ch["name"])
        subj_keys.setdefault(name_key(ch["group"]), ch["group"])
    orphans = []
    for disp in dir_names.values():
        if not _same_district(disp, roster):
            continue
        nk = name_key(disp)
        if nk in subj_keys:
            continue
        alias_txt = re.findall(r"（([^（）]+)）\s*$", norm_org(disp))
        hit = [subj_keys[name_key(a)] for a in alias_txt if name_key(a) in subj_keys]
        if hit:
            orphans.append({"目录主体": disp, "判定": "别名一致(站点以另一名称公开)", "近似站点主体": hit[0]})
            continue
        close = difflib.get_close_matches(nk, subj_keys, n=1, cutoff=0.72)
        orphans.append({"目录主体": disp,
                        "判定": "名称不一致(疑似同一主体)" if close else "站点名册未见对应栏目(需人工对照)",
                        "近似站点主体": subj_keys[close[0]] if close else ""})
    return subjects, missing, orphans


def _same_district(name, roster):
    """只保留区名与站点主体同域(普陀)的目录项, 避免把市级/外区单位算成缺。"""
    return "普陀" in name


def build_dept_checklist(subjects, kind, year):
    """主管部门层: 部门决算是否公开 + 下属单位公开率。"""
    by_group = collections.defaultdict(list)
    for s in subjects:
        if s["层级"] == "区本级":
            continue
        by_group[s["主管部门"]].append(s)
    out = []
    for g, rows in sorted(by_group.items()):
        dept_own = [r for r in rows if r["层级"] == "部门" and r["是否公开"] == "是"]
        dept_in_unit = [r for r in rows if r["是否公开"] == "是" and "部门决算" in r["公示标题"]]
        units = [r for r in rows if r["层级"] == "单位"]
        units_ok = [r for r in units if r["是否公开"] == "是"]
        if dept_own:
            dept_public, src = "是（主管部门栏目）", dept_own
        elif dept_in_unit:
            dept_public, src = "是（在本部栏目下公开）", dept_in_unit
        else:
            dept_public, src = "否", []
        out.append({"主管部门": g, f"{kind}是否公开": dept_public,
                    "部门公开时间": src[0]["公开时间"] if src else "",
                    "部门上年公开时间": src[0]["上年公开时间"] if src else "",
                    "部门较上年": src[0]["较上年周期偏差"] if src else "",
                    "部门公示标题": src[0]["公示标题"][:160] if src else "",
                    "部门公开页": src[0]["公开页URL"][:240] if src else "",
                    "下属单位名册": len(units), "已公开单位": len(units_ok),
                    "缺失单位": len(units) - len(units_ok),
                    "缺失单位名单": "、".join(r["公开主体"] for r in units if r["是否公开"] == "否")[:400]})
    return out


def write_rows(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_checklist_md(path, tag, kind, year, subjects, missing, orphans, dept_rows, deadline=None):
    ok = [s for s in subjects if s["是否公开"] == "是"]
    not_landed = [s for s in ok if s["采集状态"] not in ("ok", "")]
    late = sorted([s for s in ok if s["距通知截止日"].startswith("+")],
                  key=lambda x: -int(x["距通知截止日"].replace("天", "").replace("+", ""))) if deadline else []
    dated = sorted(s["公开时间"] for s in ok if s["公开时间"])
    def _dv(s):
        try:
            return int(s["较上年周期偏差"].replace("天", ""))
        except ValueError:
            return None
    drift = sorted([s for s in ok if (_dv(s) or 0) > 0], key=lambda s: -_dv(s))
    [s for s in ok if _dv(s) == 0]
    dl_txt = (f"已按当年通知取值：主管部门 {deadline['部门']}、下属单位 {deadline['单位']}"
              f"（来源 {deadline.get('来源') or '未注明'}）" if deadline
              else "未填当年通知截止日 → 不计算偏差。公开时限每年随人大批准/财政批复日期变动，"
                   "站点年度通知只公布编报时间(一上/一下/二上)，未公布公开截止日；"
                   "要判逾期请先在系统设置页「年度公开时限」录入当年通知日期（data/disclosure-calendar.json）")
    L = [f"# {tag} 公开清单摘要（上海市普陀区）\n",
         "- 依据：上海普陀区政府网站「政务/财政资金/财政预决算」栏目，接口 `POST /front/api/data/search`",
         f"- 公开主体名册：站点 {len(subjects)} 个（主管部门栏目 {sum(1 for s in subjects if s['层级']=='部门')}、"
         f"下属单位栏目 {sum(1 for s in subjects if s['层级']=='单位')}、区本级 1 个）",
         f"- 已公开 {len(ok)} 个，未公开 {len(missing)} 个，"
         f"已公开但文件未入库 {len(not_landed)} 个",
         f"- 公开时间分布：最早 {dated[0] if dated else '—'}，中位 "
         f"{dated[len(dated)//2] if dated else '—'}，最晚 {dated[-1] if dated else '—'}（共 {len(dated)} 个日期）",
         f"- 时限口径：{dl_txt}\n",
         "## 一、未公开（缺失）主体\n"]
    if missing:
        L.append("| 层级 | 主管部门 | 公开主体 | 判定依据 |")
        L.append("|---|---|---|---|")
        for m in sorted(missing, key=lambda x: (x["层级"], x["主管部门"])):
            L.append(f"| {m['层级']} | {m['主管部门']} | {m['公开主体']} | {m['缺失判定']} |")
    else:
        L.append("（无）")
    L.append("\n## 二、主管部门 {k} 汇总\n".format(k=kind))
    L.append("| 主管部门 | 部门{0} | 公开时间 | 上年公开时间 | 较上年 | 下属单位(名册/已公开/缺) |".format(kind))
    L.append("|---|---|---|---|---|---|")
    for d in dept_rows:
        L.append(f"| {d['主管部门']} | {d[f'{kind}是否公开']} | {d['部门公开时间'] or '—'} | {d['部门上年公开时间'] or '—'} | "
                 f"{d['部门较上年'] or '—'} | {d['下属单位名册']}/{d['已公开单位']}/{d['缺失单位']} |")
    if deadline:
        L.append(f"\n## 三、晚于当年通知截止日的公开（{len(late)} 个）\n")
        L.append("| 偏差 | 层级 | 公开主体 | 公开时间 | 通知截止日 | 主管部门 |")
        L.append("|---|---|---|---|---|---|")
        for s in late:
            L.append(f"| {s['距通知截止日']} | {s['层级']} | {s['公开主体']} | {s['公开时间']} | "
                     f"{s['通知截止日']} | {s['主管部门']} |")
        if not late:
            L.append("| — | — | 无 | — | — | — |")
    L.append(f"\n## 四、同一周期内比上年度更晚公开的（{len(drift)} 个）\n")
    L.append("同主体同比，不依赖任何截止日假设。\n")
    L.append("| 晚于上年周期 | 层级 | 公开主体 | 本度公开时间 | 上年公开时间 | 主管部门 |")
    L.append("|---|---|---|---|---|---|")
    for s in drift[:30]:
        L.append(f"| {s['较上年周期偏差']} | {s['层级']} | {s['公开主体']} | {s['公开时间']} | "
                 f"{s['上年公开时间']} | {s['主管部门']} |")
    if not drift:
        L.append("| — | — | 无 | — | — | — |")
    if len(drift) > 30:
        L.append(f"\n> 其余 {len(drift) - 30} 个见 `清单_{tag}_主体.csv` 按「较上年周期偏差」排序。")
    L.append(f"\n## 五、已公开但文件未入库（{len(not_landed)} 个）\n")
    for s in not_landed:
        L.append(f"- {s['公开主体']}：{s['公示标题'][:60]}｜公开页 {s['公开页URL'][:120]}｜状态 {s['采集状态']}")
    if not not_landed:
        L.append("（无）")
    L.append(f"\n## 六、组织目录与站点名册对照存疑（{len(orphans)} 个）\n")
    L.append("这些是项目组织目录里在册、但在站点「财政预决算」名册上找不到同名主体的条目。"
             "名称不一致/别名一致通常指同一主体换了写法；\"需人工对照\"才可能真是没有公开入口。\n")
    L.append("| 目录主体 | 判定 | 近似站点主体 |")
    L.append("|---|---|---|")
    for o in orphans:
        L.append(f"| {o['目录主体']} | {o['判定']} | {o['近似站点主体'] or '—'} |")
    if not orphans:
        L.append("| — | 无 | — |")
    L.append("\n> 逐条公开时间、公示标题、公开页 URL、PDF 链接与落地文件见同目录 "
             f"`清单_{tag}_主体.csv`；缺失标注见 `缺失_{tag}.csv`。\n")
    path.write_text("\n".join(L), encoding="utf-8")
    return path


def checklist_main(args, cache_dir, items):
    roster = load_roster(cache_dir, rebuild=args.rebuild_roster)
    manifest_path = cache_dir / "manifest.csv"
    mrows = []
    if manifest_path.exists():
        with manifest_path.open(encoding="utf-8-sig") as fh:
            mrows = list(csv.DictReader(fh))
    org_json = args.org_json or Path(__file__).resolve().parents[1] / "data" / "organizations.json"
    dir_names = directory_roster(org_json)
    pt_dir = {k: v for k, v in dir_names.items() if "普陀" in k}
    deadline = load_deadline(args.kind, args.year)
    subjects, missing, orphans = build_checklist(items, roster, args.kind, args.year, mrows, pt_dir, deadline)

    tag = f'{args.year}年度{args.kind}'
    sub_path = cache_dir / f"清单_{tag}_主体.csv"
    dept_path = cache_dir / f"清单_{tag}_部门汇总.csv"
    miss_path = cache_dir / f"缺失_{tag}.csv"
    cmp_path = cache_dir / f"目录对照_{tag}.csv"
    dept_rows = build_dept_checklist(subjects, args.kind, args.year)
    write_rows(sub_path, subjects)
    write_rows(dept_path, dept_rows)
    blank = {k: "" for k in subjects[0]} if subjects else {}
    write_rows(miss_path, [
        *missing,
        *[dict(blank, 层级="组织目录在册", 主管部门="（站点名册未列）", 公开主体=o["目录主体"],
               站点栏目=o["近似站点主体"], 是否公开="无法判定", 缺失判定=o["判定"],
               组织目录对照="目录在册") for o in orphans]])
    write_rows(cmp_path, orphans)
    md_path = write_checklist_md(cache_dir / f"清单_{tag}_摘要.md", tag, args.kind, args.year,
                                 subjects, missing, orphans, dept_rows, deadline)

    ok = [s for s in subjects if s["是否公开"] == "是"]
    late = [s for s in ok if s["距通知截止日"].startswith("+")]
    drift = [s for s in ok if (_cycle_days(s) or 0) > 0]
    not_landed = [s for s in ok if s["采集状态"] not in ("ok", "")]
    print(f"\n=== {tag} 公开清单 ===")
    print(f"站点公开主体 {len(subjects)} 个：已公开 {len(ok)}，未公开 {len(missing)}")
    print(f"  主管部门栏目 {sum(1 for s in subjects if s['层级']=='部门')} 个"
          f"（未公开 {sum(1 for s in missing if s['层级']=='部门')}）、"
          f"下属单位栏目 {sum(1 for s in subjects if s['层级']=='单位')} 个"
          f"（未公开 {sum(1 for s in missing if s['层级']=='单位')}）")
    print(f"同一周期内比{args.year - 1}年度晚公开: {len(drift)} 个"
          f"（同日 {len([s for s in ok if _cycle_days(s) == 0])} 个、更早 "
          f"{len([s for s in ok if _cycle_days(s) is not None and _cycle_days(s) < 0])} 个）——不依赖截止日假设")
    print("晚于通知截止日: " + (f"{len(late)} 个（截止日 部门{deadline['部门']}/单位{deadline['单位']}）"
                                 if deadline else "未计算——系统设置页「年度公开时限」未填当年通知日期"))
    print(f"已公开但文件未入库: {len(not_landed)} 个")
    print(f"组织目录在册但站点名册对不上: {len(orphans)} 个 → "
          + (", ".join(f"{k} {v}个" for k, v in collections.Counter(o['判定'] for o in orphans).items()) or "无"))
    print(f"\n可读摘要: {md_path}\n主体清单: {sub_path}\n部门汇总: {dept_path}\n"
          f"缺失标注: {miss_path}\n目录对照: {cmp_path}")
    return subjects, missing, orphans


def main():
    ap = argparse.ArgumentParser(description="普陀区预决算采集器")
    ap.add_argument("cmd", choices=["plan", "download", "reindex", "checklist"])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--years", default=None, help="逗号分隔, 覆盖默认年度(按类别分别取)")
    ap.add_argument("--kinds", default="预算,决算")
    ap.add_argument("--no-index-cache", action="store_true")
    ap.add_argument("--year", type=int, default=None, help="checklist 的目标报告年度")
    ap.add_argument("--kind", default="决算", help="checklist 的目标类别: 预算/决算")
    ap.add_argument("--org-json", type=Path, default=None, help="组织目录 JSON, 默认取本仓库 data/organizations.json")
    ap.add_argument("--rebuild-roster", action="store_true")
    args = ap.parse_args()

    cache_dir = args.root / "_采集清单"
    cache_dir.mkdir(parents=True, exist_ok=True)
    idx_path = cache_dir / "index.json"
    if args.no_index_cache or not idx_path.exists():
        chs = channels()
        print(f"栏目 {len(chs)} 个 (主管部门 {len({c['group'] for c in chs})} 个)")
        (cache_dir / "channels.json").write_text(json.dumps(chs, ensure_ascii=False, indent=1))
        items = fetch_items(chs)
        idx_path.write_text(json.dumps(items, ensure_ascii=False, indent=1))
        print(f"条目 {len(items)} 条 -> {idx_path}")
    items = json.loads(idx_path.read_text())

    if args.cmd == "checklist":
        if not args.year:
            ap.error("checklist 需要 --year 指定报告年度, 例如 --year 2025 --kind 决算")
        checklist_main(args, cache_dir, items)
        return

    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()}
    if args.years:
        ys = [int(y) for y in args.years.split(",")]
        budget_years = ys if "预算" in kinds else []
        account_years = ys if "决算" in kinds else []
    else:
        budget_years = list(DEFAULT_BUDGET_YEARS) if "预算" in kinds else []
        account_years = list(DEFAULT_ACCOUNT_YEARS) if "决算" in kinds else []

    files, skipped = build_plan(items, budget_years, account_years)
    if "预算" not in kinds:
        files = [f for f in files if f["kind"] != "预算"]
    if "决算" not in kinds:
        files = [f for f in files if f["kind"] != "决算"]

    per = collections.Counter((f["kind"], f["year"]) for f in files)
    print(f"\n归档计划: {len(files)} 个文件  跳过 {len(skipped)} 条")
    for (kind, year) in sorted(per):
        nf = len({f['folder'] for f in files if (f['kind'], f['year']) == (kind, year)})
        print(f"  {kind} {year}: {per[(kind, year)]:>4} 个文件  {nf:>3} 个部门文件夹")
    reasons = collections.Counter(s["reason"].split(":")[0] for s in skipped)
    print("  跳过原因:", dict(reasons))
    need_extract = sum(1 for f in files if not f["url"])
    if need_extract:
        print(f"  其中 {need_extract} 个需从公示正文提取PDF链接")
    write_manifest(cache_dir / "skipped.csv", [], cache_dir / "skipped.csv", skipped)
    if args.cmd == "download":
        ok, fail = download(files, args.root, args.workers, args.limit)
        print(f"完成 ok={ok} fail={fail}")
        print(f"清单: {cache_dir / 'manifest.csv'}")


if __name__ == "__main__":
    main()
