"""Business expenditure column names and literal amount names must both retain every money cell."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from src.services.pdf_parser import PDFParser
from scripts import d1_table_reconciliation as d1
import pdfplumber

ap = argparse.ArgumentParser()
ap.add_argument('--fixtures', type=Path, default=Path(__file__).resolve().parents[1] / 'fixtures')
args = ap.parse_args()
data = [['一般公共服务支出', '100', '20.50', '79.50'], ['教育支出', '200', '80.50', '119.50'], ['合计', '300', '101.00', '199.00']]
cases = [('explicit_amount', ['项目', '金额', '金额', '金额']),
         ('business', ['项目', '本年支出合计', '基本支出', '项目支出'])]
results = []
for name, header in cases:
    pdf = (args.fixtures / ('expenditure_header_' + name + '.pdf')).resolve()
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    artifact = PDFParser(None).build_parse_artifact(str(pdf))
    assert len(artifact.logical_tables) == 1
    with pdfplumber.open(pdf) as handle:
        texts = [page.extract_text() or '' for page in handle.pages]
    view, errors = d1._logical_view_with_provenance(d1.doc_for_artifact(artifact, str(pdf), texts), artifact.logical_tables)
    assert [r['cells'] for r in view] == [header] + data, (name, view)
    actual = d1.reconcile_auto(str(pdf))
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == digest
    results.append({'case': name, 'source_sha256': digest, 'expected': {'instances': 1, 'pass': 1, 'gap': 0},
                    'expected_data_rows': 3, 'expected_money_cells': 9, 'parser_rows_equal_independent_expected': True,
                    'actual': actual, 'ok': actual['instances'] == 1 and actual['pass'] == 1 and actual['gap'] == 0})
print(json.dumps(results, ensure_ascii=False, indent=2))
assert all(x['ok'] for x in results), '本年支出合计/基本支出/项目支出是金额栏目，正确的9格解析产物不能因未写金额二字报GAP'
