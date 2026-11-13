"""R5 independent controls: length never authorizes; word-level conservative scope; positives preserved."""
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
def case(name, pages, er, eo, kind):
    actual = run(pages)
    ok = actual['reception'] == er and actual['overseas'] == eo
    cases.append({'name': name, 'kind': kind,
                  'expected': {'reception': er, 'overseas': eo}, 'actual': actual, 'ok': ok})

# 长度无关性：阈值两侧及更远（审核方要求不只测公布的17字位置）
case('审核方A 17字标题', [BASE + '\n财政拨款三公经费支出填报示例说明：\n' + STMT], False, False, 'required')
case('审核方B 22字标题', [BASE + '\n本年度财政拨款三公经费支出决算填报示例说明：\n' + STMT], False, False, 'required')
case('外推 40字前置修饰标题', [BASE + '本年度财政拨款三公经费支出决算填报以及公开工作相关注意事项示例说明：' + STMT], False, False, 'required')
case('外推 帧词后30字修饰', [BASE + '示例' + '样式及填表格式与相关注意事项的详细说明文字以及补充规定内容补充：' + STMT], False, False, 'required')
case('无冒号未终结长行', [BASE + '\n本年度财政拨款三公经费支出决算填报示例样式说明\n' + STMT], False, False, 'required')
# 词级保守建帧（5885bcf6 自检补强）
case('已终结正文句含帧词', [BASE + '示例见附表。' + STMT], False, False, 'required')
case('词级跨页', [BASE + '示例见附表。', '因公出国（境）费支出决算0万元。'], False, False, 'required')
case('同句演示数据', [BASE + '财政拨款公务接待费支出决算0万元（演示数据）。因公出国（境）费支出决算0万元。'], False, False, 'required')
case('同句假定', [BASE + '财政拨款公务接待费支出决算0万元（假定）。因公出国（境）费支出决算0万元。'], False, False, 'required')
case('同句虚构', [BASE + '财政拨款公务接待费支出决算0万元（虚构）。因公出国（境）费支出决算0万元。'], False, False, 'required')
# 闭合语义：闭合短语内帧词不重开作用域
case('复合闭合短语不重开', [BASE + '示例：财政拨款公务接待费支出决算0万元。以上为模板示例。因公出国（境）费支出决算0万元。'], False, True, 'required')
case('简单闭合后授权', [BASE + '示例样式：财政拨款公务接待费支出决算0万元。以上示例仅供参考。因公出国（境）费支出决算0万元。'], False, True, 'required')
# 正控：不得误拒
case('无宣告合法零', [BASE + STMT], True, True, 'positive')
case('无帧词冒号片段', [BASE + '填报说明：' + STMT], True, True, 'positive')
case('真实语序具体情况如下', [BASE + '具体情况如下：' + STMT], True, True, 'positive')
case('零陈述在帧词之前(事实先行)', [BASE + '因公出国（境）费支出决算0万元。示例见附表。财政拨款公务接待费支出决算0万元。'], False, True, 'positive')

out = Path(__file__).with_name('r5_matrix_results.json')
out.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + '\n')
for c in cases:
    print(('OK ' if c['ok'] else 'MISMATCH ') + c['kind'] + ' | ' + c['name'] + ' -> ' + json.dumps(c['actual'], ensure_ascii=False))
bad = [c for c in cases if not c['ok']]
print('失配数:', len(bad), [c['name'] for c in bad])