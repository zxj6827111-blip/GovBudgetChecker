import sys
import json
import importlib
root, src, out = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
from src.engine.rules_v33 import Document
from src.engine.rule_outcome import RuleDeferred, RuleNotApplicable
from src.services.document_profile_resolver import resolve_document_profile
import src.engine.checklist_identity_rules as idr
import src.engine.checklist_text_rules as tr
import src.engine.checklist_content_rules as cr
import src.engine.checklist_structure_rules as sr
RULES=[(idr,'CMM018_ScopeTerminology'),(idr,'CMM019_CoverElements'),(idr,'CMM027_TemplatePositionScope'),(tr,'CMM008_TemplateResidue'),(sr,'CMM013_TocHeadingAlignment'),(cr,'CMM021_TableTitleNormative'),(cr,'CMM023_SectionTemplate')]
data=json.load(open(src))
res={}
for name,pages in data.items():
    if not pages or not any(t.strip() for t in pages): continue
    fn=name.split('/')[-1]
    prof=resolve_document_profile(filename=fn,page_texts=pages)
    if prof.kind!='final':
        res[name]={'kind':prof.kind}
        continue
    def make(fn=fn, pages=pages, prof=prof):
        d=Document(fn,len(pages),100,pages,[[] for _ in pages],[None]*len(pages),[[]]*len(pages),report_kind='final',document_profile=prof)
        if hasattr(idr,'prepare_identity_document'):
            from src.services.material_writing import ensure_document_resolution
            ensure_document_resolution(d, prof)
        return d
    row={'writing':(getattr(prof,'writing_scope',None) or {}).get('status') if hasattr(idr,'prepare_identity_document') else None}
    for mod,cls in RULES:
        c=getattr(mod,cls,None)
        if c is None: continue
        key=cls[:6]
        try:
            r=c().apply(make())
            row[key]={'s':'done','i':[i.message for i in r]}
        except RuleDeferred as e: row[key]={'s':'deferred','d':str(e.detail),'i':[i.message for i in (e.partial_issues or [])]}
        except RuleNotApplicable as e: row[key]={'s':'na','d':str(e.detail)}
        except Exception as e: row[key]={'s':'error','d':repr(e)[:300]}
    if hasattr(prof,'writing_scope'): row['writing']=(prof.writing_scope or {}).get('status')
    res[name]=row
json.dump(res,open(out,'w'),ensure_ascii=False)
print(out,len(res))
