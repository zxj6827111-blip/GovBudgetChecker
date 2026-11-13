"""只读检查原 PDF、冻结证据及无关既有工作区；允许项必须精确文件名。"""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import subprocess

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def verify(allowed, output):
    allowed = set(allowed)
    assert all(not name.endswith('/') and '*' not in name and not Path(name).is_absolute() and '..' not in Path(name).parts for name in allowed), '允许清单只能含仓库相对精确文件名'
    immutable = read(BASE / 'immutable_before.json')
    workspace = read(BASE / 'workspace_before.json')
    frozen_changes = [dict(path=name, before=old, after=sha(ROOT / name)) for name, old in immutable.items() if sha(ROOT / name) != old]
    changed = [dict(path=name, before=old, after=sha(ROOT / name), allowed=name in allowed) for name, old in workspace.items() if sha(ROOT / name) != old]
    raw = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT)
    current = set(part.decode() for part in raw.split(b'\0') if part)
    phase_prefix = str(BASE.relative_to(ROOT)) + '/'
    new_files = [dict(path=name, allowed=name in allowed or name.startswith(phase_prefix)) for name in sorted(current - set(workspace)) if (ROOT / name).is_file()]
    # 原文件与冻结文件分开校验，不能把冻结副本未变等同原桌面PDF未变。
    originals = {}
    for folder in ['shanghai_2024_v23_acceptance_20261015', 'shanghai_2024_new10_acceptance_20261015', 'shanghai_2024_new9_acceptance_20261016']:
        for doc in read(ROOT / 'outputs' / folder / 'inventory.json'):
            path = Path(doc['source_pdf'])
            existing = originals.get(str(path))
            assert existing is None or existing['before'] == doc['sha256']
            originals[str(path)] = dict(path=str(path), before=doc['sha256'], after=sha(path))
    original_rows = [dict(row, unchanged=row['before'] == row['after']) for row in originals.values()]
    audit_original = (BASE / 'audit_before.jsonl').read_bytes()
    audit_path = ROOT / 'data/local-audit/admin-actions.jsonl'
    audit_current = audit_path.read_bytes() if audit_path.is_file() else None
    # 用本轮开始快照生成补丁，保留Git HEAD之前已有的用户脏改语境。
    incremental, patch = [], []
    for name in sorted(allowed & workspace.keys()):
        original = BASE / 'code_before' / name
        current_path = ROOT / name
        if original.is_file() and current_path.is_file():
            assert sha(original) == workspace[name], f'code_before快照漂移: {name}'
            lines = list(difflib.unified_diff(original.read_text().splitlines(keepends=True), current_path.read_text().splitlines(keepends=True), fromfile='before/' + name, tofile='after/' + name))
            patch.extend(lines)
            incremental.append(dict(path=name, baseline_snapshot_matches_workspace_before=True, changed=bool(lines), diff_lines=len(lines)))
    (BASE / 'scope_incremental_existing.patch').write_text(''.join(patch))
    unallowed_changes = [r for r in changed if not r['allowed']]
    unallowed_new = [r for r in new_files if not r['allowed']]
    report = dict(status='passed' if not frozen_changes and not unallowed_changes and not unallowed_new and all(r['unchanged'] for r in original_rows) and audit_original == audit_current else 'failed',
                  modifies_any_source=False, original_pdfs=dict(count=len(original_rows), unchanged=sum(r['unchanged'] for r in original_rows), records=original_rows),
                  frozen_files=dict(count=len(immutable), unchanged=len(immutable) - len(frozen_changes), changes=frozen_changes),
                  existing_workspace=dict(count=len(workspace), unchanged=len(workspace) - len(changed), changed=changed, unrelated_changes=unallowed_changes),
                  new_workspace_files=new_files, unexpected_new_files=unallowed_new, allowed_exact_files=sorted(allowed),
                  allowed_file_incremental_diffs=incremental, incremental_patch='scope_incremental_existing.patch',
                  incremental_diff_basis='本轮开始code_before快照；无关文件逐字节保护，允许文件内修改另由增量补丁供审查。',
                  audit_original_bytes=len(audit_original), audit_current_bytes=len(audit_current) if audit_current is not None else None,
                  audit_bytes_unchanged=audit_original == audit_current,
                  audit_sha256_before=hashlib.sha256(audit_original).hexdigest(), audit_sha256_after=sha(audit_path))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow', action='append', default=[])
    parser.add_argument('--allow-json')
    parser.add_argument('--output', default='protection_final.json')
    args = parser.parse_args()
    allowed = args.allow
    if args.allow_json:
        data = read(Path(args.allow_json))
        allowed += data if isinstance(data, list) else data['allowed_exact_files']
    output = Path(args.output)
    if not output.is_absolute():
        output = BASE / output
    assert BASE in output.resolve().parents
    result = verify(allowed, output)
    print(json.dumps({key: value for key, value in result.items() if key not in ['original_pdfs', 'frozen_files', 'existing_workspace', 'new_workspace_files', 'allowed_exact_files']}, ensure_ascii=False))
    print(json.dumps(dict(original_pdfs=result['original_pdfs']['count'], frozen_files=result['frozen_files']['count'], unrelated_changes=len(result['existing_workspace']['unrelated_changes'])), ensure_ascii=False))
    raise SystemExit(0 if result['status'] == 'passed' else 1)
