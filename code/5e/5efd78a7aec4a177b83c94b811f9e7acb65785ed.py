"""Independent closeout verification: re-derive delivered stats + scan colon-frame hits on originals."""
import importlib.util
import json
from pathlib import Path
import sys
from collections import Counter

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
CODE = ROOT.parent / 'gbc-worktrees/auto-check-remediation-r22'
OUT = Path(__file__).resolve().parent
DELIVERY = ROOT / 'outputs/r22_rework3_20261107'
sys.path.insert(0, str(CODE))

spec = importlib.util.spec_from_file_location('collector', CODE / 'scripts/phase0_r22_target_relations.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

collector._run_relations_stats(
    str(DELIVERY / 'relations_before_main.json'),
    str(DELIVERY / 'relations_after_candidate.json'),
    str(OUT / 'stats_rederived.json'))
rederived = json.loads((OUT / 'stats_rederived.json').read_text())
delivered = json.loads((DELIVERY / 'three_dimension_stats.json').read_text())
print('重导出统计 == 交付统计(逐字节级深度相等):',
      json.dumps(rederived, sort_keys=True) == json.dumps(delivered, sort_keys=True))

unknown = [r for r in rederived['relations'] if r['engine_dimension'] == 'unknown']
print('unknown 总数:', len(unknown))
dist = Counter(tuple(r['attributions']) for r in unknown)
print('unknown 归因分布:')
for k, v in dist.most_common():
    print('  ', list(k), v)
historical_only = [r for r in unknown if r['attributions'] == ['resolved_before_this_plan']]
print('unknown 仅历史归因:', len(historical_only))
no_current = [r for r in unknown if not (set(r['attributions']) & {
    'tech_unresolved', 'corrected_previous_skip', 'original_material_missing',
    'record_missing', 'new_finding'})]
print('unknown 无任何当前缺口原因:', len(no_current), [r['relation_id'] for r in no_current][:5])

import pdfplumber
from src.services.pdf_page_extract import extract_visible_text_from_page
from src.engine.native_narrative_checks import _Text
from src.engine.narrative_zero_evidence import _FRAME_COLON_OPEN_RE, _FRAME_ANNOUNCEMENT_OPEN_RE

jobs = ['79efdd8f0d7b3a2938de65cfb5aad101', 'b589d6bd4436edaa140a224135bf10d0',
        '02e4fb64ab6bbf325117d325a2dcbfa4', 'f1a4c441a3bd02ec4cce465b1739f046']
print('四原件裸冒号帧扫描:')
for job in jobs:
    folder = ROOT / 'uploads' / job
    stored = json.loads((folder / 'status.json').read_text())
    pdf_path = folder / stored['filename']
    with pdfplumber.open(pdf_path) as pdf:
        texts = [extract_visible_text_from_page(page) for page in pdf.pages]
    compact = _Text.from_pages(texts).compact
    colon_hits = [m.group(0) for m in _FRAME_COLON_OPEN_RE.finditer(compact)]
    fwd_hits = len(list(_FRAME_ANNOUNCEMENT_OPEN_RE.finditer(compact)))
    print('  ', job[:8], stored['filename'], 'colon_hits:', colon_hits, 'forward_ref_hits:', fwd_hits)