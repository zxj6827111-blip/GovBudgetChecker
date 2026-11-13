import importlib.util,sys
from pathlib import Path
p=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_closeout_20261104/tools/evaluate_114_targets.py')
s=importlib.util.spec_from_file_location("evaluate_snapshot",p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
m.TOOLS=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_closeout_round2_review_codex_20261104/comparator/scripts')
raise SystemExit(m.main())
