import importlib.util
from pathlib import Path
p=Path(__file__).parent
s=importlib.util.spec_from_file_location("ev_fixed",p/"delivery_tools/evaluate_114_targets.py")
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
m.TOOLS=p/"comparator/scripts"
m.OCCURRENCE_CONTRACT=p/"delivery_tools/occurrence_contract_20261104.json"
raise SystemExit(m.main())
