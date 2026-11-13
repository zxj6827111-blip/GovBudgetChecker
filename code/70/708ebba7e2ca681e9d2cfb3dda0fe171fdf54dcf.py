"""只替换历史解析器文件作对照；下游模块仍为当前版本，不冒充历史整栈。"""
import importlib.util
import json
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(ROOT))
from compare_source import contains, digits, write


def main():
    spec = importlib.util.spec_from_file_location('frozen_v22_parser', ROOT/'outputs/native_structure_repair_20261015/before/src/services/pdf_parser.py')
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    results = []
    for doc in json.loads((BASE/'inventory.json').read_text()):
        out = BASE/'documents'/doc['doc_id']
        artifact = old.PDFParser(None).build_parse_artifact(str(out/'source.pdf'))
        truth = json.loads((out/'source_amount_truth.json').read_text())
        def read_match(source):
            return [(t.table_code,c.raw_text) for t in artifact.physical_fragments
                    if t.extraction_method != 'page_text_fallback'
                    for c in t.cells if c.page_number==source['page'] and c.numeric_value is not None
                    and contains(c.bbox,source['bbox']) and digits(c.raw_text)==digits(source['raw_text'])]
        missing = [s for s in truth if len(read_match(s)) != 1]
        results.append(dict(doc_id=doc['doc_id'],filename=doc['filename'],parser=artifact.parser_fingerprint,
                            source_amounts=len(truth),exact_amounts=len(truth)-len(missing),not_exact=missing))
        print(doc['doc_id'],len(truth),len(missing),flush=True)
    write(BASE/'v22_parser_comparison.json',dict(scope='frozen v22 parser with current downstream modules; not historical full stack',
          results=results,source_amounts=sum(d['source_amounts'] for d in results),
          exact_amounts=sum(d['exact_amounts'] for d in results)))


if __name__ == '__main__':
    main()
