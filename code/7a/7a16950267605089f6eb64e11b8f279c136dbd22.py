import sys,json
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path.cwd()))
from scripts.d4_real_link_acceptance import verify_all_evidence
out=Path('/private/tmp/gbc-r2-results-20261111')
class C:
 def __init__(self, bodies):self.bodies=bodies
 def get(self,url):return SimpleNamespace(status_code=200,json=lambda:self.bodies[url.rsplit('/',1)[1]])
refs=[{'page':1,'span':'表中金额105.00万元','role':'表格'},{'page':2,'span':'说明金额135.00万元','role':'说明'}]
f={'id':'cross','source':'rule','rule_id':'R','severity':'high','location':{'page':1,'table_refs':refs},'evidence':[{'evidence_id':'e1','page':1,'quote':'政府性基金表'},{'evidence_id':'e2','page':2,'quote':'情况说明'}],'evidence_verification':{'status':'verified'}}
bodies={'e1':{'physical_page':1,'quote':'政府性基金表','file_hash':'a'*64},'e2':{'physical_page':2,'quote':'情况说明','file_hash':'a'*64}}
result=verify_all_evidence(C(bodies),'control',{'rule_findings':[f]},['政府性基金表\n表中金额105.00万元','情况说明\n说明金额135.00万元'],'a'*64,2)
answer={'input':{'finding':f,'endpoint_evidence':bodies},'cross_page_operands_not_delivered':result}
(out/'d4_adversarial_controls.json').write_text(json.dumps(answer,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False,indent=2))
