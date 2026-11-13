from pathlib import Path
import hashlib,json,difflib,collections,re
root=Path('/Volumes/workSSD/software development/GovBudgetChecker')
out=root/'artifacts/pdf-input-v2-20261018-joint';work=out/'implementation'
old=root/'artifacts/pdf-input-v2-20261018';before=root/'artifacts/pdf-input-v2-20261017/before'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
starting=json.loads((out/'starting-code-sha256.json').read_text())
paths=set(starting)
paths.update(str(p.relative_to(work)) for p in work.rglob('*') if p.is_file() and not any(x in p.parts for x in ('__pycache__','.pytest_cache','.ruff_cache')))
increment=[p for p in sorted(paths) if (sha(work/p) if (work/p).is_file() else None)!=starting.get(p)]
oldchanged=json.loads((old/'changed-files.json').read_text())
allpaths=set(increment)|{r['path'] for r in oldchanged}
def patch(basis,paths,target):
 chunks=[];changes=[]
 for path in sorted(paths):
  a,b=basis/path,work/path
  aa=a.read_bytes() if a.is_file() else b'';bb=b.read_bytes() if b.is_file() else b''
  if aa==bb:continue
  at,bt=aa.decode('utf-8'),bb.decode('utf-8')
  chunks.append(f'diff --git a/{path} b/{path}\n')
  if not a.exists():chunks.append('new file mode 100644\n')
  if not b.exists():chunks.append('deleted file mode 100644\n')
  for line in difflib.unified_diff(at.splitlines(True),bt.splitlines(True),fromfile=f'a/{path}' if a.exists() else '/dev/null',tofile=f'b/{path}' if b.exists() else '/dev/null'):
   chunks.append(line if line.endswith('\n') else line+'\n\\ No newline at end of file\n')
  changes.append({'path':path,'before_sha256':sha(a) if a.exists() else None,'after_sha256':sha(b) if b.exists() else None})
 target.write_text(''.join(chunks));return changes
full=patch(before,allpaths,out/'input-v2.patch')
inc=patch(old/'implementation',increment,out/'joint-increment.patch')
(out/'changed-files.json').write_text(json.dumps(full,ensure_ascii=False,indent=2))
(out/'incremental-changed-files.json').write_text(json.dumps(inc,ensure_ascii=False,indent=2))
protected=json.loads((root/'artifacts/pdf-input-v2-20261017/baseline_files.json').read_text())
changed=[p for p,h in protected.items() if not (root/p).is_file() or sha(root/p)!=h]
protection={'protected_existing_files':len(protected),'changed_or_missing':changed,'active_checkout_untouched':not changed,
            'previous_implementation_changed': [p for p,h in starting.items() if (old/'implementation'/p).is_file() and sha(old/'implementation'/p)!=h]}
(out/'active-checkout-protection.json').write_text(json.dumps(protection,ensure_ascii=False,indent=2))
original=json.loads((old/'ruff-original-before-full.json').read_text());final=json.loads((out/'ruff-final.json').read_text())
def lint_key(d):
 path=d['filename'];relative=next((name+path.split('/'+name,1)[1] for name in ['src/','api/','ocr_service/','tests/','scripts/'] if '/'+name in path),Path(path).name)
 return relative,d['code'],d['message']
a=collections.Counter(lint_key(d) for d in original);b=collections.Counter(lint_key(d) for d in final)
lint={'baseline':len(original),'final':len(final),'new':[list(k)+[n] for k,n in (b-a).items()],'removed':[list(k)+[n] for k,n in (a-b).items()]}
(out/'lint-comparison.json').write_text(json.dumps(lint,ensure_ascii=False,indent=2))
print(json.dumps({'full_patch_files':len(full),'incremental_files':len(inc),'protection':protection,'lint':lint},ensure_ascii=False))
