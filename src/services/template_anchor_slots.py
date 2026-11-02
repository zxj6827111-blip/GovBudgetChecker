"""锚点槽位使用审核过的归一文字坐标；固定引用、金额及编码保持原文。"""
from __future__ import annotations


def validated_slots(anchor, source_year):
    text=anchor['text']
    slots=anchor.get('slots') or []
    if not slots and (anchor.get('annual_slot') or anchor.get('organization_slot')):
        raise ValueError('旧版槽位未记录具体位置，请重新逐位置审核')
    if not isinstance(slots,list) or len(slots)>100 or any(not isinstance(s,dict) or type(s.get('start')) is not int or type(s.get('end')) is not int for s in slots):
        raise ValueError('槽位列表及位置格式无效')
    result=[]
    previous_end=0
    for item in sorted(slots,key=lambda x:x['start']):
        start,end=item.get('start'),item.get('end')
        kind=item.get('kind')
        if type(start) is not int or type(end) is not int or not 0<=start<end<=len(text) or start<previous_end:
            raise ValueError('槽位位置无效或重叠')
        original=text[start:end]
        if item.get('source_text',original)!=original:raise ValueError('槽位原文与审核位置不一致')
        if kind in ('report_year','comparison_year'):
            if (start and text[start-1].isdigit()) or not text[end:].startswith('年'):
                raise ValueError('金额和科目编码不能作为年度槽位')
            prefix=text[:start]
            if prefix.rfind('《')>prefix.rfind('》'):raise ValueError('固定文献年份不能作为年度槽位')
            expected=str(source_year) if type(source_year) is int else '20XX'
            if kind=='comparison_year':
                if type(source_year) is not int:raise ValueError('源报告年度未知，不能确认同比基期槽位')
                expected=str(source_year-1)
            if original!=expected:raise ValueError('槽位与源报告年度或同比基期不一致')
        elif kind=='organization_name':
            if original!=anchor.get('organization_slot') or len(original)<3:
                raise ValueError('名称槽位须对应已核实的完整机构名称')
        else:raise ValueError('槽位类型无效')
        result.append({**item,'start':start,'end':end,'kind':kind,'source_text':original})
        previous_end=end
    return result


def instantiate_anchor(anchor, source_year, target_year, organization=None, semantic=False):
    text=anchor['text']
    slots=validated_slots(anchor,source_year)
    for slot in reversed(slots):
        kind=slot['kind']
        if semantic:value={'report_year':'{REPORT_YEAR}','comparison_year':'{COMPARISON_YEAR}','organization_name':'{ORGANIZATION}'}[kind]
        elif kind=='organization_name':
            if not organization:raise ValueError('模板名称槽位缺少已核实机构名称')
            value=organization
        else:
            if type(target_year) is not int:raise ValueError('目标报告年度尚未核实')
            value=str(target_year-(kind=='comparison_year'))
        text=text[:slot['start']]+value+text[slot['end']:]
    return text
