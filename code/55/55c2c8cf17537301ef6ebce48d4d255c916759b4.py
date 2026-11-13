import json,subprocess,time,uuid,hashlib
from pathlib import Path
import sys
BASE=Path(__file__).resolve().parent;ROOT=BASE.parents[1];sys.path.insert(0,str(ROOT))
from src.services.pdf_parser import PDFParser
name="govbudget-jsonb-"+uuid.uuid4().hex[:10]
selection=json.loads((ROOT/"outputs/putuo_visual_compare_10_20261014/selection.json").read_text())["documents"]
a=PDFParser(None).build_parse_artifact(selection[0]["source_pdf"])
payload=json.loads(Path(sys.argv[1]).read_text()) if len(sys.argv)>1 else a.to_dict();serialized=json.dumps(payload,ensure_ascii=False)
try:
 subprocess.run(["docker","run","-d","--rm","--network","none","--name",name,"-e","POSTGRES_HOST_AUTH_METHOD=trust","postgres:16"],capture_output=True,check=True)
 for _ in range(90):
  r=subprocess.run(["docker","exec",name,"pg_isready","-U","postgres"],capture_output=True)
  if r.returncode==0:break
  time.sleep(.5)
 sql="SELECT '"+serialized.replace("'","''")+"'::jsonb;"
 r=subprocess.run(["docker","exec","-i",name,"psql","-U","postgres","-At"],input=sql,capture_output=True,text=True,check=True)
 returned=json.loads(r.stdout)
 (BASE/"jsonb_roundtrip.json").write_text(json.dumps(returned,ensure_ascii=False))
 diffs=[]
 def diff(o,n,path=""):
  if isinstance(o,dict) and isinstance(n,dict):
   for k in o.keys()|n.keys():
    if k not in o or k not in n:diffs.append([path,k,"missing"]);continue
    diff(o[k],n[k],path+"/"+k)
  elif isinstance(o,list) and isinstance(n,list):
   if len(o)!=len(n):diffs.append([path,"length",len(o),len(n)]);return
   for i,(u,v) in enumerate(zip(o,n)):diff(u,v,path+"/"+str(i))
  elif o!=n or json.dumps(o)!=json.dumps(n):diffs.append([path,repr(o),repr(n)])
 diff(payload,returned)
 print("differences",len(diffs));print(json.dumps(diffs[:40],ensure_ascii=False,indent=2))
 (BASE/"jsonb_diff.json").write_text(json.dumps(diffs,ensure_ascii=False,indent=2))
finally:
 subprocess.run(["docker","rm","-f",name],capture_output=True)
