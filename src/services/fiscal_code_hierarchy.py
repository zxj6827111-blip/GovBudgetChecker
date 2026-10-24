"""按同一表已确认的编码格式解释层级，保留原编码作为事实身份。"""
from __future__ import annotations


def hierarchy_paths(codes):
    codes = set(codes)
    numeric = {c for c in codes if c.isdigit() and len(c) in (3, 5, 7)}
    # 七位尾零格式必须在当前表同时出现类级和其下级，不能逐个截掉尾零。
    padded = bool(numeric and len(numeric) == len(codes)
                  and all(len(c) == 7 for c in numeric)
                  and any(c.endswith('0000') and any(
                      other != c and other.startswith(c[:3]) for other in numeric)
                      for c in numeric))
    result = {}
    for code in codes:
        if code not in numeric:
            continue
        if padded:
            result[code] = (code[:3] if code.endswith('0000') else
                            code[:5] if code.endswith('00') else code)
            continue
        path = code
        # 混排表里个别类/款行写成补零 7 位（文旅局 2022：“21011”旁边是“2109900”）。
        # 项级编码从 01 起编，不会以 00 结尾；只有同表另有该类/款下的 7 位明细作证、
        # 且没有同名的 3/5 位行时，才按类/款解释。
        if len(code) == 7 and code.endswith('00'):
            stem = code[:3] if code.endswith('0000') else code[:5]
            if stem not in codes and any(other != code and len(other) == 7 and other.startswith(stem)
                                         and not other.endswith('00') for other in numeric):
                path = stem
        result[code] = path
    return result


def leaf_codes(codes):
    paths = hierarchy_paths(codes)
    if len(set(paths.values())) != len(paths):
        raise ValueError('classification hierarchy contains duplicate semantic paths')
    return {code for code, path in paths.items()
            if not any(other != code and candidate.startswith(path)
                       for other, candidate in paths.items())}
