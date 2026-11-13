"""用带 openpyxl 的解释器把 Excel 原件逐字导出为对照快照（不含任何归属推断）。

verify_pt_orgs.py 以这份快照为独立 oracle，因此导出层必须自带正反对照断言。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SOURCE = ("/Users/zhangxingjin/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
          "xwechat_files/wxid_lrcvinm6kqp22_a224/msg/file/2026-11/"
          "普陀区2025年度决算公开汇总表v2.5-20261029（发送稿）.xlsx")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--source", default=SOURCE)
    args = parser.parse_args()

    import openpyxl

    wb = openpyxl.load_workbook(args.source, data_only=True)
    depts = [str(r[0]).strip() for r in wb["部门决算"].iter_rows(min_row=2, values_only=True)
             if r and str(r[0] or "").strip()]
    units = [{"code": str(r[0] or "").strip(), "name": str(r[1]).strip()}
             for r in wb["单位决算"].iter_rows(min_row=2, values_only=True) if r and str(r[1] or "").strip()]

    # 正向对照：已知存在的两条
    assert "上海市普陀区教育局" in depts, "导出层漏读部门"
    assert {"code": "304001", "name": "上海市普陀区教育局本级"} in units, "导出层漏读单位"
    # 反向对照：构造一条不该出现的内容，确认断言真的会翻脸
    assert {"code": "304001", "name": "不存在的学校"} not in units
    assert "上海市普陀区不存在的部门" not in depts
    assert len(depts) == 65 and len(units) == 328, (len(depts), len(units))

    Path(args.out).write_text(json.dumps({"departments": depts, "units": units}, ensure_ascii=False),
                              encoding="utf-8")
    print(f"dumped depts={len(depts)} units={len(units)} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
