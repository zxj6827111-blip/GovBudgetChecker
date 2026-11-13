"""Capture the dirty source tree and distinguish this turn from its baseline."""
from pathlib import Path
import datetime
import difflib
import hashlib
import json
import os
import subprocess

ROOT = Path.cwd()
OUT = ROOT / "outputs/remediation_20261006_round13"


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def git(*args, env=None):
    return subprocess.run(["git", *args], check=True, capture_output=True, env=env).stdout


index = OUT / "baseline_index"
env = {**os.environ, "GIT_INDEX_FILE": str(index)}
git("read-tree", "HEAD", env=env)
git("apply", "--cached", str(OUT / "before.patch"), env=env)
paths = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")
tracked = set(git("ls-files", "-z").decode().split("\0"))
baseline = json.loads((OUT / "baseline.json").read_text())
protected = {row["path"] for row in baseline["files"]}
created = {
    'scripts/prepare_independent_acceptance.py',
    'tests/remediation/test_round13_ocr_trust.py',
    'docs/独立盲测交接操作_20261006.md',
    'docs/GovBudgetChecker_第十三轮整改交接记录_20261006.md',
}
rows, changes, focused_patch = [], [], []
extensions = {".py", ".md", ".json", ".yml", ".yaml", ".toml", ".txt", ".lock", ".tsx", ".ts", ".js", ".mjs", ".css", ".sh"}
for name in sorted(set(paths)):
    path = ROOT / name
    if not name or not path.is_file() or name.startswith((".mimosa/", "outputs/")):
        continue
    if path.suffix not in extensions and not path.name.startswith(("Dockerfile", ".env.example", ".gitignore", ".dockerignore")):
        continue
    dest = OUT / "source_files" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = path.read_bytes()
    dest.write_bytes(content)
    row = {"path": name, "sha256": digest(path), "bytes": len(content), "snapshot_path": str(dest.relative_to(ROOT))}
    rows.append(row)
    before = None
    if name in tracked:
        before = git("show", ":" + name, env=env)
    elif name in protected:
        before = (OUT / "before" / name).read_bytes()
    elif name in created:
        before = b""
    if before is not None and before != content:
        changes.append({"path": name, "baseline_sha256": hashlib.sha256(before).hexdigest(), "final_sha256": row["sha256"]})
        try:
            focused_patch.extend(difflib.unified_diff(before.decode().splitlines(True), content.decode().splitlines(True), fromfile="before/"+name, tofile="after/"+name))
        except UnicodeError:
            pass
(OUT / "this_turn_diff.patch").write_text("".join(focused_patch))
(OUT / "working_tree.patch").write_bytes(git("diff", "HEAD", "--binary"))
(OUT / "source_changes.json").write_text(json.dumps({"changes": changes, "untracked_baseline_limit": "Only captured or independently hash-recovered baselines and this turn created files are attributed to this turn; other pre-existing dirty files are preserved."}, ensure_ascii=False, indent=2))
snapshot = {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "head": git("rev-parse", "HEAD").decode().strip(), "branch": git("branch", "--show-current").decode().strip(), "source_tree_dirty": True, "files": rows, "model_manifest_sha256": digest(ROOT / "models/MANIFEST.sha256")}
(OUT / "source_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
index.unlink()
print(json.dumps({"source_files": len(rows), "changes_with_recorded_baseline": len(changes), "source_snapshot_sha256": digest(OUT / "source_snapshot.json")}))
