"""从官方样张生成脱敏固定夹具（GPT5.6 R4 P1-5）。

corpus/ 被 .gitignore 排除，干净 checkout 没有样张 PDF——集成测试
用 skipif 会静默跳过，CI 上关键测试形同虚设。本脚本把样张解析产物
（page_texts + page_tables，均为公开决算材料、无敏感信息）序列化到
tests/fixtures/ 供入库，并记录原始 PDF 的 SHA-256 供溯源校验。

用法: python scripts/build_sample_fixture.py
"""

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


def main() -> int:
    if not CORPUS_PDF.exists():
        print(f"样张不存在: {CORPUS_PDF}", file=sys.stderr)
        return 1
    digest = hashlib.sha256(CORPUS_PDF.read_bytes()).hexdigest()
    page_texts = load_page_texts(CORPUS_PDF)
    page_tables = load_page_tables(CORPUS_PDF)
    payload = {
        "source_pdf_sha256": digest,
        "source_pdf_bytes": CORPUS_PDF.stat().st_size,
        "page_count": len(page_texts),
        "raw_table_count": sum(len(t) for t in page_tables),
        "page_texts": page_texts,
        "page_tables": page_tables,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    print(f"fixture -> {OUT_PATH.relative_to(ROOT)}")
    print(f"  source SHA-256: {digest}")
    print(f"  pages: {len(page_texts)}, raw tables: {payload['raw_table_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
