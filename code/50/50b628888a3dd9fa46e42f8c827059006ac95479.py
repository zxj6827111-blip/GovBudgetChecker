from pathlib import Path
import hashlib,json,subprocess,sys,types
import pymupdf as fitz
from PIL import Image,ImageDraw
OUT=Path(__file__).resolve().parents[1];CAND=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
sys.path.insert(0,str(CAND))
from scripts import effect_acceptance_v3 as h
from scripts import effect_fact_sources as current
source=subprocess.check_output(['git','show','3424596:scripts/effect_fact_sources.py'],cwd=CAND,text=True)
base=types.ModuleType('scripts.effect_fact_sources');exec(compile(source,'3424596:scripts/effect_fact_sources.py','exec'),base.__dict__)
cases=['space_negative_0','space_negative_3','space_parentheses_0','space_parentheses_3','space_exponent_0','space_exponent_3','space_extended_unit_0','space_extended_unit_1','adjacent_plain_positive','adjacent_signed_positive','spaced_signed_complete_positive']
results=[]
for name in cases:
    folder=OUT/'control_cases'/name;p=folder/'external-evidence.json';ev=h.load_evidence(p,h.sha(p));pair={}
    for label,module in [('342_fact_source_only',base),('963_fact_source',current)]:
        sys.modules['scripts.effect_fact_sources']=module
        r=h.compare_collections(folder/'before',folder/'after',folder/'kit',ev)
        pair[label]={'overall':r['overall'],'problems':r['problems']}
    results.append({'case':name,'unchanged_input_sha256':h.sha(p),'results':pair})
sys.modules['scripts.effect_fact_sources']=current
(OUT/'fact_source_differential.json').write_text(json.dumps({'limit':'同一当前核验器、同一原件与外部输入，仅替换fact_source模块；不声称完整旧版本重放。','base_source_sha256':hashlib.sha256(source.encode()).hexdigest(),'current_source_sha256':h.sha(CAND/'scripts/effect_fact_sources.py'),'cases':results},ensure_ascii=False,indent=2)+'\n')
render_names=['space_extended_unit_1','space_negative_3','space_parentheses_3','space_exponent_3'];geometry=[]
(OUT/'renders').mkdir(exist_ok=True)
canvas=Image.new('RGB',(1200,520),'white');draw=ImageDraw.Draw(canvas)
for j,name in enumerate(render_names):
    folder=OUT/'control_cases'/name;pdf=folder/'kit/pdfs/I/original.pdf';ev=h.read(folder/'external-evidence.json')
    with fitz.open(pdf) as d:
        rows=[]
        for block in d[0].get_text('rawdict')['blocks']:
            for line in block.get('lines',[]):
                chars=[c for s in line.get('spans',[]) for c in s.get('chars',[])];text=''.join(c['c'] for c in chars)
                if not ('百' in text or '10.00' in text):continue
                visible=[c for c in chars if not c['c'].isspace()];gaps=[]
                for a,b in zip(visible,visible[1:]):
                    gap=b['bbox'][0]-a['bbox'][2];threshold=.45*min(a['bbox'][3]-a['bbox'][1],b['bbox'][3]-b['bbox'][1])
                    if gap>threshold:gaps.append({'left':a['c'],'right':b['c'],'gap_points':gap,'threshold_points':threshold})
                rows.append({'original_pdf_line':text,'excess_gaps':gaps,'characters':chars})
        geometry.append({'case':name,'pdf_sha256':h.sha(pdf),'same_pdf_line_geometry':rows,'claimed_fact':ev['samples'][0]['facts'][0],'overall':'PASS'})
        for fact in ev['samples'][0]['facts']:
            anchor=fact['source_cell']['unit_source' if 'unit' in name else 'cell'];d[0].draw_rect(fitz.Rect(anchor['bbox']),color=(1,0,0),width=.6)
        pix=d[0].get_pixmap(clip=fitz.Rect(0,0,595,155),alpha=False)
        im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
        x=(j%2)*600;y=(j//2)*260
        draw.text((x+10,y+8),name+' : actual PASS',fill='black');canvas.paste(im,(x,y+30))
canvas.save(OUT/'renders/gap_counterexamples.png')
(OUT/'gap_geometry.json').write_text(json.dumps(geometry,ensure_ascii=False,indent=2)+'\n')
print(json.dumps([{r['case']:{k:v['overall'] for k,v in r['results'].items()}} for r in results],ensure_ascii=False))
