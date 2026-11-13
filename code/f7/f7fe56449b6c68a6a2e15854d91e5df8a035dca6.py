from pathlib import Path
import sys,json,hashlib,time
runtime,carrier,out=map(Path,sys.argv[1:])
sys.path.insert(0,str(runtime))
from src.schemas.parse_artifact import ParseArtifact
from src.services.fiscal_input_builder import FiscalInputBuilder
raw=carrier.read_bytes();a=ParseArtifact.from_dict(json.loads(raw));grids=json.dumps([g.to_dict() for g in a.source_grids],sort_keys=True)
started=time.monotonic();c=FiscalInputBuilder().facts(a)
assert grids==json.dumps([g.to_dict() for g in a.source_grids],sort_keys=True)
out.mkdir(exist_ok=False)
(out/'context.json').write_text(json.dumps(c.to_dict(),ensure_ascii=False,indent=2))
(out/'receipt.json').write_text(json.dumps({'runtime':str(runtime),'carrier_sha256':hashlib.sha256(raw).hexdigest(),'facts':len(c.facts),'locally_qualified_fields':sum(f.extra_dims.get('ocr_provenance',{}).get('qualification',{}).get('status')=='qualified' for f in c.facts),'operands':len(c.definite_facts()),'elapsed_seconds':time.monotonic()-started,'new_inference':False,'source_grids_unchanged':True},indent=2))
print((out/'receipt.json').read_text())
