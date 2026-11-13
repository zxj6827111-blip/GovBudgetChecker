"""R4 independent structural controls: vary frame-word position/modifiers, not just the two public samples."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

CODE = Path('/Volumes/workSSD/software development/gbc-worktrees/auto-check-remediation-r22')
sys.path.insert(0, str(CODE))
from src.engine.narrative_zero_evidence import narrative_zero_sources

BASE = '甲局2024年度部门决算。'
STMT = '财政拨款公务接待费支出决算0万元。因公出国（境）费支出决算0万元。'

def run(pages):
    doc = SimpleNamespace(page_texts=pages,
                          fact_context=SimpleNamespace(org_name='甲局', fiscal_year=2024),
                          report_kind='final')
    return {i: bool(narrative_zero_sources(doc, table_code='FIN_07_three_public',
                                           indicator=i, stage='决算'))
            for i in ('reception', 'overseas')}

cases = []
def case(name, pages, expect_reception, expect_overseas, kind):
    actual = run(pages)
    ok = actual['reception'] == expect_reception and actual['overseas'] == expect_overseas
    cases.append({'name': name, 'kind': kind, 'expected': {'reception': expect_reception, 'overseas': expect_overseas},
                  'actual': actual, 'ok': ok})

# 结构矩阵：同一帧词"示例"在标题不同位置、前后修饰（审核方要求超出两个公开样例）
case('帧词居首+后置修饰', [BASE + '示例样式及填表格式说明：' + STMT], False, False, 'required')
case('帧词居中', [BASE + '关于示例的说明：' + STMT], False, False, 'required')
case('帧词后置+前置修饰', [BASE + '本表填报格式样式示例：' + STMT], False, False, 'required')
case('另一帧词假定+修饰', [BASE + '假定情形说明：' + STMT], False, False, 'required')
case('半角冒号', [BASE + '示例样式:' + STMT], False, False, 'required')
case('独立标题行无冒号', [BASE + '\n示例样式\n' + STMT], False, False, 'required')
case('标题行跨页未闭合', [BASE + '模板示例说明：', '因公出国（境）费支出决算0万元。'], False, False, 'required')
case('结构宣告+显式闭合后合法零', [BASE + '示例样式：财政拨款公务接待费支出决算0万元。以上示例仅供参考。因公出国（境）费支出决算0万元。'], False, True, 'required')
# 正控：不含帧词的片段不得建帧
case('无帧词冒号片段', [BASE + '填报说明：' + STMT], True, True, 'positive')
case('真实语序具体情况如下', [BASE + '具体情况如下：' + STMT], True, True, 'positive')
case('三公经费决算情况说明标题', [BASE + '三公经费决算情况说明：' + STMT], True, True, 'positive')
case('无宣告合法零', [BASE + STMT], True, True, 'positive')
# 披露边界实测：超长标题（帧词距冒号>24字）——ZCode披露不建帧，验证披露属实
case('超长后置修饰(29字>24,披露边界)', [BASE + '示例' + '样式及填表格式与相关注意事项的详细说明文字以及补充规定内容' + '：' + STMT], False, True, 'disclosed_boundary')
# 披露边界实测：帧词在片段16字之外
case('超长前置修饰(>16字,披露边界)', [BASE + '本表填报格式样式以及相关注意事项的说明示例：' + STMT], False, True, 'disclosed_boundary')

out = Path(__file__).with_name('structural_matrix_results.json')
out.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + '\n')
for c in cases:
    print(('OK ' if c['ok'] else 'MISMATCH ') + c['kind'] + ' | ' + c['name'] + ' -> ' + json.dumps(c['actual'], ensure_ascii=False))
required_bad = [c for c in cases if not c['ok'] and c['kind'] != 'disclosed_boundary']
print('required/positive 失配数:', len(required_bad))