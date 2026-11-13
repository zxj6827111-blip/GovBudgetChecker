"""R5 regression: replay equality, stats re-derivation, full frame-noun scan of originals."""
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
CODE = ROOT.parent / 'gbc-worktrees/auto-check-remediation-r22'
OUT = Path(__file__).resolve().parent
D5 = ROOT / 'outputs/r22_rework5_20261107'
D4 = ROOT / 'outputs/r22_rework4_20261107'
sys.path.insert(0, str(CODE))

def norm_summary(path):
    return [{k: v for k, v in j.items() if k != 'elapsed'}
            for j in json.loads(Path(path).read_text())]

mine = norm_summary(OUT / 'replay/summary.json')
print('我的r5重放 == ZCode r5交付重放:', mine == norm_summary(D5 / 'candidate_replay/summary.json'))
print('我的r5重放 == 我的r4重放(33877665):', mine == norm_summary(ROOT / 'outputs/r4_closeout_verify_qoder_20261107/replay/summary.json'))
print('我的r5重放 == 审核方r5重放(33877665):', mine == norm_summary(ROOT / 'outputs/r22_independent_review_r5_codex_20261107/candidate_replay/summary.json'))
for j in json.loads((OUT / 'replay/summary.json').read_text()):
    full = json.loads((OUT / f"replay/{j['job']}.json").read_text())
    zs = {k: bool(v) for k, v in full['zero_sources'].items()}
    prev = json.loads((ROOT / f"outputs/r4_closeout_verify_qoder_20261107/replay/{j['job']}.json").read_text())
    zs_prev = {k: bool(v) for k, v in prev['zero_sources'].items()}
    print('  ', j['job'][:8], 'zero_sources:', zs, '与r4一致:', zs == zs_prev)

spec = importlib.util.spec_from_file_location('collector', CODE / 'scripts/phase0_r22_target_relations.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
collector._run_relations_stats(str(D5 / 'relations_before_main.json'),
                               str(D5 / 'relations_after_candidate.json'),
                               str(OUT / 'stats_rederived.json'))
rederived = json.loads((OUT / 'stats_rederived.json').read_text())
delivered = json.loads((D5 / 'three_dimension_stats.json').read_text())
print('重导出统计 == r5交付统计:', json.dumps(rederived, sort_keys=True) == json.dumps(delivered, sort_keys=True))
r4 = json.loads((D4 / 'three_dimension_stats.json').read_text())
strip = lambda s: {k: v for k, v in s.items() if k not in ('before_meta', 'after_meta')}
print('r5统计(除meta) == r4统计:', json.dumps(strip(rederived), sort_keys=True) == json.dumps(strip(r4), sort_keys=True))
unknown = [r for r in rederived['relations'] if r['engine_dimension'] == 'unknown']
print('unknown总数:', len(unknown),
      '仅历史归因:', len([r for r in unknown if r['attributions'] == ['resolved_before_this_plan']]))
rec = [r for r in rederived['relations'] if 'recovered_this_round' in r['attributions']]
print('恢复关系:', [(r['relation_id'].split('::')[-1], r['evidence_nature']) for r in rec])

import pdfplumber
from src.services.pdf_page_extract import extract_visible_text_from_page
from src.engine.native_narrative_checks import _Text
from src.engine.narrative_zero_evidence import (_FRAME_COLON_OPEN_RE, _FRAME_TITLE_LINE_RE,
                                                _FRAME_NOUN_RE, _FRAME_CLOSURE_RE)

jobs = ['79efdd8f0d7b3a2938de65cfb5aad101', 'b589d6bd4436edaa140a224135bf10d0',
        '02e4fb64ab6bbf325117d325a2dcbfa4', 'f1a4c441a3bd02ec4cce465b1739f046']
print('四原件全帧机制扫描(冒号片段/未终结行/裸帧词出现):')
for job in jobs:
    folder = ROOT / 'uploads' / job
    stored = json.loads((folder / 'status.json').read_text())
    with pdfplumber.open(folder / stored['filename']) as pdf:
        texts = [extract_visible_text_from_page(page) for page in pdf.pages]
    t = _Text.from_pages(texts)
    colon = [m.group(0) for m in _FRAME_COLON_OPEN_RE.finditer(t.compact)]
    title = [ln.strip()[:30] for ln in t.raw.splitlines()
             if ln.strip() and _FRAME_TITLE_LINE_RE.match(ln.strip())]
    words = [(m.group(0), t.compact[max(0, m.start()-12):m.end()+12]) for m in _FRAME_NOUN_RE.finditer(t.compact)]
    print('  ', job[:8], stored['filename'])
    print('     colon_hits:', colon, 'unterminated_line_hits:', title)
    print('     裸帧词出现:', words if words else '无')