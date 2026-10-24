"""独立图像候选、断点换代与来源身份的控制反例，不计入真实准确率。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from src.services.raster_grid_candidates import discover_grid_cells
from src.services.ocr_tiled_pipeline import checkpoint_epoch_path
from src.services.ocr_tiles import recognize_tiles, digest, png_bytes
from src.services.ocr_client import OcrPageResult
from src.services.input_geometry import Affine


def grid_image():
    image=Image.new('RGB',(600,300),'white');draw=ImageDraw.Draw(image)
    for y in (20,180,260):draw.line((20,y,560,y),fill='black',width=2)
    draw.line((180,100,560,100),fill='black',width=2)
    for x in (20,180,560):draw.line((x,20,x,260),fill='black',width=2)
    draw.line((360,100,360,260),fill='black',width=2)
    return image


def test_merged_headers_rowspans_and_blank_cells_remain_candidates():
    result=discover_grid_cells(grid_image())
    positions={(c['row_idx'],c['col_idx'],c['row_span'],c['col_span']) for c in result['cells']}
    assert positions=={(0,0,2,1),(0,1,1,2),(1,1,1,1),(1,2,1,1),
                       (2,0,1,1),(2,1,1,1),(2,2,1,1)}
    assert not result['discovery_regions']
    assert result['completeness'] is None and result['business_identity_confirmed'] is False
    assert all(c['literal_status']=='unrecognized' and 'amount'not in c for c in result['cells'])
    assert all(0<=c['bbox'][0]<c['bbox'][2]<=600 and 0<=c['bbox'][1]<c['bbox'][3]<=300
               for c in result['cells'])


def test_open_border_is_not_fabricated_and_ink_proposes_independent_read():
    image=grid_image();draw=ImageDraw.Draw(image)
    draw.rectangle((22,259,558,263),fill='white')
    draw.text((400,140),'1250.64',fill='black')
    result=discover_grid_cells(image)
    assert not any(c['row_idx']==2 for c in result['cells'])
    assert result['discovery_regions'] and not any('raw_text'in c for c in result['cells'])


def test_floating_glyph_strokes_do_not_split_a_cell_but_attached_dividers_do():
    # 扫描件上汉字竖笔够长时会被测成短竖线；悬空在格内的笔画不能把「合计」「科目名称」格拆掉。
    image=grid_image();draw=ImageDraw.Draw(image)
    for x in (230,250,270):draw.line((x,200,x,240),fill='black',width=2)

    def spans_name_cell(result):
        return any(abs(c['bbox'][0]-180)<3 and abs(c['bbox'][2]-360)<3 and abs(c['bbox'][1]-180)<3
                   and abs(c['bbox'][3]-260)<3 for c in result['cells'])

    assert spans_name_cell(discover_grid_cells(image))
    # 接到格上边框的半截隔线是真实结构：不能跨过它拼成一个格。
    draw.line((270,180,270,225),fill='black',width=2)
    assert not spans_name_cell(discover_grid_cells(image))


def test_checkpoint_epoch_does_not_depend_on_mutable_run_id_and_preserves_history(tmp_path):
    artifact=SimpleNamespace(source_file_hash='a'*64,parser_fingerprint='p1',
                             config_fingerprint='c1',parse_run_uid='run1')
    health={'model_fingerprint':'b'*64,'pipeline_fingerprint':'c'*64}
    root=tmp_path/'checkpoint.json';first=checkpoint_epoch_path(root,artifact,health)
    first.mkdir(parents=True);(first/'page-1.v3.json').write_text('old evidence')
    artifact.parse_run_uid='run2'
    assert checkpoint_epoch_path(root,artifact,health)==first
    for field in ('source_file_hash','parser_fingerprint','config_fingerprint'):
        changed=deepcopy(artifact);setattr(changed,field,'new')
        assert checkpoint_epoch_path(root,changed,health)!=first
    assert checkpoint_epoch_path(root,artifact,{**health,'model_fingerprint':'d'*64})!=first
    assert (first/'page-1.v3.json').read_text()=='old evidence'


@pytest.mark.asyncio
async def test_first_pass_completely_misses_grid_text_and_two_rereads_still_run(monkeypatch,tmp_path):
    calls=[];image=grid_image();ImageDraw.Draw(image).text((400,140),'0.00',fill='black')
    async def infer(client,**kwargs):
        calls.append(kwargs['tile_context']);return OcrPageResult(1)
    monkeypatch.setattr('src.services.ocr_client.ocr_page',infer)
    grid=discover_grid_cells(image)
    _,_,state=await recognize_tiles(image,file_hash='a'*64,page=1,pixel_to_pdf=Affine(),client=None,
        model='b'*64,pipeline='c'*64,checkpoint_path=tmp_path/'state.json',
        discovery_regions=grid['discovery_regions'],original_parent_sha='d'*64)
    assert {c['attempt'] for c in calls}=={0,1,2}
    assert state['status']=='review_required' and state['unresolved']
    import hashlib
    assert state['identity']['parent']==hashlib.sha256(png_bytes(image)).hexdigest()
    assert state['identity']['original_page_render']=='d'*64
    assert all(c['original_page_render_sha256']=='d'*64 for c in calls)
    assert calls[0]['parent_image_sha256']==state['identity']['parent']
    for context in calls:
        assert context['tile_id']==digest({k:v for k,v in context.items()if k!='tile_id'})


@pytest.mark.asyncio
async def test_two_jobs_cannot_modify_the_same_page_checkpoint_concurrently(monkeypatch,tmp_path):
    import asyncio
    started=asyncio.Event();release=asyncio.Event();calls=[]
    async def infer(client,**kwargs):
        calls.append(kwargs);started.set();await release.wait();return OcrPageResult(1)
    monkeypatch.setattr('src.services.ocr_client.ocr_page',infer)
    options=dict(file_hash='a'*64,page=1,pixel_to_pdf=Affine(),client=None,model='b'*64,pipeline='c'*64,
                 checkpoint_path=tmp_path/'page.json')
    image=Image.new('RGB',(120,100),'white')
    task=asyncio.create_task(recognize_tiles(image,**options));await started.wait()
    _,_,second=await recognize_tiles(image,**options)
    assert second['status']=='pending'and second['pending']==['checkpoint_in_use']
    release.set();await task
    assert len(calls)==1


@pytest.mark.asyncio
async def test_corrupt_checkpoint_is_preserved_before_recomputation(monkeypatch,tmp_path):
    async def infer(client,**kwargs):return OcrPageResult(1)
    monkeypatch.setattr('src.services.ocr_client.ocr_page',infer)
    path=tmp_path/'page.json';path.write_bytes(b'broken raw evidence')
    await recognize_tiles(Image.new('RGB',(120,100),'white'),file_hash='a'*64,page=1,
        pixel_to_pdf=Affine(),client=None,model='b'*64,pipeline='c'*64,checkpoint_path=path)
    assert next((tmp_path/'checkpoint_conflicts').glob('*.json')).read_bytes()==b'broken raw evidence'
