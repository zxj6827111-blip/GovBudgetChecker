"""Clear PDFs, genuine current PDFParser output, no mutations, independent expected currency-column roles."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd()))
from src.services.pdf_parser import PDFParser
from scripts import d1_table_reconciliation as d1

ap = argparse.ArgumentParser()
ap.add_argument('--fixtures', type=Path, default=Path(__file__).resolve().parents[1] / 'fixtures')
args = ap.parse_args()
results = []
for name in ['decimal', 'mixed']:
    pdf = (args.fixtures / ('mixed_amount_source_' + name + '.pdf')).resolve()
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()
    artifact = PDFParser(None).build_parse_artifact(str(pdf))
    assert len(artifact.logical_tables) == 1
    with patch.object(d1.PDFParser, 'build_parse_artifact', return_value=artifact):
        actual = d1.reconcile_auto(str(pdf))
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == before
    ok = actual['instances'] == 1 and actual['pass'] == 1 and actual['gap'] == 0
    results.append({'case': name, 'source_sha256': before, 'expected': {'instances': 1, 'pass': 1, 'gap': 0},
                    'expected_data_rows': 3, 'expected_money_cells': 6, 'unmodified_current_parser_artifact': True,
                    'actual': actual, 'ok': ok})
print(json.dumps(results, ensure_ascii=False, indent=2))
assert all(r['ok'] for r in results), '两个原件的六个金额格均应完整匹配；整数格式不能把金额列变成科目编码列并制造GAP'
