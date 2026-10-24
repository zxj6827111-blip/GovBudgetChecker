"""合成 v3 协议材料，只验证契约，不作为真实 OCR 准确性证据。"""

import copy
from src.services.input_geometry import Affine
from src.services.ocr_tiles import digest
from ocr_service.protocol import PROTOCOL_VERSION


def bind_fixture_tile(candidate, transform):
    source = candidate.provenance
    size = transform["render_size_px"]
    matrix = transform.get("pixel_to_pdf_matrix")
    if not matrix:
        scale = transform["scale"]
        origin = transform["render_origin_px"]
        crop = transform["cropbox"]
        matrix = Affine(
            a=1 / scale, d=1 / scale, e=crop[0] - origin[0] / scale, f=crop[1] - origin[1] / scale
        ).values()
    affine = Affine(*matrix)
    padding = [0,0,(-size[0])%32,(-size[1])%32]
    model_size = [size[0]+padding[2],size[1]+padding[3]]
    context = dict(
        inference_mode='structure',
        source_sha256=source["source_sha256"],
        physical_page=candidate.page_number,
        parent_image_sha256=source["input_sha256"],
        input_sha256=source["input_sha256"],
        size_px=size,
        parent_size_px=size,
        crop_box_px=[0, 0, *size],
        source_region=affine.box([0, 0, *size]),
        pixel_to_pdf=matrix,
        pdf_to_pixel=affine.inverse().values(),
        preprocessing=[],
        attempt=0,
    )
    context["tile_id"] = digest(context)
    source.update(
        schema_version=PROTOCOL_VERSION,
        tile_context=context,
        prediction_coordinate_scale=[1.0, 1.0],
        completeness={"scope": "tile"},
        model_input_size_px=model_size,
        detector_input_verified=True,
        detector_tensor_sizes_px=[model_size],
        inference_padding_px=padding,
        input_resize_applied=False,
    )
    transform["tile_context"] = copy.deepcopy(context)
    transform["ocr_response"] = copy.deepcopy(source)
    transform["page_request"]["schema_version"] = PROTOCOL_VERSION
