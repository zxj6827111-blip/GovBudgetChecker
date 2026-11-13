"""故意切断关键修复的入口；相关固定原页回归必须失败，源码文件不改写。"""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]
if len(sys.argv)>1:
    sys.path.insert(0,str(ROOT))
    import pytest
    from src.services.pdf_parser import PDFParser
    from src.services.fiscal_table_rules import NINE_TABLE_RULES
    mode=sys.argv[1]
    if mode=='continuation':
        PDFParser._align_native_continuation_columns=lambda self,previous,current:current
        selector='all_fixed_source and continuation'
    else:
        for code in ('FIN_08_gov_fund','FIN_09_state_capital'):
            rule=NINE_TABLE_RULES[code]
            NINE_TABLE_RULES[code]=replace(rule,measure_aliases={k:v for k,v in rule.measure_aliases.items() if k not in {'beginning_balance','ending_balance'}})
        selector='all_fixed_source and balance'
    raise SystemExit(pytest.main(['-q','--tb=short','tests/remediation/test_native_fiscal_coverage.py','-k',selector]))
results=[]
for mode,failures in [('continuation',2),('balances',6)]:
    result=subprocess.run([sys.executable,str(__file__),mode],cwd=ROOT,capture_output=True,text=True)
    (BASE/f'negative_{mode}.log').write_text(result.stdout+result.stderr)
    assert result.returncode==1 and f'{failures} failed' in result.stdout,(mode,result.stdout[-3000:])
    results.append(dict(control=mode,expected_failed_tests=failures,exit_code=result.returncode,mutated_source_files=False))
(BASE/'negative_controls.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print(results)
