import json,math,collections
from pathlib import Path
rows=json.loads(Path('income-grid-conflicts-v31/unresolved-source-cells.json').read_text())
for item in rows:
 measured=item['physical_measurement'];boxes=[part['grid_bbox'] for g in item['fragment_groups'] for support in g.get('coverage_support',[g['selected']]) for part in support.get('members',[support])]
 pixels={(x,y) for y,a,b in measured.get('content_ink_runs_px',measured.get('observed_ink_runs_px',[])) for x in range(a,b) if not any(bb[0]-1<=x<=bb[2]+1 and bb[1]-1<=y<=bb[3]+1 for bb in boxes)}
 comps=[]
 while pixels:
  seed=pixels.pop();cc={seed};queue=[seed]
  while queue:
   x,y=queue.pop()
   for dx,dy in ((-1,0),(1,0),(0,1),(0,-1)):
    v=(x+dx,y+dy)
    if v in pixels:pixels.remove(v);cc.add(v);queue.append(v)
  comps.append({'pixels':len(cc),'bbox':[min(p[0] for p in cc),min(p[1] for p in cc),max(p[0] for p in cc)+1,max(p[1] for p in cc)+1]})
 print(item['page'],item['row_idx'],item['col_idx'],'cell',measured['bbox_px'],'boxes',boxes,'uncovered',comps,'edges',[{k:v for k,v in e.items() if k in ['axis','coordinate','thickness']} for e in measured.get('boundary_measurements',[])])
