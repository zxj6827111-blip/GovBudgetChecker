"""表间勾稽实测：FIN_01 收入/支出总计 vs FIN_02/FIN_03 合计。

用于判定"数据不全"到底是解析丢失还是规则假设不符。
"""
from __future__ import annotations

import glob
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.pdf_parser import PDFParser  # noqa: E402


def to_num(s) -> Decimal | None:
    if s is None:
        return None
    t = re.sub(r"[,，\s]", "", str(s))
    if not t or not re.fullmatch(r"-?\d+(?:\.\d+)?", t):
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def sum_rows(rows, col, want_len, prefix=None):
    total = Decimal(0)
    n = 0
    for r in rows:
        if col >= len(r):
            continue
        code = str(r[0] or "").strip()
        if not (code.isdigit() and len(code) == want_len):
            continue
        if prefix and not code.startswith(prefix):
            continue
        v = to_num(r[col])
        if v is not None:
            total += v
            n += 1
    return total, n


def main() -> None:
    files = sorted(glob.glob("uploads/*/*.pdf"))
    print(f"{'文件':<22} {'FIN02合计':>14} {'类和':>14} {'FIN03合计':>14} {'类和':>14} 判定")
    print("-" * 100)
    bad = 0
    for f in files:
        try:
            art = PDFParser(None).build_parse_artifact(f)
        except Exception as e:  # noqa: BLE001
            print(f"{Path(f).name[:20]:<22} PARSE_FAIL {e}")
            continue
        t2 = [t for t in art.table_fragments if t.table_code == "FIN_02_income"]
        t3 = [t for t in art.table_fragments if t.table_code == "FIN_03_expenditure"]
        if not t2 or not t3:
            print(f"{Path(f).name[:20]:<22} 缺表 FIN02={bool(t2)} FIN03={bool(t3)}")
            bad += 1
            continue
        t2 = max(t2, key=lambda x: len(x.rows))
        t3 = max(t3, key=lambda x: len(x.rows))

        # 合计行：找第 0 列或第 3 列为“合计”的行，金额取该行最后一个可用数值列
        def grand(rows):
            for r in rows:
                joined = "".join(str(c or "") for c in r[:4])
                if "合计" in joined:
                    for c in r[1:]:
                        v = to_num(c)
                        if v is not None:
                            return v
            return None

        # 口径：FIN_02 第4列(本年收入合计)，FIN_03 第4列(本年支出合计)
        s2, n2 = sum_rows(t2.rows, 4, 3)
        s3, n3 = sum_rows(t3.rows, 4, 3)
        g2, g3 = grand(t2.rows), grand(t3.rows)
        flag = []
        if g2 is None or abs(s2 - g2) > Decimal("0.5"):
            flag.append("收入不合")
        if g3 is None or abs(s3 - g3) > Decimal("0.5"):
            flag.append("支出不合")
        if flag:
            bad += 1
        print(f"{Path(f).name[:20]:<22} {str(g2):>14} {str(s2):>14} {str(g3):>14} {str(s3):>14} "
              f"{' '.join(flag) or 'OK'}  [类级行 {n2}/{n3}]")
    print("-" * 100)
    print(f"异常 {bad}/{len(files)}")


if __name__ == "__main__":
    main()
