import json
from pathlib import Path
root=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/pdf_acceptance_shanghai2022_20261021')
obs=[]
# Values were read from original render contact sheets before opening production facts.
expected={'SH2022-15':{7:{'124,584,000.00','0.00'},8:{'124,584,000.00','0.00'},9:{'124,584,000.00','0.00'},11:{'124,584,000.00','0.00'},14:{'0.00'},15:{'0.00'}},'SH2022-02':{8:{'5,701,073,209','5,079,043,209','622,030,000','70,115,526','142,150,852','332,882,397','27,513,648','113,955,679','121,171,767','52,402,903','1,456,337,050','3,431,858,036','283,960,000','93,258,107','5,913,339,587'},21:{'91,631,532','48,263,612','43,367,920','5,079,043,209','988,600,332','4,090,442,877'},27:{'1,435.72','457.50','125.53','852.69','150.00','702.69','6,149.06'}}}
for doc,pages in expected.items():
 source=json.loads((root/'original_occurrences'/doc/'observations.json').read_text())
 for c in source['cells']:
  if c['physical_page'] in pages and c['normalized_lexeme'] in pages[c['physical_page']]:
   obs.append({**c,'source_sha256':source['source_sha256'],'visual_literal':c['normalized_lexeme'],'visual_evidence':str(root/'budget_table_renders'/doc/f"page-{c['physical_page']}.png"),'annotation_method':'model_visual_auxiliary_original_render_with_independent_grid_location','human_verified':False,'annotation_complete':False,'production_output_used_as_truth':False})
# Visible scan observations: no native grid or production-derived bbox is invented.
for page,values in {7:['111,615,328','101,360,282','2,729,648','1,330,998','6,194,400'],8:['101,360,282','53,994,064','47,366,218','2,729,648','1,818,432','909,216','1,306,998'],9:['24,000','6,194,400','2,361,600','3,832,800','111,615,328']}.items():
 for i,v in enumerate(values):obs.append({'occurrence_id':f'SH2022-46:p{page}:visual-{i+1}','source_sha256':'9998da876c9bf81bb83408e0fac744d7e721c4c86c1f9f032efe5cbc3c384b6f','physical_page':page,'bbox':None,'raw_value':v,'source_unit':'元','visual_evidence':str(root/'budget_table_renders/SH2022-46'/f'page-{page}.png'),'annotation_method':'model_visual_auxiliary_bbox_unknown','human_verified':False,'annotation_complete':False,'production_output_used_as_truth':False})
(root/'model_visual_auxiliary_cells.json').write_text(json.dumps({'scope':'limited_original_render_observations_not_human_truth','human_verified':False,'annotation_complete':False,'cells':obs},ensure_ascii=False,indent=2));print('auxiliary_visual_cells',len(obs))
