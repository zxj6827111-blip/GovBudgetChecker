"""IR-16b 方案一：用原生文字层当免费真值，测 OCR 的字/数保真度。

不新增人工标注。对每一页：
  1) 用 pymupdf 取原生文字层的词与坐标（这就是"答案"）；
  2) 找出金额最密集的 700px 像素带，只把这一带送去 OCR（与 IR-16 相同的稳定裁图路径）；
  3) 把答案裁到同一像素带，与 OCR 输出按 token 对照，产出命中/错数/漏数/错字/多识别。

输出为估计值而非绝对真值：文字层本身也可能与原件不符，且不覆盖列身份。
比较器自带负例自检，改坏一个数字必须能被抓出来。
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from unicodedata import normalize

import httpx
import pymupdf
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_CORPUS = Path.home() / "Desktop/决算测试上海市"
DEFAULT_INTAKE = ROOT / "outputs/intake_remediation_20261024/batch2_final/intake.jsonl.gz"
DEFAULT_OUT = ROOT / "outputs/intake_remediation_20261024/ir16b_textlayer_oracle"
AMOUNT = re.compile(r"\d[\d,]*(?:\.\d+)?|\.\d+")
CJK = re.compile(r"[一-鿿]")
SEED = 20261025


def norm(value: str) -> str:
    return normalize("NFKC", str(value or "")).replace("，", ",")


def norm_chars(value: str) -> str:
    """只用于汉字统计：汉字之间不留空格，避免分词差异影响字符计数。"""
    return norm(value).replace(" ", "")


VALID_MONEY = re.compile(r"^-?\d{1,3}(?:,\d{3})+(?:\.\d+)?$|^-?\d+(?:\.\d+)?$")
JOIN_COMMA = re.compile(r"(\d)\s*,\s*(?=\d)")
JOIN_DOT = re.compile(r"(\d)\s*\.\s*(?=\d)")


def defuse_ocr_spaces(text: str) -> str:
    """OCR 常在千分位后塞空格（'174, 757.49'）；先并回一个 token，
    否则会把'读对但带空格'误判成漏读。只并在逗号/小数点两侧紧邻的数字，
    不并纯空格分隔的两个数，避免把两列粘成一个。"""
    text = JOIN_COMMA.sub(r"\1,", text)
    return JOIN_DOT.sub(r"\1.", text)


def money_value(token: str) -> float | None:
    """只有写法合法的金额才给数值；'33,346,70' 这类分组错的返回 None。"""
    if not VALID_MONEY.match(token):
        return None
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def digit_run(token: str) -> str:
    return "".join(re.findall(r"\d+", token))


def amount_tokens(text: str) -> list[str]:
    out = []
    for piece in defuse_ocr_spaces(norm(text)).split():
        for raw in AMOUNT.findall(piece):
            cleaned = raw.strip(".,")
            if cleaned and re.search(r"\d", cleaned):
                out.append(cleaned)
    return out


def hamming(a: str, b: str) -> int:
    return sum(1 for x, y in zip(a, b, strict=False) if x != y)


def levenshtein(a: str, b: str, limit: int = 2) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


@dataclass
class Compare:
    oracle_amounts: int = 0
    exact: int = 0
    soft: int = 0          # 数值相同，只是逗号/小数位写法差异
    delimiter: int = 0     # 数字串相同但数值变了（小数点/千分位读错）——危险
    misread: int = 0       # 数字被读错
    missing: int = 0       # 完全没读到
    spurious: int = 0      # OCR 多出、文字层没有的数字
    malformed: int = 0      # 多出的数字本身写法就不合法
    zero: dict = field(default_factory=lambda: {"oracle": 0, "exact": 0, "soft": 0,
                                                 "delimiter": 0, "misread": 0, "missing": 0})
    nonzero: dict = field(default_factory=lambda: {"oracle": 0, "exact": 0, "soft": 0,
                                                    "delimiter": 0, "misread": 0, "missing": 0})
    oracle_chars: int = 0
    char_hit: int = 0
    char_extra: int = 0
    details: list[dict] = field(default_factory=list)

    def tally(self, token: str, outcome: str) -> None:
        bucket = self.zero if is_zero_value(token) else self.nonzero
        bucket["oracle"] += 1
        if outcome in bucket:
            bucket[outcome] += 1

    def as_rates(self) -> dict:
        a = max(1, self.oracle_amounts)
        c = max(1, self.oracle_chars)
        return {
            "amount_exact_rate": round(self.exact / a, 4),
            "amount_soft_rate": round(self.soft / a, 4),
            "amount_delimiter_rate": round(self.delimiter / a, 4),
            "amount_misread_rate": round(self.misread / a, 4),
            "amount_missing_rate": round(self.missing / a, 4),
            "char_hit_rate": round(self.char_hit / c, 4),
            "char_extra_per_page": self.char_extra,
        }


def compare_tokens(oracle_amounts: list[str], ocr_amounts: list[str]) -> tuple[Counter, Compare]:
    """多重集对照：精确 → 数值相同(仅写法差异) → 数字串相同但数值变了(分隔符错)
    → 近似(读错数) → 漏读。分隔符错单独计，因为 '33,346.70' 读成 '33,346,70' 是记错数，
    不能当格式差异放过。"""
    stat = Compare(oracle_amounts=len(oracle_amounts))
    remaining_ocr = Counter(ocr_amounts)
    unmatched: list[str] = []
    for token in oracle_amounts:
        if remaining_ocr[token]:
            remaining_ocr[token] -= 1
            stat.exact += 1
            stat.tally(token, "exact")
            continue
        unmatched.append(token)
    by_run: dict[str, list[str]] = {}
    for token, count in remaining_ocr.items():
        for _ in range(max(0, count)):
            by_run.setdefault(digit_run(token), []).append(token)
    hard: list[str] = []
    for token in unmatched:
        value = money_value(token)
        same_value = next((o for o in by_run.get(digit_run(token), []) if money_value(o) == value and value is not None), None)
        if same_value is not None:
            by_run[digit_run(token)].remove(same_value)
            remaining_ocr[same_value] -= 1
            stat.soft += 1
            stat.tally(token, "soft")
            stat.details.append({"kind": "soft", "oracle": token, "ocr": same_value})
            continue
        same_run = next((o for o in by_run.get(digit_run(token), []) if money_value(o) != value), None)
        if same_run is not None:
            by_run[digit_run(token)].remove(same_run)
            remaining_ocr[same_run] -= 1
            stat.delimiter += 1
            stat.tally(token, "delimiter")
            stat.details.append({"kind": "delimiter", "oracle": token, "ocr": same_run,
                                 "oracle_value": value, "ocr_value": money_value(same_run)})
            continue
        hard.append(token)
    pool = [t for t, n in remaining_ocr.items() for _ in range(max(0, n))]
    for token in hard:
        best, best_cost = None, 99
        for cand in pool:
            if len(digit_run(cand)) == len(digit_run(token)):
                cost = hamming(digit_run(cand), digit_run(token))
                if cost < best_cost:
                    best, best_cost = cand, cost
            cost = levenshtein(cand, token)
            if cost < best_cost:
                best, best_cost = cand, cost
        if best is not None and best_cost <= 2:
            pool.remove(best)
            remaining_ocr[best] -= 1
            stat.misread += 1
            stat.tally(token, "misread")
            stat.details.append({"kind": "misread", "oracle": token, "ocr": best, "cost": best_cost})
        else:
            stat.missing += 1
            stat.tally(token, "missing")
            stat.details.append({"kind": "missing", "oracle": token})
    stat.spurious = sum(max(0, n) for n in remaining_ocr.values())
    stat.malformed = sum(max(0, n) for token, n in remaining_ocr.items() if money_value(token) is None)
    return remaining_ocr, stat


def compare_chars(oracle_text: str, ocr_text: str) -> tuple[int, int, int]:
    oc, cc = Counter(CJK.findall(norm_chars(oracle_text))), Counter(CJK.findall(norm_chars(ocr_text)))
    hit = sum(min(oc[k], cc[k]) for k in oc)
    extra = sum(max(0, cc[k] - oc.get(k, 0)) for k in cc)
    return sum(oc.values()), hit, extra


def words_in_band(page, y0_pt: float, y1_pt: float) -> list[str]:
    """只取整词落在带内（含 1pt 容差）的文字，避免跨界词被裁一半造成假错读。"""
    return [w[4] for w in page.get_text("words") if y0_pt - 1 <= w[1] and w[3] <= y1_pt + 1]


def is_zero_value(token: str) -> bool:
    try:
        return float(token.replace(",", "")) == 0.0
    except ValueError:
        return False


def pick_band(page, dpi: int, band_px: int) -> tuple[int, int, int, int]:
    """选金额最密集的像素带；返回 (y0_px, y1_px, 词数, 金额 token 数)。"""
    zoom = dpi / 72.0
    band_pt = band_px / zoom
    height = float(page.rect.height)
    tops = [0.0] if height <= band_pt else [
        min(pt, height - band_pt) for pt in [band_pt * i / 2 for i in range(int(height / band_pt) * 2 + 2)]
        if min(pt, height - band_pt) >= 0
    ]
    seen_tops, best = set(), (0, band_px, 0, -1)
    for top in tops:
        if round(top, 2) in seen_tops:
            continue
        seen_tops.add(round(top, 2))
        bottom = top + band_pt
        words = words_in_band(page, top, bottom)
        amounts = sum(len(amount_tokens(w)) for w in words)
        if amounts > best[3]:
            best = (int(top * zoom), min(int(height * zoom), int(bottom * zoom)), len(words), amounts)
    return best


def collect_candidates(corpus: Path, intake: Path, want: int) -> list[dict]:
    seen, rows = set(), []
    with gzip.open(intake, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            sha = str(row.get("sha256") or "")
            if not sha or sha in seen or int(row.get("pages") or 0) < 3:
                continue
            seen.add(sha)
            rows.append(row)
    by_year: dict[str, list[dict]] = {}
    for row in rows:
        by_year.setdefault(str(row["file"]).split("/")[0], []).append(row)
    picked: list[dict] = []
    years = sorted(by_year)
    per = max(1, want // max(1, len(years)))
    for year in years:
        bucket = sorted(by_year[year], key=lambda r: hashlib.sha256(str(r["file"]).encode()).hexdigest())
        for row in bucket:
            path = corpus / row["file"]
            if not path.is_file():
                continue
            try:
                with pymupdf.open(path) as pdf:
                    for page_no in range(1, min(len(pdf), 20) + 1):
                        page = pdf[page_no - 1]
                        if len(page.get_text().strip()) < 120:
                            continue
                        y0, y1, words, amounts = pick_band(page, 150, 700)
                        if amounts >= 8 and words >= 6:
                            picked.append({"file": row["file"], "page": page_no, "year": year,
                                           "band": [y0, y1], "band_amounts": amounts})
                            break
            except Exception:
                continue
            if sum(1 for p in picked if p["year"] == year) >= per:
                break
    picked.sort(key=lambda p: hashlib.sha256(f"{p['file']}{p['page']}".encode()).hexdigest())
    return picked[:want]


async def wait_healthy(budget: float = 180.0) -> dict:
    """OCR 服务不健康时等它恢复，不硬闯；共享服务被打崩过一次，不能再堆请求。"""
    from src.services.ocr_client import check_ocr_health
    deadline = time.monotonic() + budget
    health: dict = {}
    while True:
        health = await check_ocr_health()
        if health.get("ok"):
            os.environ["OCR_MODEL_FINGERPRINT"] = str(health.get("model_fingerprint") or "")
            return health
        if time.monotonic() >= deadline:
            raise RuntimeError(f"OCR service not ready: {health}")
        print(json.dumps({"wait_for_ocr": str(health.get("error"))[:200]}, ensure_ascii=False), flush=True)
        await asyncio.sleep(10)


async def measure_page(item: dict, *, corpus: Path, dpi: int, band_px: int, out_dir: Path) -> dict:
    from src.services.input_geometry import Affine
    from src.services.ocr_client import ocr_page
    from src.services.ocr_tiles import make_tile

    path = corpus / item["file"]
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    with pymupdf.open(path) as pdf:
        page = pdf[item["page"] - 1]
        y0, y1, _, band_amounts = pick_band(page, dpi, band_px)
        zoom = dpi / 72.0
        words = words_in_band(page, y0 / zoom, y1 / zoom)
        oracle_text = " ".join(words)
        pix = page.get_pixmap(dpi=dpi)
        png = pix.tobytes("png")
        image = Image.open(io.BytesIO(png)).convert("RGB")
        box = (0, y0, image.width, min(image.height, y1))
        request = make_tile(
            image, box,
            parent_sha=hashlib.sha256(png).hexdigest(), file_hash=file_hash,
            page=item["page"], pixel_to_pdf=Affine(a=72.0 / dpi, d=72.0 / dpi, e=0.0, f=y0 * 72.0 / dpi),
            inference_mode="text",
        )
    health = await wait_healthy()
    directory = out_dir / f"{file_hash[:12]}-p{item['page']}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "crop.png").write_bytes(request.image)
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=240) as client:
        result = await ocr_page(client, image_png=request.image, page_number=item["page"],
                                timeout_seconds=240, tile_context=request.context, region_only=True)
    elapsed = round(time.monotonic() - started, 2)
    blocks = [str(getattr(b, "text", "") or "") for b in (getattr(result, "blocks", []) or [])]
    ocr_text = "\n".join(blocks)
    (directory / "response.json").write_text(
        json.dumps({"ocr_text": ocr_text, "result": result.to_dict() if hasattr(result, "to_dict") else {}},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    _, stat = compare_tokens(amount_tokens(oracle_text), amount_tokens(ocr_text))
    total, hit, extra = compare_chars(oracle_text, ocr_text)
    stat.oracle_chars, stat.char_hit, stat.char_extra = total, hit, extra
    record = {
        "file": item["file"], "physical_page": item["page"], "sha256": file_hash,
        "band_px": [y0, y1], "band_amount_tokens": band_amounts,
        "ok": bool(getattr(result, "ok", True)), "elapsed_seconds": elapsed,
        "model_fingerprint": str(health.get("model_fingerprint") or ""),
        "oracle_text_chars": len(oracle_text), "ocr_text_chars": len(ocr_text),
        "amounts": {"oracle": stat.oracle_amounts, "exact": stat.exact, "soft": stat.soft,
                    "delimiter": stat.delimiter, "misread": stat.misread, "missing": stat.missing,
                    "spurious": stat.spurious, "malformed": stat.malformed},
        "amounts_zero": dict(stat.zero), "amounts_nonzero": dict(stat.nonzero),
        "chars": {"oracle": total, "hit": hit, "extra": extra},
        "rates": stat.as_rates(),
        "mismatch_details": stat.details[:40],
    }
    (directory / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def self_check_comparator() -> dict:
    """比较器负例自检：oracle 与自身必须满分，改一个数字/丢一个金额必须被抓出来。"""
    oracle = ["1,234.56", "78.00", "0.35", "合计", "9,999.00"]
    text = " ".join(oracle)
    base, stat = compare_tokens(amount_tokens(text), amount_tokens(text))
    perfect = stat.exact == 4 and stat.misread == 0 and stat.missing == 0 and stat.spurious == 0
    _, changed = compare_tokens(amount_tokens(text), amount_tokens(text.replace("1,234.56", "1,234.57")))
    catches_digit = changed.misread == 1 and changed.exact == 3
    _, dropped = compare_tokens(amount_tokens(text), amount_tokens(text.replace("0.35", "")))
    catches_drop = dropped.missing == 1
    _, comma = compare_tokens(amount_tokens(text), amount_tokens(text.replace("1,234.56", "1234.56")))
    comma_is_soft = comma.soft == 1 and comma.misread == 0
    c_total, c_hit, _ = compare_chars("人员经费", "人员经费")
    c_total2, c_hit2, _ = compare_chars("人员经费", "人员经货")
    catches_char = c_total == c_hit == 4 and c_total2 == 4 and c_hit2 == 3
    # 旧缺陷反向断言：空格是列边界，两列金额绝不能粘成一个数。
    glue_free = amount_tokens("13,333.01 1,295.73 2080502") == ["13,333.01", "1,295.73", "2080502"]
    # 实测到的两种 OCR 输出，比较器必须分得清：带空格但读对 = 命中；小数点读成逗号 = 记错数。
    _, spaced = compare_tokens(amount_tokens("174,757.49 58,849.85"),
                               amount_tokens("174, 757.49\n58, 849. 85"))
    spaced_is_hit = spaced.exact == 2 and spaced.missing == 0 and spaced.spurious == 0
    _, slipped = compare_tokens(amount_tokens("33,346.70"), amount_tokens("33, 346, 70"))
    catches_decimal_slip = slipped.delimiter == 1 and slipped.soft == 0 and slipped.exact == 0
    return {"self_match_perfect": perfect, "catches_digit_change": catches_digit,
            "catches_missing_amount": catches_drop, "comma_is_soft_not_error": comma_is_soft,
            "catches_char_change": catches_char, "columns_not_glued": glue_free,
            "spaced_number_still_counts": spaced_is_hit, "decimal_slip_is_error": catches_decimal_slip,
            "all_passed": all([perfect, catches_digit, catches_drop, comma_is_soft, catches_char,
                               glue_free, spaced_is_hit, catches_decimal_slip])}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--intake", type=Path, default=DEFAULT_INTAKE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--pages", type=int, default=24)
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--band-height", type=int, default=700)
    parser.add_argument("--sleep", type=float, default=3.0, help="两次 OCR 请求之间的间隔秒数")
    parser.add_argument("--resume", action="store_true", help="沿用 out 目录里已成功的页，只补缺的部分")
    args = parser.parse_args()

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    checks = self_check_comparator()
    print(json.dumps({"comparator_self_check": checks}, ensure_ascii=False))
    if not checks["all_passed"]:
        raise SystemExit("comparator self-check failed; refusing to report rates")

    candidates = collect_candidates(args.corpus, args.intake, args.pages)
    (out_dir / "sampling.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"sampled_pages": len(candidates),
                      "years": sorted({c["year"] for c in candidates})}, ensure_ascii=False))

    records: list[dict] = []
    if args.resume and (out_dir / "results.json").is_file():
        previous = json.loads((out_dir / "results.json").read_text(encoding="utf-8")).get("records", [])
        records = [r for r in previous if r.get("ok") and r.get("amounts")]
        print(json.dumps({"resumed_ok_pages": len(records)}, ensure_ascii=False))
    done_keys = {(r["file"], r["physical_page"]) for r in records}
    not_ready = 0
    for item in candidates:
        if (item["file"], item["page"]) in done_keys:
            continue
        await asyncio.sleep(args.sleep)
        try:
            record = await measure_page(item, corpus=args.corpus, dpi=args.dpi,
                                        band_px=args.band_height, out_dir=out_dir)
        except Exception as exc:
            record = {"file": item["file"], "physical_page": item["page"], "ok": False,
                      "error": repr(exc)[:400]}
            if "not ready" in record["error"]:
                not_ready += 1
        records.append(record)
        a = record.get("amounts", {})
        print(json.dumps({"file": Path(str(record.get('file'))).name, "page": record.get("physical_page"),
                          "ok": record.get("ok"), "amounts": a,
                          "char_hit": (record.get("chars") or {}).get("hit"),
                          "oracle_chars": (record.get("chars") or {}).get("oracle"),
                          "elapsed": record.get("elapsed_seconds"),
                          "error": record.get("error")}, ensure_ascii=False), flush=True)
        (out_dir / "results.json").write_text(
            json.dumps({"records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
        if not_ready >= 2:
            print(json.dumps({"aborted": "OCR 服务连续两次未在 180 秒内恢复，停止采集以免压垮共享服务"},
                             ensure_ascii=False), flush=True)
            break

    done = [r for r in records if r.get("ok") and r.get("amounts")]
    tot = {k: sum(r["amounts"][k] for r in done) for k in
           ("oracle", "exact", "soft", "delimiter", "misread", "missing", "spurious", "malformed")}
    chars = {k: sum(r["chars"][k] for r in done) for k in ("oracle", "hit", "extra")}
    kinds = ("oracle", "exact", "soft", "delimiter", "misread", "missing")
    zero = {k: sum(r.get("amounts_zero", {}).get(k, 0) for r in done) for k in kinds}
    nz = {k: sum(r.get("amounts_nonzero", {}).get(k, 0) for r in done) for k in kinds}
    def bucket_rates(b: dict) -> dict:
        n = max(1, b["oracle"])
        return {"tokens": b["oracle"],
                "exact": round(b["exact"] / n, 4),
                "soft_format_only": round(b["soft"] / n, 4),
                "delimiter_error": round(b["delimiter"] / n, 4),
                "misread": round(b["misread"] / n, 4),
                "missing": round(b["missing"] / n, 4)}
    def gap(r: dict) -> int:
        return (r["amounts"]["delimiter"] + r["amounts"]["misread"]
                + r["amounts"]["missing"] + r["amounts"]["soft"])
    worst = sorted(done, key=gap, reverse=True)[:8]
    summary = {
        "method": "原生文字层当答案，同一像素带送 OCR，token 级多重集对照",
        "comparator_self_check": checks,
        "pages_sampled": len(candidates),
        "pages_ok": len(done),
        "pages_failed": sum(1 for r in records if not (r.get("ok") and r.get("amounts"))),
        "dpi": args.dpi,
        "model_fingerprints": sorted({str(r.get("model_fingerprint")) for r in done}),
        "band_height_px": args.band_height,
        "amount_tokens": tot,
        "amount_rates": {
            "exact": round(tot["exact"] / max(1, tot["oracle"]), 4),
            "soft_format_only": round(tot["soft"] / max(1, tot["oracle"]), 4),
            "delimiter_error": round(tot["delimiter"] / max(1, tot["oracle"]), 4),
            "misread": round(tot["misread"] / max(1, tot["oracle"]), 4),
            "missing": round(tot["missing"] / max(1, tot["oracle"]), 4),
            "spurious_per_page": round(tot["spurious"] / max(1, len(done)), 2),
            "malformed_per_page": round(tot["malformed"] / max(1, len(done)), 2),
        },
        "headline_value_wrong_rate": round(
            (tot["delimiter"] + tot["misread"]) / max(1, tot["oracle"]), 4),
        "amount_rates_nonzero_only": bucket_rates(nz),
        "amount_rates_zero_only": bucket_rates(zero),
        "pages_perfect": sum(1 for r in done if gap(r) == 0 and r["amounts"]["spurious"] == 0),
        "pages_with_mismatch": sum(1 for r in done if gap(r) > 0 or r["amounts"]["spurious"] > 0),
        "worst_pages": [{"file": Path(str(r["file"])).name, "page": r["physical_page"],
                         "amounts": r["amounts"], "details": r.get("mismatch_details", [])[:6]}
                        for r in worst if gap(r) > 0],
        "char_tokens": chars,
        "char_hit_rate": round(chars["hit"] / max(1, chars["oracle"]), 4),
        "elapsed_seconds_total": round(sum(float(r.get("elapsed_seconds") or 0) for r in done), 1),
        "median_elapsed_seconds": round(sorted(float(r.get("elapsed_seconds") or 0) for r in done)[len(done) // 2], 2) if done else None,
        "limits": [
            "文字层不是绝对真值：它只是原生 PDF 内嵌的向量文字，个别导出也可能与原件不符。",
            "只覆盖每页金额最密集的 1 个像素带，不是整页，也不是随机页位置。",
            "测的是字符/数字保真度，不测列身份与行归属，因此不能替代 IR-18 的结构真值。",
            "扫描页（无文字层）无法用本方法测量；本方法只覆盖有文字层的页面。",
        ],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
