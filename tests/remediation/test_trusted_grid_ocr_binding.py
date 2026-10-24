"""独立图像网格与真实响应协议的控制样本；不代表材料准确率。"""
import copy
import hashlib

import pytest
from PIL import Image, ImageDraw

from src.schemas.parse_artifact import ParseArtifact,ParsedTextBlock
from src.services.input_geometry import Affine
from src.services.input_processing_identity import content_hash
from src.services.ocr_grid_reconstruction import attach_grid_candidates,bound_grid_table
from src.services.ocr_qualification import qualification
from src.services.ocr_tiles import make_tile,png_bytes


def fixture(*,bbox_spill=False,neighboring_ink=False,extra=()):
    image=Image.new('RGB',(680,260),'white');draw=ImageDraw.Draw(image)
    for x in (30,180,330,480,630):draw.line((x,70,x,210),fill='black',width=2)
    for y in (70,140,210):draw.line((30,y,630,y),fill='black',width=2)
    entries=[('测试局2025年收入决算表 单位：万元',[30,20,550,40]),
             ('科目编码',[50,90,150,110]),('科目名称',[200,90,300,110]),
             ('本年收入合计',[350,90,460,110]),('财政拨款收入',[500,90,610,110]),
             ('合计',[200,160,300,180]),('100.00',[350,160,460,180]),('100.00',[500,160,610,180])]
    for _,box in entries:draw.rectangle(box,fill='black')
    if bbox_spill:
        draw.rectangle(entries[-1][1],fill='white')
        draw.text((505,165),'100.00',fill='black')
        entries[-1]=(entries[-1][0],[500,160,642 if neighboring_ink else 634,180])
    if neighboring_ink:draw.rectangle([636,165,640,175],fill='black')
    scores=[.995]*len(entries)+[score for _,_,score in extra]
    entries+=[(text,box)for text,box,_ in extra]
    raw={'overall_ocr_res':{'rec_texts':[text for text,_ in entries],
        'rec_scores':scores,'rec_polys':[[[b[0],b[1]],[b[2],b[1]],
            [b[2],b[3]],[b[0],b[3]]]for _,b in entries]}}
    data=png_bytes(image);context=make_tile(image,(0,0,*image.size),parent_sha=hashlib.sha256(data).hexdigest(),
        file_hash='a'*64,page=1,pixel_to_pdf=Affine()).context
    source={'schema_version':'gbc.ocr.cells.v3','source_sha256':'a'*64,
        'render_sha256':context['input_sha256'],'input_sha256':context['input_sha256'],
        'source_size_px':list(image.size),'model_fingerprint':'b'*64,'pipeline_fingerprint':'c'*64,
        'raw_prediction':raw,'raw_prediction_sha256':content_hash(raw),'tile_context':context,
        'detector_input_verified':True,'input_resize_applied':False,'inference_padding_px':[0,0,24,28],
        'model_input_size_px':[704,288],'detector_tensor_sizes_px':[[704,288]]}
    tid='ocr_tile_'+context['tile_id']
    artifact=ParseArtifact(source_file_hash='a'*64,page_count=1,ocr_model_fingerprint='b'*64,
        coordinate_transforms={tid:{'transform_version':'pdf-tile-v3','physical_page':1,
            'source_sha256':'a'*64,'render_sha256':context['input_sha256'],'render_size_px':list(image.size),
            'tile_context':context,'pixel_to_pdf_matrix':context['pixel_to_pdf'],
            'pdf_to_pixel_matrix':context['pdf_to_pixel'],'ocr_response':source,
            'bbox_transform_verified':True,'page_recovery':{},
            'page_request':{'physical_page':1,**{k:source[k]for k in ('source_sha256','render_sha256',
                'input_sha256','schema_version','raw_prediction_sha256','model_fingerprint','pipeline_fingerprint')}}}},
        text_blocks=[ParsedTextBlock(tid+f':block-{i}',1,text,tuple(box),scores[i],'ocr')
                     for i,(text,box)in enumerate(entries)])
    attach_grid_candidates(artifact,1,image,Affine())
    return artifact,artifact.ocr_table_candidates[0]


def test_measured_grid_line_pixels_do_not_move_a_word_into_a_neighbor():
    artifact,candidate=fixture(bbox_spill=True)
    last=candidate.cells[-1]
    assert last['raw_text']=='100.00'and last['fragments'][0]['bbox'][2]==634
    assert last['fragments'][0]['geometry_binding']['visible_ink_bbox'][2]<630
    assert qualification(candidate,artifact,organization='测试局',year=2025,report_kind='final')['status']=='qualified'
    artifact,candidate=fixture(bbox_spill=True,neighboring_ink=True)
    assert qualification(candidate,artifact,organization='测试局',year=2025,report_kind='final')['status']=='rejected'


def test_independent_grid_binds_occurrences_and_retains_blank_without_zero():
    artifact,candidate=fixture();table,reasons=bound_grid_table(candidate,artifact)
    assert not reasons and table['rows']==[['科目编码','科目名称','本年收入合计','财政拨款收入'],
                                         ['','合计','100.00','100.00']]
    assert sum(c['raw_text']=='100.00'for c in table['cells'])==2
    blank=next(c for c in table['cells']if(c['row_idx'],c['col_idx'])==(1,0))
    assert blank['literal_status']=='blank'and blank['raw_text']==''
    result=qualification(candidate,artifact,organization='测试局',year=2025,report_kind='final')
    assert result['status']=='qualified',result
    restored=ParseArtifact.from_dict(artifact.to_dict())
    assert bound_grid_table(restored.ocr_table_candidates[0],restored)[0]==table
    from src.services.ocr_fact_promotion import promote_ocr_candidates,_ocr_number
    from src.engine.fact_context import build_fact_context_from_artifact
    summary=promote_ocr_candidates(artifact,organization='测试局',year=2025,report_kind='final')
    assert summary['usable_for_rules']and artifact.resolved_cells
    context=build_fact_context_from_artifact(artifact,org_name='测试局',fiscal_year=2025,report_kind='final')
    assert len(context.facts)==2 and all(f.normalized_value==100 for f in context.facts)
    assert _ocr_number('10\n0.00')is None and _ocr_number('(100.00)')==-100


def test_tile_cut_fragments_of_the_header_lines_do_not_block_qualification():
    # 重叠图块把表题读出一个残缺低分版本；同一位置已有 0.995 的完整读数。
    title='测试局2025年收入决算表 单位：万元'
    artifact,candidate=fixture(extra=[(title[:12],[30,20,330,40],.92)])
    assert qualification(candidate,artifact,organization='测试局',year=2025,report_kind='final')['status']=='qualified'
    # 只读到主体名称开头的「编制单位：测试」不是另一个主体；「其他局」才是。
    artifact.text_blocks.append(ParsedTextBlock('cut',1,'编制单位：测试',None,.999,'ocr'))
    assert qualification(candidate,artifact,organization='测试局',year=2025,report_kind='final')['status']=='qualified'
    artifact.text_blocks.append(ParsedTextBlock('foreign',1,'编制单位：其他局',None,.999,'ocr'))
    result=qualification(candidate,artifact,organization='测试局',year=2025,report_kind='final')
    assert 'table_subject_conflict'in result['reasons']
    # 没有完整高分读数覆盖的低分表题仍然拉低分数。
    artifact,candidate=fixture(extra=[(title[:12],[30,220,330,240],.92)])
    result=qualification(candidate,artifact,organization='测试局',year=2025,report_kind='final')
    assert 'critical_recognition_score_below_threshold'in result['reasons']


@pytest.mark.parametrize('fault',['cell','rows','image','matrix','raw','score','tensor','context','model','neighbor'])
def test_grid_mutation_cannot_reuse_previous_qualification(fault):
    artifact,candidate=fixture()
    assert qualification(candidate,artifact,organization='测试局',year=2025,report_kind='final')['status']=='qualified'
    grid=artifact.coordinate_transforms[candidate.coordinate_transform_id]
    source=next(t for t in artifact.coordinate_transforms.values()if t.get('transform_version')=='pdf-tile-v3')
    if fault=='cell':candidate.cells[-1]['raw_text']='999.00'
    elif fault=='rows':candidate.rows[-1][-1]='999.00'
    elif fault=='image':grid['render_png_base64']=grid['render_png_base64'][:-4]+'AAAA'
    elif fault=='matrix':grid['pixel_to_pdf_matrix'][-1]+=1
    elif fault=='raw':source['ocr_response']['raw_prediction']['overall_ocr_res']['rec_texts'][-1]='999.00'
    elif fault=='score':source['ocr_response']['raw_prediction']['overall_ocr_res']['rec_scores'][-1]=.4
    elif fault=='tensor':source['ocr_response']['detector_input_verified']=False
    elif fault=='context':source['tile_context']['physical_page']=2
    elif fault=='model':candidate.provenance['model_fingerprint']='d'*64
    elif fault=='neighbor':candidate.cells[-1]['page_bbox']=candidate.cells[-2]['page_bbox']
    result=qualification(candidate,artifact,organization='测试局',year=2025,report_kind='final')
    assert result['status']=='rejected',result
