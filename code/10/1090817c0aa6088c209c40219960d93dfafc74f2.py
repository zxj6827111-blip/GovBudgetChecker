"""Diagnostic only: remove one duplicated keyword group in memory, not on disk."""
import hashlib
import importlib.abc
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
for key in list(os.environ):
    if key.startswith(("AI_", "GOVBUDGET_TEST_AI_", "OPENAI_", "GEMINI_", "ZHIPU_", "DEEPSEEK_")):
        os.environ.pop(key)
os.environ.update(DATABASE_URL="", GOVBUDGET_TEST_DATABASE_URL="", PYTHON_DOTENV_DISABLED="1", UPLOAD_DIR=str(OUT / "diagnostic_uploads"))
source_path = ROOT / "src/engine/budget_rules.py"
original = source_path.read_text()
group = '''                                check_relation_id=f"{self.code}:T9:{key}:text_vs_table",
                                fact_ids=fact_ids,
                                evidence_ids=(
                                    list(fact_t9_refs[key].evidence_ids or [])
                                    if key in fact_t9_refs else []
                                ),
'''
if original.count(group + group) != 1:
    raise RuntimeError("Diagnostic transform requires exactly one known repeated group")
diagnostic = original.replace(group + group, group, 1)
(OUT / "diagnostic_transform.json").write_text(json.dumps({
    "scope": "diagnostic_only_not_current_working_tree_acceptance",
    "removed_only_second_duplicate_keyword_group": True,
    "source_sha256": hashlib.sha256(original.encode()).hexdigest(),
    "diagnostic_source_sha256": hashlib.sha256(diagnostic.encode()).hexdigest(),
    "removed_text": group,
}, indent=2))

class Loader(importlib.abc.Loader):
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        module.__file__ = str(source_path)
        exec(compile(diagnostic, str(source_path), "exec"), module.__dict__)

class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "src.engine.budget_rules":
            return importlib.util.spec_from_loader(fullname, Loader(), origin=str(source_path))
        return None

sys.meta_path.insert(0, Finder())
import pytest
result = pytest.main(["-q"])
sys.exit(result)
