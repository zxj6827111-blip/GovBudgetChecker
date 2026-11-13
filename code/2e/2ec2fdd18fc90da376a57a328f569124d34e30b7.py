import pathlib,json,time
root=pathlib.Path('/Volumes/workSSD/software development/GovBudgetChecker');out=root/'outputs/native_review_coverage_20261108';m=json.loads((out/'manifest_before.json').read_text());jobs=[j for j,x in m.items() if x['status']=='review_required'];previous=None
while True:
 rows=[]
 for j in jobs:
  d=json.loads((root/'uploads'/j/'status.json').read_text());rows.append({'job':j,'status':d['status'],'progress':d.get('progress'),'stage':d.get('stage'),'revision':d.get('analysis_revision')})
 state=json.dumps(rows,ensure_ascii=False)
 if state!=previous:
  print(time.strftime('%H:%M:%S'),state,flush=True);previous=state
 if all(x['status'] not in ('queued','running','processing','analyzing') for x in rows):break
 time.sleep(10)
