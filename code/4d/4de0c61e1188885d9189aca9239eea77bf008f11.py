from pathlib import Path
import hashlib,json,difflib,collections
root=Path('/Volumes/workSSD/software development/GovBudgetChecker')
out=root/'artifacts/pdf-input-v2-20261018-stitch';work=out/'implementation'
old=root/'artifacts/pdf-input-v2-20261018-joint';basis=root/'artifacts/pdf-input-v2-20261017/before'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
starting=json.loads((out/'starting-code-sha256.json').read_text())
paths={p for p in starting if not p.startswith(('data/','uploads/','outputs/','reports/'))}
paths.update(str(p.relative_to(work)) for name in ('src','api','ocr_service','tests','scripts') for p in (work/name).rglob('*') if p.is_file() and not any(x in p.parts for x in ('__pycache__','.pytest_cache','.ruff_cache')))
increment=[p for p in sorted(paths) if (sha(work/p) if (work/p).is_file() else None)!=starting.get(p)]
allpaths=set(increment)|{r['path'] for r in json.loads((old/'changed-files.json').read_text()) if not r['path'].startswith(('data/','uploads/','outputs/','reports/'))}
def patch(before,names,target):
 chunks=[];changes=[]
 for path in sorted(names):
  a,b=before/path,work/path;aa=a.read_bytes() if a.is_file() else b'';bb=b.read_bytes() if b.is_file() else b''
  if aa==bb:continue
  chunks.append(f'diff --git a/{path} b/{path}\n')
  if not a.exists():chunks.append('new file mode 100644\n')
  if not b.exists():chunks.append('deleted file mode 100644\n')
  for line in difflib.unified_diff(aa.decode().splitlines(True),bb.decode().splitlines(True),fromfile=f'a/{path}' if a.exists() else '/dev/null',tofile=f'b/{path}' if b.exists() else '/dev/null'):
   chunks.append(line if line.endswith('\n') else line+'\n\\ No newline at end of file\n')
  changes.append({'path':path,'before_sha256':sha(a) if a.exists() else None,'after_sha256':sha(b) if b.exists() else None})
 target.write_text(''.join(chunks));return changes
full=patch(basis,allpaths,out/'input-v2.patch');inc=patch(old/'implementation',increment,out/'stitch-increment.patch')
(out/'changed-files.json').write_text(json.dumps(full,ensure_ascii=False,indent=2));(out/'incremental-changed-files.json').write_text(json.dumps(inc,ensure_ascii=False,indent=2))
protected=json.loads((root/'artifacts/pdf-input-v2-20261017/baseline_files.json').read_text());changed=[p for p,h in protected.items() if not (root/p).is_file() or sha(root/p)!=h]
prior=[p for p,h in starting.items() if not (old/'implementation'/p).is_file() or sha(old/'implementation'/p)!=h]
assert not changed and not prior
protection={'protected_existing_files':len(protected),'changed_or_missing':changed,'previous_implementation_changed':prior,'active_checkout_untouched':True}
(out/'active-checkout-protection.json').write_text(json.dumps(protection,ensure_ascii=False,indent=2))
base_lint=json.loads((old/'ruff-final.json').read_text());new_lint=json.loads((out/'ruff-full.json').read_text())
def key(d):return str(Path(d['filename']).relative_to(old/'implementation' if str(d['filename']).startswith(str(old/'implementation')) else work)),d['code'],d['message']
a=collections.Counter(map(key,base_lint));b=collections.Counter(map(key,new_lint));lint={'baseline':len(base_lint),'current':len(new_lint),'new':[list(k)+[v] for k,v in (b-a).items()],'removed':[list(k)+[v] for k,v in (a-b).items()]}
(out/'lint-comparison.json').write_text(json.dumps(lint,ensure_ascii=False,indent=2))
print(json.dumps({'full_files':len(full),'increment_files':len(inc),'increment_paths':increment,'protection':protection,'lint':lint},ensure_ascii=False))
