import ast,hashlib,json
from pathlib import Path
from types import SimpleNamespace
source=Path('/repo/tests/remediation/test_zc02.py')
tree=ast.parse(source.read_text())
function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='test_ocr_service_accepts_multipart_and_returns_ppstructure')
class Monkey:
 def __init__(self): self.changes=[]
 def setattr(self,obj,name,value): self.changes.append((obj,name,getattr(obj,name)));setattr(obj,name,value)
 def restore(self):
  for obj,name,value in reversed(self.changes):setattr(obj,name,value)
namespace={'pytest':SimpleNamespace(importorskip=__import__)}
exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),namespace)
monkey=Monkey()
try: namespace[function.name](monkey)
finally: monkey.restore()
import cv2
print(json.dumps({'passed':True,'scope':'exact_cv2_multipart_contract_test_with_fixture_predictor_not_model_accuracy','test_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'opencv_version':cv2.__version__}))
