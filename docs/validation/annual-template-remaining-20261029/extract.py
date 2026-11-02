import fitz
import json
import sys
import pathlib
root=pathlib.Path(sys.argv[1])
out={}
for p in sorted(root.rglob('*.pdf')):
    try:
        with fitz.open(p) as d: out[str(p.relative_to(root))]=[pg.get_text() for pg in d]
    except Exception: out[str(p.relative_to(root))]=None
json.dump(out,open(sys.argv[2],'w'),ensure_ascii=False)
print(len(out))
