#!/usr/bin/env python
"""Benchmark 材料登记（WP4-I S1 工具 1）。

把一份政府预决算公开 PDF 登记进第一期基准语料（DOC-B1-###）：

- 生成 ``corpus/DOC-B1-###/`` 目录并把 PDF 落位为 ``sample.pdf``
  （PDF 永不入 git，靠 .gitignore 的 ``corpus/*`` 忽略规则保证）；
- 计算 sha256 并做**重复源拒绝**（同一 PDF 登记两次直接失败，防错源
  沿用 R8 P1 的绑定思想）；
- 探测页数与文本层字符量（复用 replay_golden_corpus 的 pdfplumber 装载，
  文本层缺失的登记为 scan-watch 观察集候选）；
- 向 ``corpus/manifest.csv`` 追加一行（沿用 GOLDEN_CORPUS_TEMPLATE 字段
  并扩展 report_kind_true / source_url / subset / depth）。

登记 ≠ 入库标注：golden.json 由人工盲标完成后另行放入（S3）；登记只
保证「材料身份 + 来源 + 子集」可追溯。

用法：
    python scripts/bench_register.py --pdf path/to/材料.pdf \
        --report-kind-true final --subset final-main --source-url https://...
    python scripts/bench_register.py --list
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.replay_golden_corpus import load_page_texts  # noqa: E402

CORPUS_DIR = _REPO_ROOT / "corpus"
MANIFEST_PATH = CORPUS_DIR / "manifest.csv"

#: manifest.csv 表头（GOLDEN_CORPUS_TEMPLATE 字段 + WP4-I 扩展列）
MANIFEST_FIELDS = [
    "doc_id",
    "sha256",
    "report_kind_true",
    "subset",
    "region",
    "depth",
    "source_url",
    "pages",
    "text_chars",
    "registered_at",
    "notes",
]

#: 合法子集标记（WP4-I 方案 §2.5；region 是 锚定/探针 的分层键）
#: 合法子集标记。
#: 前 5 个来自 WP4-I 方案 §2.5 的枚举；``budget-unit`` 是**本项目的显式扩展**——
#: 单位级预算与部门级预算粒度不同、且普陀区单位预算为同一模板（核心表标题命中集合
#: 完全相同），混入 budget-main 会让该层的规则级 precision 被样本相关性带偏。
#: 分层报告时 budget-unit 单列，不与部门级混算。
SUBSETS = (
    "final-main",
    "budget-main",
    "budget-unit",
    "scan-watch",
    "clean-contrast",
    "probe-region",
)
REGIONS = ("anchor", "probe")
DEPTHS = ("", "L1", "L2")
KINDS = ("final", "budget", "unknown")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(manifest_path: Path = MANIFEST_PATH) -> List[dict]:
    if not manifest_path.exists():
        return []
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def next_doc_id(manifest_path: Path = MANIFEST_PATH) -> str:
    """下一个 DOC-B1-### 编号：按现存 manifest 行与目录号取最大 +1。

    目录扫描以 manifest 所在目录为准（支持测试/多语料根），不读模块常量。
    """
    used = set()
    corpus_dir = manifest_path.parent
    for row in load_manifest(manifest_path):
        doc_id = str(row.get("doc_id") or "")
        if doc_id.startswith("DOC-B1-"):
            used.add(doc_id)
    if corpus_dir.exists():
        for entry in corpus_dir.iterdir():
            if entry.is_dir() and entry.name.startswith("DOC-B1-"):
                used.add(entry.name)
    seq = 0
    for doc_id in used:
        try:
            seq = max(seq, int(doc_id.split("-", 2)[2]))
        except (IndexError, ValueError):
            continue
    return f"DOC-B1-{seq + 1:03d}"


def engine_fingerprint() -> dict:
    """引擎指纹（WP4-I §6.7：评测报告须绑定 doc_id + sha256 + **引擎指纹**）。

    两个互补维度：
    - ``rules_sha256``：三个规则注册表（final/budget/common）的「规则码:严重度」
      排序后哈希。与 git 无关——worktree、打包分发、无 .git 环境都能得到同一值；
      规则集一变指纹就变，这正是「换了 checker 要能归因」的要点。
    - ``git_head`` / ``git_dirty``：本次测量的代码版本与工作树是否干净。
      历史快照若产自未提交的工作树，这里会如实带 dirty=True。
    """
    import hashlib
    import subprocess

    from src.engine.budget_rules import ALL_BUDGET_RULES
    from src.engine.common_rules import ALL_COMMON_RULES
    from src.engine.rules_v33 import ALL_RULES as FINAL_ALL_RULES

    parts = []
    rule_count = 0
    for tag, rules in (
        ("final", FINAL_ALL_RULES),
        ("budget", ALL_BUDGET_RULES),
        ("common", ALL_COMMON_RULES),
    ):
        codes = sorted(
            f"{getattr(item, 'code', '')}:{getattr(item, 'severity', '')}" for item in rules
        )
        rule_count += len(codes)
        parts.append(f"{tag}=" + ",".join(codes))
    inventory = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

    # 行为指纹必须哈希**引擎源码**：只哈希「规则码:严重度」清单对本次改造
    # （MR-1 双栏取数 / MR-2 舍入聚类 / MR-4 口径门槛）完全不敏感——三种引擎
    # 会算出同一个清单哈希，指纹随之失去归因能力（实测踩过：三节点同为 7241a74a）。
    source = hashlib.sha256()
    for rel in (
        "src/engine/rules_v33.py",
        "src/engine/budget_rules.py",
        "src/engine/common_rules.py",
        "src/engine/pipeline.py",
    ):
        path = _REPO_ROOT / rel
        source.update(rel.encode("utf-8"))
        source.update(path.read_bytes() if path.exists() else b"<missing>")
    digest = source.hexdigest()

    head = ""
    dirty = False
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, check=False,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_REPO_ROOT, capture_output=True, text=True, check=False,
        ).stdout.strip()
        dirty = bool(status)
    except OSError:
        pass  # 无 git 环境：指纹仍有 rules_sha256 可用
    return {
        "rules_sha256": digest,
        "rule_inventory_sha256": inventory,
        "rules_count": rule_count,
        "git_head": head,
        "git_dirty": dirty,
    }


def probe_pdf(pdf_path: Path) -> dict:
    """页数与文本层探测（复用 replay_golden_corpus 的 pdfplumber 装载）。"""
    texts = load_page_texts(pdf_path)
    return {
        "pages": len(texts),
        "text_chars": sum(len((t or "").strip()) for t in texts),
    }


def register_pdf(
    pdf_path: Path,
    *,
    report_kind_true: str,
    subset: str,
    region: str = "anchor",
    depth: str = "",
    source_url: str = "",
    notes: str = "",
    doc_id: Optional[str] = None,
    corpus_dir: Path = CORPUS_DIR,
    manifest_path: Path = MANIFEST_PATH,
) -> dict:
    """登记一份 PDF；成功返回登记信息，违反准入直接抛 SystemExit。

    ``doc_id`` 用于把**既有 golden 语料**（如 DOC-20260905-001）补登进
    manifest：目录已存在时要求其 golden.json 声明的 sha256 与 PDF 一致
    （同一 fail-closed 绑定），编号冲突与重复 SHA 拒绝规则不变。
    """
    pdf_path = pdf_path.resolve()
    if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
        raise SystemExit(f"待登记文件不存在或不是 PDF: {pdf_path}")
    if report_kind_true not in KINDS:
        raise SystemExit(f"report-kind-true 必须是 {KINDS} 之一: {report_kind_true}")
    if subset not in SUBSETS:
        raise SystemExit(f"subset 必须是 {SUBSETS} 之一: {subset}")
    if region not in REGIONS:
        raise SystemExit(f"region 必须是 {REGIONS} 之一: {region}")
    if depth not in DEPTHS:
        raise SystemExit(f"depth 只允许空/L1/L2: {depth}")

    sha = sha256_file(pdf_path)
    for row in load_manifest(manifest_path):
        if str(row.get("sha256") or "") == sha:
            raise SystemExit(
                f"重复登记拒绝：该 PDF 的 sha256 已登记为 {row.get('doc_id')}"
                "（同一材料只允许一个 DOC-ID，防错源）"
            )

    if doc_id:
        doc_id = str(doc_id).strip()
        if not doc_id.startswith("DOC-"):
            raise SystemExit(f"doc-id 必须以 DOC- 开头: {doc_id}")
        doc_dir = corpus_dir / doc_id
        if doc_dir.exists():
            golden_path = doc_dir / "golden.json"
            if golden_path.exists():
                golden_sha = str(
                    json.loads(golden_path.read_text(encoding="utf-8")).get("sha256") or ""
                ).strip()
                if golden_sha and golden_sha != sha:
                    raise SystemExit(
                        f"{doc_id} 的 golden.json 声明 sha256={golden_sha[:12]}…，"
                        f"与待登记 PDF {sha[:12]}… 不一致——拒绝登记（fail-closed）"
                    )
            target_pdf = doc_dir / "sample.pdf"
            if not target_pdf.exists():
                shutil.copyfile(pdf_path, target_pdf)
        else:
            doc_dir.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(pdf_path, doc_dir / "sample.pdf")
    else:
        doc_id = next_doc_id(manifest_path)
        doc_dir = corpus_dir / doc_id
        if doc_dir.exists():
            raise SystemExit(f"目录已存在（编号冲突）: {doc_dir}")
        doc_dir.mkdir(parents=True, exist_ok=False)
        # PDF 永不入 git：corpus/* 整体忽略（见 .gitignore），这里只是本机落位
        shutil.copyfile(pdf_path, doc_dir / "sample.pdf")

    probe = probe_pdf(doc_dir / "sample.pdf")
    row = {
        "doc_id": doc_id,
        "sha256": sha,
        "report_kind_true": report_kind_true,
        "subset": subset,
        "region": region,
        "depth": depth,
        "source_url": source_url,
        "pages": probe["pages"],
        "text_chars": probe["text_chars"],
        "registered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "notes": notes,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = manifest_path.exists()
    with manifest_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", help="待登记 PDF 路径")
    parser.add_argument(
        "--doc-id",
        default=None,
        help="指定 DOC-ID（为既有 golden 语料补登 manifest 行时使用）",
    )
    parser.add_argument("--report-kind-true", choices=KINDS, help="人工确认的真实文种")
    parser.add_argument("--subset", choices=SUBSETS, help="子集标记")
    parser.add_argument("--region", choices=REGIONS, default="anchor", help="锚定/探针分层")
    parser.add_argument("--depth", default="", help="标注深度 L1/L2（登记时可空）")
    parser.add_argument("--source-url", default="", help="公开来源 URL/渠道")
    parser.add_argument("--notes", default="", help="备注")
    parser.add_argument("--list", action="store_true", help="列出已登记语料")
    args = parser.parse_args()

    if args.list:
        rows = load_manifest()
        if not rows:
            print("（manifest 为空，尚无登记语料）")
            return 0
        for row in rows:
            print(
                f"{row.get('doc_id')}\t{row.get('report_kind_true')}\t"
                f"{row.get('subset')}\t{row.get('region')}\t{row.get('sha256')[:12]}…"
            )
        return 0

    if not args.pdf or not args.report_kind_true or not args.subset:
        parser.error("登记需要 --pdf、--report-kind-true、--subset（--list 除外）")

    row = register_pdf(
        Path(args.pdf),
        report_kind_true=args.report_kind_true,
        subset=args.subset,
        region=args.region,
        depth=args.depth,
        source_url=args.source_url,
        notes=args.notes,
        doc_id=args.doc_id,
    )
    print(
        f"registered: {row['doc_id']}  sha256={row['sha256'][:12]}…  "
        f"pages={row['pages']}  text_chars={row['text_chars']}"
    )
    if int(row["text_chars"] or 0) < 200:
        print(
            "警告：文本层字符量过少，疑似扫描件——建议子集标记改为 scan-watch "
            "（观察集不进主指标）"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
