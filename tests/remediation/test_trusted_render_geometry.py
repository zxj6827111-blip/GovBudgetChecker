"""对实际渲染像素与原生对象定位；正反矩阵自洽不能证明坐标基准正确。"""
import numpy as np
import pdfplumber
import pymupdf as fitz
import pytest

from src.services.input_geometry import page_viewport_to_native, rendered_pixels_to_native


def source_page(tmp_path, rotation, nonzero_media, user_unit):
    path=tmp_path/'geometry.pdf'
    doc=fitz.open();page=doc.new_page(width=400,height=600)
    if nonzero_media:
        page.set_mediabox(fitz.Rect(-50,-30,350,570))
    page.draw_rect(fitz.Rect(50,100,80,120),color=(0,0,0),fill=(0,0,0),width=0)
    page.set_cropbox(fitz.Rect(-30,30,330,560) if nonzero_media else fitz.Rect(20,30,380,560))
    page.set_rotation(rotation)
    doc.xref_set_key(page.xref,'UserUnit',str(user_unit))
    doc.save(path);doc.close()
    return path


def black_box(pixmap):
    pixels=np.frombuffer(pixmap.samples,dtype=np.uint8).reshape(pixmap.height,pixmap.width,pixmap.n)
    ys,xs=np.where(pixels[:,:,:3].max(axis=2)<100)
    assert len(xs)>0
    return [int(xs.min()),int(ys.min()),int(xs.max())+1,int(ys.max())+1]


@pytest.mark.parametrize('rotation',[0,90,180,270])
@pytest.mark.parametrize('nonzero_media',[False,True])
@pytest.mark.parametrize('user_unit',[1,2])
def test_visible_crop_pixels_match_native_object_and_high_resolution_clip(tmp_path,rotation,nonzero_media,user_unit):
    path=source_page(tmp_path,rotation,nonzero_media,user_unit)
    with pdfplumber.open(path) as pdf:
        obj=(pdf.pages[0].rects+pdf.pages[0].curves)[0]
        expected=[obj['x0'],obj['top'],obj['x1'],obj['bottom']]
    with fitz.open(path) as pdf:
        page=pdf[0];pix=page.get_pixmap(dpi=300)
        transform=rendered_pixels_to_native(page,pix,300)
        pixels=black_box(pix)
        # 在真实像素中验证一像素上限，而不是只检查 inverse(forward(x))。
        assert transform.inverse().box(expected)==pytest.approx(pixels,abs=1)
        native=transform.box(pixels)
        viewport=page_viewport_to_native(page)
        clip=fitz.Rect(viewport.inverse().box(native))+(-2,-2,2,2)
        raster=page.get_pixmap(dpi=600,clip=clip)
        local=rendered_pixels_to_native(page,raster,600)
        actual=black_box(raster)
        assert local.inverse().box(expected)==pytest.approx(actual,abs=1)
        assert local.box(actual)==pytest.approx(expected,abs=72/600/user_unit)
        # 任何单边 DPI 换算都漏掉裁剪原点；此反例必须真的包含平移。
        assert abs(transform.e)>0 or abs(transform.f)>0
    from src.services.ocr_pipeline import _render_pages_png,_pdf_bbox_from_render_bbox
    from PIL import Image
    import io
    images,transforms=_render_pages_png(str(path),[1],dpi=300)
    pixels=np.asarray(Image.open(io.BytesIO(images[0])).convert('RGB'))
    ys,xs=np.where(pixels.max(axis=2)<100)
    box=[int(xs.min()),int(ys.min()),int(xs.max())+1,int(ys.max())+1]
    assert transforms[1]['transform_version']=='pdf-render-v4'
    assert _pdf_bbox_from_render_bbox(box,transforms[1])==pytest.approx(expected,abs=72/300/user_unit)
