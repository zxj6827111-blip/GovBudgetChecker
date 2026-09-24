"""从官方样张生成脱敏固定夹具（GPT5.6 R4 P1-5）。

corpus/ 被 .gitignore 排除，干净 checkout 没有样张 PDF——集成测试
用 skipif 会静默跳过，CI 上关键测试形同虚设。本脚本把样张解析产物
（page_texts + page_tables，均为公开决算材料、无敏感信息）序列化到
tests/fixtures/ 供入库，并记录原始 PDF 的 SHA-256 供溯源校验。

用法::

    python scripts/build_sample_fixture.py                     # 默认样张
    python scripts/build_sample_fixture.py \
        --pdf <path> --out tests/fixtures/<name>.json --doc-id <id>

WP4-A（三公经费跨表一致性）需要**另一个**冻结样张：2026-09-16 人工
复核判定的 Y02 漏报（宜川路街道 2025 年度决算，PDF 第 22/24 页）出自
该样张，而不是 corpus 里的生态环境局样张。为避免再写一份近乎重复的
脚本，这里把输入/输出做成可选参数，默认行为完全不变。
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.replay_golden_corpus import load_page_tables, load_page_texts  # noqa: E402

CORPUS_PDF = ROOT / "corpus" / "DOC-20260905-001" / "sample.pdf"
OUT_DIR = ROOT / "tests" / "fixtures"
OUT_PATH = OUT_DIR / "sample_page_data.json"


def build_fixture(pdf: Path, out_path: Path, doc_id: str) -> int:
    if not pdf.exists():
        print(f"样张不存在: {pdf}", file=sys.stderr)
        return 1
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    page_texts = load_page_texts(pdf)
    page_tables = load_page_tables(pdf)
    payload = {
        "doc_id": doc_id,
        "source_pdf_sha256": digest,
        "source_pdf_bytes": pdf.stat().st_size,
        "page_count": len(page_texts),
        "raw_table_count": sum(len(t) for t in page_tables),
        "page_texts": page_texts,
        "page_tables": page_tables,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    try:
        shown = out_path.relative_to(ROOT)
    except ValueError:  # 输出到仓库外（自定义 --out）时按绝对路径显示
        shown = out_path
    print(f"fixture -> {shown}")
    print(f"  source SHA-256: {digest}")
    print(f"  pages: {len(page_texts)}, raw tables: {payload['raw_table_count']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="生成样张解析固定夹具")
    parser.add_argument("--pdf", default=str(CORPUS_PDF), help="源 PDF 路径")
    parser.add_argument("--out", default=str(OUT_PATH), help="输出夹具路径")
    parser.add_argument(
        "--doc-id",
        default="",
        help="夹具 doc_id（默认取源 PDF 所在目录名）",
    )
    args = parser.parse_args()
    pdf = Path(args.pdf)
    doc_id = args.doc_id.strip() or pdf.parent.name
    return build_fixture(pdf, Path(args.out), doc_id)


if __name__ == "__main__":
    raise SystemExit(main())
