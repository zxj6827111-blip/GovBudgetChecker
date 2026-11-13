"""Read-only rule-system inventory dump for the 2026-11-10 governance audit.

Outputs JSON to stdout; touches no repo file.
Layers:
  1. YAML declaration layer: rules/v3_3.yaml via rules.loader_ext.RuleLoaderExt
  2. Python execution layer: src.engine.rule_registry.rule_catalog()
  3. Obligation layer: src.engine.check_obligations.OBLIGATION_CATALOG
  4. Checklist layer: rules/checklists/*.yaml via checklist_catalog
  5. Legacy DB QC layer: src.qc.runner_v3 rule keys (static list)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rules.loader_ext import RuleLoaderExt
from src.engine.rule_registry import rule_catalog
from src.engine.check_obligations import OBLIGATION_CATALOG, OBLIGATION_CATALOG_VERSION
from src.engine.checklist_catalog import load_checklist, validate_checklist

out = {}

# 1. YAML declaration layer (default file = v3_3.yaml, same as production fallback)
loader = RuleLoaderExt(str(ROOT / "rules"))
ext = loader.load_default_rules()
out["yaml_rules"] = [
    {
        "code": r.code,
        "executor": r.executor,
        "enabled": r.enabled,
        "severity": r.severity,
        "aliases": r.aliases,
        "category": r.category,
        "desc": (r.desc or "")[:80],
    }
    for r in ext
]
out["yaml_validation_errors"] = loader.validate_rules(ext)

# also parse budget_v3_3_draft.yaml to see whether it is wired anywhere
draft = loader.load_rules_from_file(str(ROOT / "rules" / "budget_v3_3_draft.yaml"))
out["yaml_draft_rules"] = [{"code": r.code, "executor": r.executor, "enabled": r.enabled} for r in draft]

# 2. Python execution layer
out["registry"] = rule_catalog()

# 3. Obligation layer
out["obligation_catalog_version"] = OBLIGATION_CATALOG_VERSION
out["obligations"] = [
    {
        "obligation_id": o.obligation_id,
        "group": o.group_id,
        "title": o.title,
        "report_kinds": list(o.report_kinds),
        "checkers_by_kind": {k: list(v) for k, v in o.checkers_by_kind.items()},
        "pending_checkers": list(o.pending_checkers),
        "requires_ai": o.requires_ai,
        "depends_on": list(o.depends_on),
    }
    for o in OBLIGATION_CATALOG
]

# 4. Checklist layer
out["checklists"] = {}
for kind in ("budget", "final"):
    doc = load_checklist(kind)
    out["checklists"][kind] = {
        "version": doc.get("version"),
        "validation_problems": validate_checklist(kind),
        "items": [
            {
                "id": i.get("id"),
                "status": i.get("status"),
                "decision": i.get("decision"),
                "obligations": i.get("obligations") or [],
                "rules": i.get("rules") or [],
                "title": (i.get("title") or "")[:60],
            }
            for i in doc["items"]
        ],
    }

# 5. Cross-layer correspondence
reg_ids = {r["rule_id"] for r in out["registry"]["rules"]}
yaml_codes = {r["code"] for r in out["yaml_rules"]}
yaml_aliases = {a for r in out["yaml_rules"] for a in r["aliases"]}
out["cross"] = {
    "yaml_codes_not_in_registry": sorted(yaml_codes - reg_ids),
    "registry_ids_not_in_yaml": sorted(reg_ids - yaml_codes),
    "registry_ids_matching_yaml_alias": sorted(reg_ids & yaml_aliases),
    "yaml_enabled_false": [r["code"] for r in out["yaml_rules"] if not r["enabled"]],
    "yaml_executor_counts": {},
}
for r in out["yaml_rules"]:
    c = out["cross"]["yaml_executor_counts"]
    c[r["executor"]] = c.get(r["executor"], 0) + 1

print(json.dumps(out, ensure_ascii=False, indent=1))