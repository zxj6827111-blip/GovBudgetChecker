"""在已确认的原生行列内融合逐格 OCR；原网格与可用原生金额保持独立来源。"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from src.engine.fiscal_lexeme import amount_in_wan
from src.services.fiscal_column_identity import canonical_column_mappings
from src.services.fiscal_table_rules import NINE_TABLE_RULES, normalize_text
from src.services.input_processing_identity import content_hash
from src.services.ocr_qualification import qualification
from src.services.source_reconstruction import native_source_conflict
from src.services.table_import_receipt import source_cell_id


def _same_grid_cell(a, b):
    if not a or not b:
        return False
    area_a,area_b=(a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1])
    intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return min(area_a,area_b)>0 and intersection/max(area_a,area_b)>=.9


def _labels(table, row, money_columns):
    return normalize_text(''.join(c.raw_text for c in sorted(table.cells,key=lambda c:c.col_idx)
                                 if c.row_idx==row and c.col_idx not in money_columns))


def fuse_overlapping_cells(artifact,candidate,*,table_code,mappings,organization,year,report_kind):
    """按原生格、完整列身份和同一行标签对齐；歧义继续待裁定。"""
    result={'fused_cells':[],'conflicts':[],'rejected':[]}
    if table_code not in NINE_TABLE_RULES:
        return result
    from src.services.table_reading_assessment import intersects
    matches=[(i,t) for i,t in enumerate(artifact.table_fragments)
             if t.table_code==table_code and t.extraction_method in {'lines','lines_strict'}
             and candidate.page_bbox and intersects(t.bbox,candidate.page_bbox)
             and any(c.page_number==candidate.page_number for c in t.cells)]
    if len(matches)!=1:
        return result
    table_index,native=matches[0]
    instances=[i for i in artifact.logical_tables if i.instance_uid==native.fragment_uid
               or native.fragment_uid in i.fragment_uids]
    if (len(instances)!=1 or instances[0].identity_conflict or native.confidence<.8
            or instances[0].org_name!=organization or instances[0].fiscal_year!=year):
        return result
    native_maps=canonical_column_mappings(native,NINE_TABLE_RULES[table_code])
    native_money={m['source_col_idx']:m['canonical_measure'] for m in native_maps}
    ocr_money={m['source_col_idx']:m['canonical_measure'] for m in mappings}
    native_headers={m['source_col_idx']:normalize_text(m.get('source_col_name')or'') for m in native_maps}
    ocr_headers={m['source_col_idx']:normalize_text(m.get('source_col_name')or'') for m in mappings}
    if len(native_money)!=len(native_maps) or len(ocr_money)!=len(mappings) or native.unit_hint is None:
        return result
    changed=None
    for observed in candidate.cells:
        col,row=observed.get('col_idx'),observed.get('row_idx')
        if col not in ocr_money or observed.get('row_span',1)!=1 or observed.get('col_span',1)!=1:
            continue
        qualified=qualification(candidate,artifact,organization=organization,year=year,
                                report_kind=report_kind,cell_position=(row,col),native_field_alignment=True)
        if qualified['status']!='qualified' or qualified['unit']!=native.unit_hint:
            continue
        amount=amount_in_wan(observed.get('raw_text'),qualified['unit'])
        if amount is None:
            continue
        targets=[c for c in native.cells if not c.is_header and c.page_number==candidate.page_number
                 and native_money.get(c.col_idx)==ocr_money[col] and c.row_span==1 and c.col_span==1
                 and _same_grid_cell(c.bbox,observed.get('page_bbox'))]
        if len(targets)!=1:
            result['rejected'].append({'row':row,'col':col,'reason':'native_cell_geometry_not_unique'})
            continue
        target=targets[0]
        if not native_headers.get(target.col_idx) or native_headers[target.col_idx]!=ocr_headers.get(col):
            result['rejected'].append({'row':row,'col':col,'reason':'native_column_stage_differs'})
            continue
        from src.services.ocr_pipeline import _pdf_bbox_from_render_bbox
        transform=artifact.coordinate_transforms[candidate.coordinate_transform_id]
        from src.services.ocr_grid_reconstruction import GRID_PROTOCOL
        grid_source=candidate.provenance.get('schema_version')==GRID_PROTOCOL
        from src.services.input_geometry import Affine
        fragment_boxes=[Affine(*transform['pixel_to_pdf_matrix']).box(f['grid_bbox'])if grid_source
                        else _pdf_bbox_from_render_bbox(f.get('bbox')or f.get('render_bbox'),transform)
                        for f in observed.get('fragments',[])]
        if not fragment_boxes or any(b is None or b[0]<target.bbox[0] or b[1]<target.bbox[1]
                                    or b[2]>target.bbox[2] or b[3]>target.bbox[3] for b in fragment_boxes):
            result['rejected'].append({'row':row,'col':col,'reason':'ocr_fragment_outside_native_cell'})
            continue
        ocr_label=normalize_text(''.join(str(v or '')for i,v in enumerate(candidate.rows[row])if i not in ocr_money))
        native_label=_labels(native,target.row_idx,native_money)
        matrix_row=(not native_label and not ocr_label and table_code=='FIN_07_three_public'
                    and len(set(native_headers.values()))==len(native_headers))
        if not matrix_row and (not ocr_label or native_label!=ocr_label):
            result['rejected'].append({'row':row,'col':col,'reason':'native_row_identity_differs'})
            continue
        source_id=source_cell_id(native,target)
        if native_source_conflict(target):
            result['rejected'].append({'source_cell_id':source_id,'reason':'native_source_requires_adjudication'})
            continue
        original_amount=amount_in_wan(target.raw_text,native.unit_hint)
        if original_amount is not None and original_amount==amount:
            continue
        # 认证空白和破折号不能被一次高分OCR凭空变成金额。
        if target.native_blank_verified or target.raw_text.strip() in {'-','—','–','－'}:
            result['rejected'].append({'source_cell_id':source_id,'reason':'explicit_blank_or_placeholder'})
            continue
        if changed is None:
            changed=deepcopy(native)
            artifact.table_fragments[table_index]=changed
        cell=next(c for c in changed.cells if (c.row_idx,c.col_idx)==(target.row_idx,target.col_idx))
        proof={'native_source_cell_id':source_id,'native_raw_text':target.raw_text,
               'ocr_candidate_uid':candidate.candidate_uid,'ocr_raw_text':observed['raw_text'],
               'ocr_cell':deepcopy(observed),'ocr_source':deepcopy(candidate.provenance),
               'basis':'same_physical_grid_row_labels_column_unit_and_qualified_ocr'}
        if original_amount is not None:
            conflict={'recovery_kind':'unresolved_native_ocr_conflict',**proof}
            if not any(p.get('ocr_candidate_uid')==candidate.candidate_uid for p in cell.native_text_sources):
                cell.native_text_sources.append(conflict)
            result['conflicts'].append({'source_cell_id':source_id,'native_raw_text':target.raw_text,
                                        'ocr_raw_text':observed['raw_text']})
            _record_reconstruction(artifact,native,target,observed,candidate,source_id,conflict=True)
            continue
        if target.extraction_method=='ocr':
            continue
        # 仅逻辑格选择OCR来源；原始物理格和 source_grids 从不被改写。
        fragment=deepcopy(native)
        prediction_hash=candidate.provenance.get('raw_prediction_sha256')or candidate.provenance.get('table_hash')
        fragment.fragment_uid=content_hash(['native-ocr-cell',source_id,prediction_hash])[:24]
        cell.raw_text=str(observed['raw_text']);cell.normalized_text=cell.raw_text
        from src.schemas.parse_artifact import CellPresence
        cell.presence=CellPresence.detect(cell.raw_text)
        factor={'元':Decimal('.0001'),'万元':Decimal(1),'亿元':Decimal(10000)}[qualified['unit']]
        cell.numeric_value=float(amount/factor)
        cell.normalized_value_wan=amount;cell.extraction_method='ocr'
        cell.confidence=observed.get('score')
        cell.origin_fragment_uid=fragment.fragment_uid
        cell.origin_row_idx=cell.row_idx;cell.origin_col_idx=cell.col_idx
        cell.ocr_provenance={'qualification':qualified,'source':deepcopy(candidate.provenance),
                             'cell':deepcopy(observed),'native_alignment':proof}
        cell.native_text_sources=[]
        changed.rows[cell.row_idx][cell.col_idx]=cell.raw_text
        fragment.cells=[deepcopy(cell)];fragment.rows=deepcopy(changed.rows)
        fragment.extraction_method='ocr';fragment.page_number=candidate.page_number
        fragment.bbox=tuple(cell.bbox)
        if not any(t.fragment_uid==fragment.fragment_uid for t in artifact.physical_fragments):
            artifact.physical_fragments.append(fragment)
        result['fused_cells'].append({'source_cell_id':source_id,'selected_source_cell_id':source_cell_id(fragment,cell),
                                      'raw_text':cell.raw_text,'qualification':qualified})
        _record_reconstruction(artifact,native,cell,observed,candidate,source_id,conflict=False)
    return result


def _record_reconstruction(artifact,table,cell,observed,candidate,native_source_id,*,conflict):
    from src.schemas.source_reconstruction import SourceToken,CellCandidate,ResolvedLogicalCell
    from src.services.ocr_pipeline import _pdf_bbox_from_render_bbox
    from src.services.source_reconstruction import typed_literal
    transform=artifact.coordinate_transforms[candidate.coordinate_transform_id]
    prediction_hash=candidate.provenance.get('raw_prediction_sha256')or candidate.provenance.get('table_hash')
    token_ids=[]
    for fragment in observed.get('fragments',[]):
        box=fragment.get('bbox')or fragment.get('render_bbox')
        source_transform=artifact.coordinate_transforms.get(fragment.get('coordinate_transform_id'),transform)
        uid=content_hash([prediction_hash,fragment])[:24]
        token_ids.append(uid)
        if not any(t.token_uid==uid for t in artifact.source_tokens):
            artifact.source_tokens.append(SourceToken(uid,fragment['text'],candidate.page_number,
                _pdf_bbox_from_render_bbox(box,source_transform)if box else None,'ocr',fragment.get('score'),
                {'coordinate_transform_id':candidate.coordinate_transform_id,
                 'raw_prediction_sha256':fragment.get('raw_prediction_sha256')or prediction_hash}))
    source=source_cell_id(table,cell)
    candidate_uid=content_hash([candidate.candidate_uid,observed['row_idx'],observed['col_idx'],native_source_id])[:24]
    if not any(c.candidate_uid==candidate_uid for c in artifact.cell_candidates):
        artifact.cell_candidates.append(CellCandidate(candidate_uid,source,table.fragment_uid,candidate.page_number,
            observed.get('page_bbox'),observed['raw_text'],'ocr',cell.row_idx,cell.col_idx,token_uids=token_ids))
    resolved=[c for c in artifact.resolved_cells if c.table_instance_uid==table.fragment_uid
              and (c.row_idx,c.col_idx)==(cell.row_idx,cell.col_idx)]
    if not resolved:
        resolved=[ResolvedLogicalCell(content_hash([table.fragment_uid,cell.row_idx,cell.col_idx])[:24],
            table.fragment_uid,[],cell.row_idx,cell.col_idx,cell.page_number,list(cell.bbox),
            cell.raw_text,'unrecognized',None,cell.unit_hint,'unresolved')]
        artifact.resolved_cells.extend(resolved)
    for logical in resolved:
        logical.source_cell_ids=sorted(set(logical.source_cell_ids+[native_source_id,source]))
        logical.resolution_status='unresolved'if conflict else'source_bound'
        if not conflict:
            logical.raw_text=cell.raw_text
            logical.value_kind,logical.decimal_value=typed_literal(cell.raw_text,monetary=True)
        receipt={'kind':'native_ocr_cell_conflict'if conflict else'native_grid_ocr_cell_fusion',
                 'native_source_cell_id':native_source_id,'ocr_candidate_uid':candidate_uid,
                 'ocr_raw_prediction_sha256':prediction_hash}
        if receipt not in logical.provenance:
            logical.provenance.append(receipt)
        if receipt not in artifact.reconstruction_decisions:
            artifact.reconstruction_decisions.append(receipt)
