from pathlib import Path
import sys,json
BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE/'implementation'))
from src.schemas.parse_artifact import ParseArtifact
from src.services.fiscal_source_profile import source_document_profile
records=[]
for p in sorted((BASE/'frozen34-v16').glob('*/documents/*/artifact.json')):
 artifact=ParseArtifact.from_dict(json.loads(p.read_text()))
 profile=source_document_profile(artifact)
 context=json.loads((p.parent/'fact_context.json').read_text())
 record={'document':p.parent.name,'source_file_hash':artifact.source_file_hash,
  'source_org':profile['org_name'],'baseline_org':context['org_name'],'report_year':profile['report_year'],
  'report_kind':profile['report_kind'],'org_aliases':profile['org_aliases'],'org_scopes':profile['org_scopes'],
  'reasons':profile['reasons'],'declarations':len(profile['sources'])}
 records.append(record)
 print(json.dumps(record,ensure_ascii=False),flush=True)
(BASE/'native34-source-profile-audit-v25.json').write_text(json.dumps({'scope':'fixed_development_sources','documents':len(records),'rows':records},ensure_ascii=False,indent=2))
