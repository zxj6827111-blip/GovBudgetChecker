"""核验冻结证据、原PDF及任务开始前工作区内容；只读并另存本轮报告。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def verify(allowed):
    immutable = read(BASE / "immutable_before.json")
    frozen_differences = [dict(path=name, before=value, after=sha(ROOT / name) if (ROOT / name).exists() else None)
                          for name, value in immutable.items() if not (ROOT / name).is_file() or sha(ROOT / name) != value]
    directories = {name.split("/")[1] for name in immutable if name.startswith("outputs/")}
    added_frozen = [str(path.relative_to(ROOT)) for directory in directories for path in (ROOT / "outputs" / directory).rglob("*")
                    if path.is_file() and "__pycache__" not in path.parts and str(path.relative_to(ROOT)) not in immutable]
    originals = []
    for batch in ("shanghai_2024_new10_acceptance_20261015", "shanghai_2024_v23_acceptance_20261015"):
        for doc in read(ROOT / "outputs" / batch / "inventory.json"):
            source = Path(doc["source_pdf"])
            originals.append(dict(path=str(source), expected_sha256=doc["sha256"], actual_sha256=sha(source), unchanged=sha(source) == doc["sha256"]))
    before = read(BASE / "workspace_before.json")
    changed = []
    for name, old in before.items():
        target = ROOT / name
        now = sha(target) if target.is_file() else None
        if old != now:
            changed.append(dict(path=name, before=old, after=now, authorized=any(name == item or name.startswith(item.rstrip("/") + "/") for item in allowed)))
    listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT, check=True, capture_output=True).stdout
    added_workspace = [name for name in listed.decode().split("\0") if name and name not in before and (ROOT / name).is_file()]
    audit_path = ROOT / "data/local-audit/admin-actions.jsonl"
    audit_equal = audit_path.is_file() and audit_path.read_bytes() == (BASE / "audit_before.jsonl").read_bytes()
    report = dict(frozen_files_checked=len(immutable), frozen_differences=frozen_differences, added_frozen_files=added_frozen,
                  original_pdfs_checked=len(originals), original_pdfs=originals, workspace_preexisting_files_checked=len(before),
                  preexisting_changed_files=changed, unauthorized_preexisting_changes=[row for row in changed if not row["authorized"]],
                  added_workspace_files=added_workspace, default_audit_log_bytes_unchanged=audit_equal,
                  pass_frozen=not frozen_differences and not added_frozen,
                  pass_originals=all(row["unchanged"] for row in originals),
                  pass_unrelated=not any(not row["authorized"] for row in changed),
                  policy="No reset/delete/restore is performed; changes are reported for review")
    (BASE / "scope_protection_verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow", nargs="*", default=[])
    args = parser.parse_args()
    result = verify(args.allow)
    print(json.dumps({key: value for key, value in result.items() if key not in ("original_pdfs", "preexisting_changed_files", "added_workspace_files")}, ensure_ascii=False))
