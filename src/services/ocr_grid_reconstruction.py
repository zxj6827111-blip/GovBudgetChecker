"""以独立像素网格绑定多个 OCR 图块；组合证据不冒充模型原始表格预测。"""
from __future__ import annotations

import base64
import hashlib
import io
import math
import re
from copy import deepcopy

from PIL import Image

from src.services.input_geometry import Affine
from src.services.input_processing_identity import content_hash
from src.services.raster_grid_candidates import discover_grid_cells

GRID_PROTOCOL = 'gbc.ocr.grid-cells.v4'


def _contains(outer, inner):
    return (inner and outer[0] <= inner[0] < inner[2] <= outer[2]
            and outer[1] <= inner[1] < inner[3] <= outer[3])


def _overlap(a, b):
    intersection = max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return intersection/min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1]))


def _components(cells):
    """相接的闭合格组成物理表；同页彼此分离的表不拼接。"""
    pending=set(range(len(cells)));groups=[]
    while pending:
        group=[pending.pop()];frontier=list(group)
        while frontier:
            a=cells[frontier.pop()]['bbox'];connected=[]
            for i in pending:
                b=cells[i]['bbox']
                vertical=(abs(a[2]-b[0])<.01 or abs(b[2]-a[0])<.01) and min(a[3],b[3])>max(a[1],b[1])
                horizontal=(abs(a[3]-b[1])<.01 or abs(b[3]-a[1])<.01) and min(a[2],b[2])>max(a[0],b[0])
                if vertical or horizontal:connected.append(i)
            pending.difference_update(connected);group.extend(connected);frontier.extend(connected)
        if len(group)>1:groups.append([deepcopy(cells[i])for i in sorted(group)])
    return groups


def _source_tokens(artifact, page, affine, page_image_sha):
    """只读取有原始预测、实际输入尺寸和完整请求身份的文字片段。"""
    from src.services.ocr_qualification import bound_response, page_binding_reasons, complete_first_pass_cell
    from src.schemas.parse_artifact import OcrTableCandidate
    tokens=[]
    originals={t.get('tile_context',{}).get('original_page_render_sha256',page_image_sha)
        for t in artifact.coordinate_transforms.values()if t.get('physical_page')==page
        and t.get('tile_context',{}).get('attempt')==0
        and t.get('tile_context',{}).get('parent_image_sha256')==page_image_sha}
    for transform_id,transform in sorted(artifact.coordinate_transforms.items()):
        if transform.get('transform_version')!='pdf-tile-v3' or transform.get('physical_page')!=page:
            continue
        source=transform.get('ocr_response')or{}
        from ocr_service.protocol import PROTOCOL_VERSION
        if source.get('schema_version')!=PROTOCOL_VERSION:continue
        context=transform.get('tile_context')or{}
        if context.get('attempt')==0 and context.get('parent_image_sha256')!=page_image_sha:continue
        if context.get('attempt')!=0 and context.get('original_page_render_sha256')not in originals:continue
        probe=OcrTableCandidate('probe',page,[],None,None,coordinate_transform_id=transform_id,
                                provenance={**source,'source_sha256':artifact.source_file_hash,
                                            'render_sha256':transform.get('render_sha256')})
        if page_binding_reasons(probe,artifact) or detector_input_reasons(source):continue
        response=bound_response(source)
        if response is None:continue
        local_to_grid=Affine(*context['pixel_to_pdf']).then(affine.inverse())
        observations=[(index,b)for index,b in enumerate(response.get('blocks',[]))]
        for table_index,table in enumerate(response.get('tables',[])):
            for cell in table.get('cells',[]):
                observations.extend(([table_index,cell['row_idx'],cell['col_idx'],index],fragment)
                    for index,fragment in enumerate(cell.get('fragments',[])))
        for index,block in observations:
            if not block.get('bbox') or not str(block.get('text')or'').strip():continue
            fragment={'text':block['text'],'score':block.get('score'),'bbox':block['bbox']}
            # 截断边缘和补读片段需要完整图像上下文，不能靠高分绕过。
            if not complete_first_pass_cell({'fragments':[fragment]},context):continue
            box=local_to_grid.box(block['bbox'])
            tokens.append({**fragment,'grid_bbox':box,'coordinate_transform_id':transform_id,
                           'source_block_index':index,'raw_prediction_sha256':source['raw_prediction_sha256']})
    return tokens


def detector_input_reasons(source):
    size=source.get('source_size_px')or[]
    if len(size)!=2:return ['actual_detector_input_unverified']
    padding=[0,0,(-size[0])%32,(-size[1])%32];expected=[size[0]+padding[2],size[1]+padding[3]]
    if (source.get('detector_input_verified')is not True or source.get('input_resize_applied')is not False
        or source.get('model_input_size_px')!=expected or source.get('inference_padding_px')!=padding
        or not source.get('detector_tensor_sizes_px')or source['detector_tensor_sizes_px'][0]!=expected):
        return ['actual_detector_input_unverified']
    return []


def reconstruct_grid_tables(image, affine, artifact, page, dpi=300):
    """纯重放函数：网格来自像素，字面与分数来自各自原始响应。"""
    from src.services.ocr_tiles import _visible_text_ink
    import numpy as np
    from src.services.ocr_tiles import png_bytes
    grid=discover_grid_cells(image,dpi=dpi)
    tokens=_source_tokens(artifact,page,affine,hashlib.sha256(png_bytes(image)).hexdigest())
    # 斜扫、断续网线的残段不是未读文字；与补读调度用同一套独立线检测，否则正确读出的格也会因残线判「未识别」。
    tables=[];ink=_visible_text_ink(image,independent_lines=True)
    # 仅排除已被四边闭合格使用的实测网线带，不能按固定容差把邻格文字抹掉。
    for border in grid.get('border_segments',[]):
        start,end=border['band']
        for a,b in border['spans']:
            if border['direction']=='horizontal':ink[start:end,a:b]=False
            else:ink[a:b,start:end]=False
    for token in tokens:
        raw=token['grid_bbox']
        if any(_contains(c['bbox'],raw)for c in grid['cells']):continue
        x0,y0=max(0,int(raw[0])),max(0,int(raw[1]))
        x1,y1=min(image.width,int(raw[2])+1),min(image.height,int(raw[3])+1)
        ys,xs=np.where(ink[y0:y1,x0:x1])
        if not len(xs):continue
        tight=[int(xs.min())+x0,int(ys.min())+y0,int(xs.max())+x0+1,int(ys.max())+y0+1]
        matches=[c for c in grid['cells']if _contains(c['bbox'],tight)]
        if len(matches)==1:
            token['geometry_binding']={'basis':'all_visible_ink_after_measured_grid_lines',
                'raw_grid_bbox':raw,'visible_ink_bbox':tight,'border_segments_hash':content_hash(grid['border_segments'])}
            token['grid_bbox']=tight
    for group in _components(grid['cells']):
        xs=sorted({c['bbox'][i]for c in group for i in (0,2)})
        ys=sorted({c['bbox'][i]for c in group for i in (1,3)})
        rows=[[''for _ in xs[:-1]]for _ in ys[:-1]];cells=[]
        for physical in group:
            box=physical['bbox'];fragments=[];conflicts=[]
            for token in tokens:
                if not _contains(box,token['grid_bbox']):continue
                duplicates=[f for f in fragments if _overlap(f['grid_bbox'],token['grid_bbox'])>.8]
                if duplicates:
                    if any(f['text']!=token['text']for f in duplicates):conflicts.append(token)
                    # 相同片段只保留原始分数最高的实际观测；不同字面绝不投票。
                    elif token.get('score',0)>duplicates[0].get('score',0):
                        fragments.remove(duplicates[0]);fragments.append(token)
                else:fragments.append(token)
            fragments.sort(key=lambda f:(round(f['grid_bbox'][1]/5),f['grid_bbox'][0]))
            raw='\n'.join(f['text'].strip()for f in fragments)
            row,col=ys.index(box[1]),xs.index(box[0]);rs=ys.index(box[3])-row;cs=xs.index(box[2])-col
            # 未被文字框覆盖的可见墨迹继续未裁定；空格保持空格。
            y0,y1=int(box[1])+3,int(box[3])-3;x0,x1=int(box[0])+3,int(box[2])-3
            remaining=ink[y0:y1,x0:x1].copy()
            for f in fragments:
                # 1 像素栅格容差须向外取整：878.9999 截成 878 会让字形最右一列像素落在框外。
                b=f['grid_bbox'];left=max(x0,math.floor(b[0])-1);right=min(x1,math.ceil(b[2])+2)
                top=max(y0,math.floor(b[1])-1);bottom=min(y1,math.ceil(b[3])+2)
                if right>left and bottom>top:remaining[top-y0:bottom-y0,left-x0:right-x0]=False
            status='conflict'if conflicts else'unrecognized'if np.any(remaining)else'bound'if raw else'blank'
            cells.append({'row_idx':row,'col_idx':col,'row_span':rs,'col_span':cs,
                'raw_text':raw,'render_bbox':box,'page_bbox':affine.box(box),'fragments':fragments,
                'score':min((f.get('score')or 0 for f in fragments),default=None),
                'literal_status':status,'conflicting_fragments':conflicts})
            rows[row][col]=raw
        tables.append({'rows':rows,'cells':cells,'bbox':[xs[0],ys[0],xs[-1],ys[-1]],
                       'mapping_status':'mapped'})
    return tables


def attach_grid_candidates(artifact, page, image, affine, dpi=300):
    from src.schemas.parse_artifact import OcrTableCandidate
    from src.services.ocr_tiles import png_bytes
    data=png_bytes(image);render_sha=hashlib.sha256(data).hexdigest()
    tables=reconstruct_grid_tables(image,affine,artifact,page,dpi)
    prefix=f'ocr_grid_page_{page}_'
    artifact.ocr_table_candidates=[c for c in artifact.ocr_table_candidates
                                    if not c.coordinate_transform_id.startswith(prefix)]
    # 一个完整页图只保存一份；候选格引用它，不复制成每张表的原始预测。
    transform_id=prefix+content_hash([render_sha,affine.values()])[:24]
    artifact.coordinate_transforms[transform_id]={'transform_version':'image-grid-v1','physical_page':page,
        'source_sha256':artifact.source_file_hash,'render_sha256':render_sha,
        'render_png_base64':base64.b64encode(data).decode('ascii'),
        'pixel_to_pdf_matrix':affine.values(),'protocol':GRID_PROTOCOL,'render_dpi':dpi}
    for index,table in enumerate(tables):
        provenance={'schema_version':GRID_PROTOCOL,'source_sha256':artifact.source_file_hash,
                    'render_sha256':render_sha,'table_index':index,'table_hash':content_hash(table),
                    'model_fingerprint':artifact.ocr_model_fingerprint}
        artifact.ocr_table_candidates.append(OcrTableCandidate(transform_id+f':table-{index}',page,
            table['rows'],tuple(table['bbox']),tuple(affine.box(table['bbox'])),
            coordinate_transform_id=transform_id,cells=table['cells'],mapping_status='mapped',provenance=provenance))
    return len(tables)


def bound_grid_table(candidate,artifact):
    transform=artifact.coordinate_transforms.get(candidate.coordinate_transform_id)or{}
    reasons=[]
    try:
        if (transform.get('transform_version')!='image-grid-v1'
            or transform.get('source_sha256')!=artifact.source_file_hash
            or transform.get('physical_page')!=candidate.page_number
            or candidate.provenance.get('source_sha256')!=artifact.source_file_hash):
            raise ValueError('grid_source_identity_mismatch')
        data=base64.b64decode(transform['render_png_base64'],validate=True)
        if hashlib.sha256(data).hexdigest()!=transform.get('render_sha256'):
            raise ValueError('grid_render_hash_mismatch')
        if candidate.provenance.get('render_sha256')!=transform['render_sha256']:
            raise ValueError('grid_candidate_render_mismatch')
        affine=Affine(*transform['pixel_to_pdf_matrix'])
        # 每格复验仍校验完整依赖摘要；同一不可变输入无需反复扫描整页像素。
        dependencies={tid:value for tid,value in artifact.coordinate_transforms.items()
                      if value.get('physical_page')==candidate.page_number
                      and value.get('transform_version')=='pdf-tile-v3'}
        key=content_hash([transform,dependencies])
        cache=getattr(artifact,'_grid_replay_cache',{})
        if key not in cache:
            image=Image.open(io.BytesIO(data)).convert('RGB')
            # 网线粗细与最短线段按渲染 DPI 换算；旧产物没有记录时按当时固定的 300 DPI 重放。
            cache={key:reconstruct_grid_tables(image,affine,artifact,candidate.page_number,
                                               transform.get('render_dpi',300))}
            artifact._grid_replay_cache=cache
        tables=cache[key]
        index=candidate.provenance['table_index'];canonical=tables[index]
        if (content_hash(canonical)!=candidate.provenance.get('table_hash')
            or candidate.rows!=canonical['rows']or candidate.cells!=canonical['cells']
            or list(candidate.page_bbox or[])!=affine.box(canonical['bbox'])):
            raise ValueError('grid_cells_not_bound_to_raw_predictions')
        return canonical,reasons
    except (KeyError,ValueError,IndexError,TypeError,OSError)as error:
        return None,[str(error)]


def stitched_identity(blocks,organization):
    """图块切线会把一行「编制部门：…办公室」切成首尾重叠的几段（「…建设办公」「设办公室」）。

    同页且框相交的两段按首尾重叠字（至少 2 字）缝合，缝出完整主体名才算证据；
    多组可缝时取两段中较低分最高的一组。两段都要作为身份读数接受原始预测绑定与分数检查。
    """
    best=[]
    for a in blocks:
        for b in blocks:
            if (a is b or a.page_number!=b.page_number or not a.bbox or not b.bbox
                    or not organization or _overlap(a.bbox,b.bbox)<=0):
                continue
            k=next((k for k in range(min(len(a.text),len(b.text))-1,1,-1)if a.text.endswith(b.text[:k])),0)
            if k and organization in a.text+b.text[k:] and (
                    not best or min(x.confidence or 0 for x in (a,b))>min(x.confidence or 0 for x in best)):
                best=[a,b]
    return best


def qualify_grid(candidate,artifact,*,organization,year,report_kind,cell_position=None,native_field_alignment=False):
    from src.services.ocr_qualification import bound_response, complete_first_pass_cell
    from src.services.table_reading_assessment import native_region_conflicts
    table,reasons=bound_grid_table(candidate,artifact);scores=[];unit=None
    page_blocks=[b for b in artifact.text_blocks if b.page_number==candidate.page_number and b.extraction_method=='ocr']
    identity=[b for b in artifact.text_blocks if b.extraction_method=='ocr' and (
        organization and organization in b.text or year and re.search(rf'{year}\s*年',b.text))]
    text='\n'.join(b.text for b in page_blocks)
    units=[(b,m.group(1))for b in page_blocks for m in re.finditer(r'单位[:：]\s*(万元|亿元|元)',b.text)]
    if not units or len({u for _,u in units})!=1:reasons.append('unit_missing_or_conflicting')
    else:unit=units[0][1]
    if organization and not any(organization in b.text for b in identity):
        identity+=stitched_identity([b for b in artifact.text_blocks if b.extraction_method=='ocr'],organization)
    if not organization or not (any(organization in b.text for b in identity)
                                or stitched_identity(identity,organization)):reasons.append('original_subject_missing')
    if not year or not any(re.search(rf'{year}\s*年',b.text)for b in identity):reasons.append('original_year_missing')
    stage='决算'if report_kind=='final'else'预算'
    stage_blocks=[b for b in page_blocks if stage in b.text]
    if report_kind not in {'budget','final'}or not stage_blocks:reasons.append('original_stage_missing')
    years=set(re.findall(r'(20\d{2})\s*年',text))
    if years and years!={str(year)}:reasons.append('year_scope_conflict')
    owners=re.findall(r'(?:编制单位|编制部门|预算单位|决算单位)[:：]\s*([^\n:：]+)',text)
    expected=''.join(str(organization or'').split())
    # 图块边界会把「编制部门：上海推进科技创新中心建设办公室」切成几段；只剩主体名称开头
    # 的残段不是另一个主体。完整主体仍须由 original_subject_missing 另行证明。
    if any(not (owner==expected or expected.startswith(owner))
           for owner in {''.join(o.split())for o in owners}):
        reasons.append('table_subject_conflict')
    if candidate.provenance.get('model_fingerprint')!=artifact.ocr_model_fingerprint:
        reasons.append('model_hash_mismatch')
    ocr_blocks=[b for b in artifact.text_blocks if b.extraction_method=='ocr' and b.bbox]

    def superseded(block):
        """重叠图块对同一处的残缺低分读数：同页同位置已有 ≥0.98 的完整读数时不再拉低身份分数。

        字面冲突（如单位一处读成「元」一处读成「万元」）仍按全部读数判定，这里只管分数。
        """
        return block.bbox and any(other is not block and other.page_number==block.page_number
            and (other.confidence or 0)>=.98 and _overlap(other.bbox,block.bbox)>=.5 for other in ocr_blocks)

    for block in identity+stage_blocks+[b for b,_ in units]:
        tid=block.block_uid.split(':block-',1)[0];transform=artifact.coordinate_transforms.get(tid)or{}
        response=transform.get('ocr_response')or{};raw=bound_response(response)or{}
        from src.schemas.parse_artifact import OcrTableCandidate
        from src.services.ocr_qualification import page_binding_reasons
        probe=OcrTableCandidate('identity-probe',block.page_number,[],coordinate_transform_id=tid,
            provenance={**response,'source_sha256':artifact.source_file_hash,'render_sha256':transform.get('render_sha256')})
        local=Affine(*transform['pixel_to_pdf_matrix'])if transform.get('pixel_to_pdf_matrix')else None
        if (page_binding_reasons(probe,artifact)or transform.get('physical_page')!=block.page_number
            or transform.get('source_sha256')!=artifact.source_file_hash
            or detector_input_reasons(response)or response.get('model_fingerprint')!=artifact.ocr_model_fingerprint
            or not any(i['text']==block.text and i.get('score')==block.confidence and i.get('bbox')
                       and local and list(block.bbox or[])==local.box(i['bbox'])for i in raw.get('blocks',[]))):
            reasons.append('identity_not_bound_to_raw_prediction')
        if (block.confidence or 0)>=.98 or not superseded(block):
            scores.append(block.confidence or 0)
    if not native_field_alignment and native_region_conflicts(artifact,candidate):
        reasons.append('native_OCR_conflict_requires_review')
    if table:
        first_data=next((i for i,row in enumerate(table['rows'])
                        if any(re.fullmatch(r'[+-]?\d+(?:\.\d+)?',v.strip())for v in row)),1)
        selected=[c for c in table['cells']if cell_position is None or c['row_idx']<first_data
                  or c['row_idx']==cell_position[0]]
        for cell in selected:
            if cell['literal_status']in {'conflict','unrecognized'}:reasons.append('grid_literal_unresolved')
            for fragment in cell['fragments']:
                transform=artifact.coordinate_transforms[fragment['coordinate_transform_id']]
                source=transform.get('ocr_response')or{}
                if source.get('model_fingerprint')!=artifact.ocr_model_fingerprint:
                    reasons.append('model_hash_mismatch')
                if not complete_first_pass_cell({'fragments':[fragment]},transform.get('tile_context')):
                    reasons.append('cell_boundary_requires_regional_recovery')
                box=cell['page_bbox']
                if any(min(box[2],r[2])>max(box[0],r[0])and min(box[3],r[3])>max(box[1],r[1])
                       for r in transform.get('page_recovery',{}).get('unresolved_pdf_regions',[])):
                    reasons.append('cell_region_recovery_incomplete')
                scores.append(fragment.get('score')or 0)
    if not scores or min(scores)<.98:reasons.append('critical_recognition_score_below_threshold')
    return {'status':'rejected'if reasons else'qualified','reasons':sorted(set(reasons)),
        'minimum_score':min(scores,default=0),'unit':unit,'organization':organization,'year':year,
        'report_kind':report_kind,'evidence_verification':'unverified','physical_page':candidate.page_number,
        'protocol_version':GRID_PROTOCOL,'operand_binding_version':'independent-grid-source-blocks-v1',
        'coordinate_transform_id':candidate.coordinate_transform_id}
