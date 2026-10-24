原生表格结构恢复的固定回归数据（v23）

这五个 JSON 是用户提供的普陀决算原件中的字符位置、原网格与读取探针快照，不是由修复后的表格反推的期望。
source 字段保留原件文件名、SHA256 与物理页；page.chars 的顺序保持原页字符索引不变；table 为修复前 v22 的原始物理表。
测试期望的 19 个金额和科目在 test_native_structure_recovery.py 中单独定义，已对原 PDF 的渲染及独立 PyMuPDF 文本位置核对。

open_fiscal_tail：就业促进中心第 8 页，支出末行四个金额。
open_basic_tail：就业促进中心第 11 页，31022 无形资产购置。
missing_money_grid：眼病牙病防治所第 12 页，三个合计金额。
decimal_wrap：精神卫生中心第 5 页，三个小数尾数换行的金额。
merged_rows：机关事务管理局第 9 页，四个科目的合计/基本支出共八个金额。

运行：python -m pytest tests/remediation/test_native_structure_recovery.py tests/remediation/test_native_structure_pdf_pipeline.py
后一组测试实时生成有相同结构缺陷的 PDF，经公开解析入口到财政事实校验；禁用恢复入口时四例均失败，防止只保留辅助模块却漏接产品调用链。
全量 CI 的 python -m pytest 自动包含两组测试，不依赖原件在特定电脑上的路径。

这些是开发回归样本；未知材料、真实扫描材料的独立验收仍须另外冻结原页真值。
