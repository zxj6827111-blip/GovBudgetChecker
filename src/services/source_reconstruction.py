"""统一来源索引与逐格裁定；候选不会自动成为第二张业务表。"""
from __future__ import annotations
import re
import unicodedata
from decimal import Decimal,InvalidOperation
from src.schemas.source_reconstruction import SourceToken,CellCandidate,ResolvedLogicalCell
from src.services.input_processing_identity import content_hash
from src.services.fiscal_table_rules import normalize_text,NINE_TABLE_RULES


def typed_literal(raw, *, monetary=False, dimension=False):
    original=unicodedata.normalize('NFKC',''if raw is None else str(raw)).strip()
    if not original:return 'blank',None
    if original in {'-','—','–','－','--'}:return 'dash',None
    if any(x in original for x in ('无此项','不涉及')):return 'not_applicable',None
    text=original.replace('−','-').replace('－','-')
    # 同格中的独立数字词不能通过删除空格拼成另一笔金额。
    if re.search(r'\d[ \t\u3000]+\d',text):return 'unrecognized',None
    text=re.sub(r'\s+','',text)
    for unit in ['百万元','亿元','万元','千元','元']:
        if text.endswith(unit):
            text=text[:-len(unit)]
            break
    percent=text.endswith('%')
    if percent:text=text[:-1]
    if text.startswith('(')and text.endswith(')'):text='-'+text[1:-1]
    if not re.fullmatch(r'[+-]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?',text):return 'text',None
    try:value=Decimal(text.replace(',','').replace('，',''))
    except InvalidOperation:return 'unrecognized',None
    if dimension:return 'code',str(value)
    if percent:return 'percent',str(value)
    return ('zero'if value==0 else'amount'if monetary else'number'),str(value)


def _intersection(a,b):
    return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))


def native_source_conflict(cell):
    """字符恢复和可见网格不一致时，两条通道都保留，统一判为待裁定。"""
    if cell is None:
        return False
    sources = cell.native_text_sources
    if any(str(p.get('recovery_kind', '')).startswith('unresolved_') for p in sources):
        return True
    return (cell.grid_raw_text is not None
            and normalize_text(cell.grid_raw_text) != normalize_text(cell.raw_text)
            and any(p.get('recovery_kind') == 'cross_grid_decimal_tail' for p in sources))


def resolve_empty_table_shadows(tables, page_text):
    """同一空表的文本策略投影需与原生闭合网格及原页无此项说明共同裁定。"""
    from src.services.fiscal_column_identity import canonical_column_mappings
    selected=[]
    decisions=[]
    compact=normalize_text(page_text)
    for text in tables:
        if text.extraction_method!='text' or text.table_code not in {'FIN_08_gov_fund','FIN_09_state_capital'}:
            selected.append(text)
            continue
        grids=[g for g in tables if g.extraction_method in {'lines','lines_strict'}and
               g.table_code==text.table_code and g.page_number==text.page_number and
               normalize_text(g.title)==normalize_text(text.title)]
        matches=[]
        for grid in grids:
            min_area=min((text.bbox[2]-text.bbox[0])*(text.bbox[3]-text.bbox[1]),
                         (grid.bbox[2]-grid.bbox[0])*(grid.bbox[3]-grid.bbox[1]))
            if min_area<=0 or _intersection(text.bbox,grid.bbox)/min_area<.6:continue
            columns={m['source_col_idx']for m in canonical_column_mappings(grid,NINE_TABLE_RULES[grid.table_code])}
            # 空表可能把最后一行也判成表头；按原生网格和空白认证判定，不能
            # 因上游 header_row_count 偏大再生成一个业务表实例。
            body=[c for c in grid.cells if c.row_idx > 0 and c.col_idx in columns]
            header_words={'合计','基本支出','项目支出','本年支出','本年收入','年初结转和结余','年末结转和结余'}
            blanks=[c for c in body if not c.raw_text.strip() and c.bbox]
            if not blanks or any(not c.native_blank_verified for c in blanks):continue
            if any(c.raw_text.strip() and c.numeric_value!=0 and normalize_text(c.raw_text)not in header_words for c in body):continue
            if any(c.numeric_value not in (None,0)for c in text.cells if not c.is_header):continue
            label='政府性基金'if grid.table_code=='FIN_08_gov_fund'else'国有资本经营'
            if label not in compact or not any(note in compact for note in ['本表无数据','本表为空表','故本表无数据']):continue
            matches.append(grid)
        if len(matches)==1:
            decisions.append({'candidate_uid':text.fragment_uid,'selected_table_uid':matches[0].fragment_uid,
                              'status':'duplicate_projection','reason':'certified_empty_grid_and_original_no_activity_note'})
        else:selected.append(text)
    return selected,decisions


def attach_reconstruction(artifact, candidates):
    from src.services.fiscal_column_identity import canonical_column_mappings,code_dimension_columns,header_paths
    from src.services.table_import_receipt import source_cell_id
    tokens=[]
    for b in artifact.text_blocks:
        tokens.append(SourceToken(b.block_uid,b.text,b.page_number,list(b.bbox)if b.bbox else None,
                                  b.extraction_method,b.confidence,{'source_file_hash':artifact.source_file_hash,'source_block_id':b.block_uid}))
    by_page={}
    for t in tokens:by_page.setdefault(t.physical_page,[]).append(t)
    cells=[]
    seen=set()
    for table in candidates:
        for cell in table.cells:
            cid=source_cell_id(table,cell)
            candidate_uid=content_hash([table.fragment_uid,cid,table.extraction_method])[:24]
            if candidate_uid in seen:continue
            seen.add(candidate_uid)
            refs=[]
            if cell.bbox:
                refs=[t.token_uid for t in by_page.get(cell.page_number,[])if t.bbox and
                      cell.bbox[0]-.1<=(t.bbox[0]+t.bbox[2])/2<=cell.bbox[2]+.1 and
                      cell.bbox[1]-.1<=(t.bbox[1]+t.bbox[3])/2<=cell.bbox[3]+.1]
            cells.append(CellCandidate(candidate_uid,cid,table.fragment_uid,cell.page_number,
                list(cell.bbox)if cell.bbox else None,cell.raw_text,table.extraction_method,
                cell.row_idx,cell.col_idx,cell.row_span,cell.col_span,refs))
    resolved=[]
    for table in artifact.table_fragments:
        rule=NINE_TABLE_RULES.get(table.table_code)
        mappings=canonical_column_mappings(table,rule)if rule else []
        money={m['source_col_idx']:m for m in mappings}
        dimensions=code_dimension_columns(table,header_paths(table))
        for c in table.cells:
            kind,value=typed_literal(c.raw_text,monetary=c.col_idx in money,dimension=c.col_idx in dimensions)
            mapping=money.get(c.col_idx,{})
            conflict = native_source_conflict(c)
            if c.extraction_method=='ocr' and c.ocr_provenance.get('qualification',{}).get('status')!='qualified':
                conflict=True
            provenance = list(c.native_text_sources)
            if conflict:
                provenance.append({'kind': 'unresolved_source_conflict', 'grid_raw_text': c.grid_raw_text,
                                   'content_stream_candidate': c.raw_text, 'requires_adjudication': True})
            resolved.append(ResolvedLogicalCell(content_hash([table.fragment_uid,c.row_idx,c.col_idx])[:24],
                table.fragment_uid,[source_cell_id(table,c)],c.row_idx,c.col_idx,c.page_number,
                list(c.bbox)if c.bbox else None,c.raw_text,kind,value,c.unit_hint,
                'candidate'if table.extraction_method=='page_text_fallback'else'unresolved'if conflict or kind=='unrecognized'else'source_bound',
                {'indicator':mapping.get('canonical_measure'),'table_code':table.table_code},
                provenance))
    artifact.source_tokens=tokens
    artifact.cell_candidates=cells
    artifact.resolved_cells=resolved


def resolve_native_table_candidates(policy, existing, incoming):
    """所有提取策略和修复候选在此唯一裁定业务表，物理来源保持独立。"""
    merged = existing[:]
    for candidate in incoming:
        replaced = False
        for idx, current in enumerate(merged):
            if current.page_number != candidate.page_number:
                continue
            overlap = policy._iou(current.bbox, candidate.bbox)
            containment = policy._containment_ratio(current.bbox, candidate.bbox)
            # Text strategy can include cover/narration above and below a
            # ruled grid, while clipping the final column. It is another
            # extraction of the same physical grid, not a second table.
            # Relax containment only for matching known codes and explicit
            # text-versus-lines evidence; keep other instances ambiguous.
            methods = {current.extraction_method, candidate.extraction_method}
            same_grid_shadow = (
                current.table_code == candidate.table_code
                and not current.table_code.startswith("UNKNOWN_")
                and "text" in methods
                and bool(methods & {"lines", "lines_strict"})
                and containment >= 0.3
                and policy._text_grid_shadow(current, candidate)
            )
            if containment >= 0.9 and "text" in methods and bool(methods & {"lines", "lines_strict"}):
                from src.services.native_dimension_recovery import repaired_native_grid_shadow
                same_grid_shadow |= repaired_native_grid_shadow(current, candidate)
            if overlap < 0.75 and containment < 0.9 and not same_grid_shadow:
                continue
            if same_grid_shadow:
                if current.extraction_method == "text":
                    merged[idx] = candidate
            elif "text" in methods and bool(methods & {"lines", "lines_strict"}):
                # 同一位置的闭合原生网格已有明确格边界。文本策略的高分
                # 不能替换它而丢失空列/原格位置；无网格时仍使用文本策略。
                if current.extraction_method == "text":
                    merged[idx] = candidate
            elif policy._table_priority(candidate) > policy._table_priority(current):
                merged[idx] = candidate
            replaced = True
            break
        if not replaced:
            merged.append(candidate)
    from copy import deepcopy
    from src.engine.fiscal_lexeme import amount_in_wan
    for index,selected in enumerate(merged):
        alternatives=[t for t in existing+incoming if t is not selected
            and t.page_number==selected.page_number and t.table_code==selected.table_code
            and t.extraction_method in {'lines','lines_strict'}
            and selected.extraction_method in {'lines','lines_strict'}
            and policy._containment_ratio(t.bbox,selected.bbox)>=.9]
        changed=None
        for cell in selected.cells:
            if cell.is_header or not cell.bbox:continue
            for other in alternatives:
                if other.unit_hint!=selected.unit_hint:continue
                matches=[c for c in other.cells if c.bbox and not c.is_header
                    and c.row_idx==cell.row_idx and c.col_idx==cell.col_idx
                    and min(c.bbox[2]-c.bbox[0],cell.bbox[2]-cell.bbox[0])>0
                    and _intersection(c.bbox,cell.bbox)/max(
                        (c.bbox[2]-c.bbox[0])*(c.bbox[3]-c.bbox[1]),
                        (cell.bbox[2]-cell.bbox[0])*(cell.bbox[3]-cell.bbox[1]))>=.95]
                if len(matches)!=1:continue
                candidate=matches[0]
                first=amount_in_wan(cell.raw_text,selected.unit_hint)
                second=amount_in_wan(candidate.raw_text,other.unit_hint)
                if first is None or second is None or first==second:continue
                if changed is None:
                    changed=deepcopy(selected)
                    merged[index]=changed
                target=next(c for c in changed.cells if(c.row_idx,c.col_idx)==(cell.row_idx,cell.col_idx))
                proof={'recovery_kind':'unresolved_native_candidate_conflict',
                    'selected_fragment_uid':selected.fragment_uid,'selected_raw_text':cell.raw_text,
                    'candidate_fragment_uid':other.fragment_uid,'candidate_raw_text':candidate.raw_text,
                    'bbox':list(cell.bbox),'candidate_bbox':list(candidate.bbox)}
                if proof not in target.native_text_sources:target.native_text_sources.append(proof)
    return merged
