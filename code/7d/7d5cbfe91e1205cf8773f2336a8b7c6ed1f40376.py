from pathlib import Path
import difflib,hashlib,json,re,subprocess
root=Path(__file__).resolve().parents[2];out=Path(__file__).resolve().parent
prior=root/'outputs/reacceptance_20261004_round5';patch=(prior/'working_tree.patch').read_text().splitlines(keepends=True)
snap=json.loads((prior/'source_snapshot_before.json').read_text());comp=json.loads((out/'round5_comparison.json').read_text())
sections={};key=None
for line in patch:
 if line.startswith('diff --git a/'):
  m=re.match(r'diff --git a/(.*?) b/(.*?)\n',line)
  key=m.group(2) if m else None
  if key:sections[key]=[]
 elif key:sections[key].append(line)
def previous(path):
 old=subprocess.check_output(['git','show','HEAD:'+path],cwd=root).decode().splitlines(keepends=True)
 lines=sections.get(path,[]);result=[];cursor=0;i=0
 while i<len(lines):
  m=re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@',lines[i])
  if not m:i+=1;continue
  start=int(m.group(1))-1;result.extend(old[cursor:start]);cursor=start;i+=1
  while i<len(lines) and not lines[i].startswith('@@ '):
   s=lines[i]
   if s.startswith(' '):assert old[cursor]==s[1:];result.append(s[1:]);cursor+=1
   elif s.startswith('-'):assert old[cursor]==s[1:];cursor+=1
   elif s.startswith('+'):result.append(s[1:])
   elif s.startswith('\\ No newline'):
    if result:result[-1]=result[-1].removesuffix('\n')
   i+=1
 result.extend(old[cursor:]);text=''.join(result)
 assert hashlib.sha256(text.encode()).hexdigest()==snap['files'][path],path
 return text
bundles={'source':[],'test':[]};summary=[]
for path in comp['changed_since_round5']:
 a=previous(path);b=(root/path).read_text();d=''.join(difflib.unified_diff(a.splitlines(keepends=True),b.splitlines(keepends=True),fromfile='round5/'+path,tofile='round6/'+path))
 bundles['test' if path.startswith('tests/') else 'source'].append(d);summary.append({'path':path,'diff_lines':len(d.splitlines())})
for path in comp['new_modified_files']:
 a=subprocess.check_output(['git','show','HEAD:'+path],cwd=root).decode();b=(root/path).read_text();d=''.join(difflib.unified_diff(a.splitlines(keepends=True),b.splitlines(keepends=True),fromfile='HEAD/'+path,tofile='round6/'+path))
 bundles['test' if path.startswith('tests/') else 'source'].append(d);summary.append({'path':path,'diff_lines':len(d.splitlines())})
for kind,diffs in bundles.items():(out/(kind+'_changes_since_round5.patch')).write_text('\n'.join(diffs))
print(json.dumps(summary,ensure_ascii=False,indent=2))
