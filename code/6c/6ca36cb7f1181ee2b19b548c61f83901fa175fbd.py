"""在被测解析前封存独立原页金额、表头与科目身份；不导入项目解析器。"""
import hashlib
import importlib.util
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('independent_oracle', BASE / 'compare_source.py')
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


seal = {}
totals = []
for doc in json.loads((BASE / 'inventory.json').read_text()):
    folder = BASE / 'documents' / doc['doc_id']
    source = json.loads((folder / 'source_oracle.json').read_text())
    values, regions = oracle.oracle_amounts(source)
    write(folder / 'preparse_source_truth.json', values)
    write(folder / 'preparse_source_regions.json', regions)
    seal[doc['doc_id']] = dict(amounts_sha256=sha(folder / 'preparse_source_truth.json'),
                              regions_sha256=sha(folder / 'preparse_source_regions.json'))
    totals.append(dict(doc_id=doc['doc_id'], amounts=len(values), source_regions=len(regions),
                       integer_amounts=sum('.' not in row['raw_text'] for row in values),
                       grid_word_disagreements=sum(not row['source_words_agree'] for row in values)))
write(BASE / 'preparse_truth_seal.json', dict(source_only_before_tested_parse=True, human_full_signoff=False,
                                           source_files=seal, totals=totals))
print(json.dumps(totals, ensure_ascii=False), flush=True)
