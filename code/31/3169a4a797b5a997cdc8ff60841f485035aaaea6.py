"""A genuine current PDFParser artifact, controlled one-cell corruption, immutable PDF."""
import argparse
import copy
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path.cwd()))
from src.services.pdf_parser import PDFParser
from scripts import d1_table_reconciliation as d1

parser = argparse.ArgumentParser()
parser.add_argument('--pdf', type=Path, default=Path(__file__).resolve().parents[1] / 'fixtures/year_amount_source.pdf')
args = parser.parse_args()
pdf = args.pdf.resolve()
digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
artifact = PDFParser(None).build_parse_artifact(str(pdf))
assert len(artifact.logical_tables) == 1
with patch.object(d1.PDFParser, 'build_parse_artifact', return_value=artifact):
    normal = d1.reconcile_auto(str(pdf))
corrupt = copy.deepcopy(artifact)
positions = set()
for fragment in list(corrupt.table_fragments) + list(corrupt.physical_fragments):
    for row_index, row in enumerate(fragment.rows):
        if len(row) >= 4 and row[0] == '年初结转和结余' and row[2] == '年末结转和结余':
            assert row[1] in {'2025', '2026'}
            row[1] = '2026'
            positions.add((fragment.fragment_uid, row_index, 1))
            for cell in fragment.cells:
                if cell.row_idx == row_index and cell.col_idx == 1:
                    cell.raw_text = '2026'
                    cell.numeric_value = Decimal('2026')
assert len(positions) == 1, positions
with patch.object(d1.PDFParser, 'build_parse_artifact', return_value=corrupt):
    bad = d1.reconcile_auto(str(pdf))
assert hashlib.sha256(pdf.read_bytes()).hexdigest() == digest
print(json.dumps({'source_sha256': digest, 'unique_changed_cells': list(positions),
                  'expected': {'normal': {'pass': 1, 'gap': 0}, 'corrupt': {'pass': 0, 'gap': 1}},
                  'normal': normal, 'corrupt': bad}, ensure_ascii=False, indent=2))
assert normal['pass'] == 1 and normal['gap'] == 0
assert bad['pass'] == 0 and bad['gap'] == 1, '金额列2025不能因下一车道名称以年末开头被丢弃；2026必须与原件不符'
