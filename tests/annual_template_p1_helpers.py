"""合成原件绑定契约夹具，不是真实 OCR 或独立业务验收。"""
import copy
from types import SimpleNamespace
from src.schemas.parse_artifact import ParseArtifact,ParsedTextBlock
from src.services.ocr_tiles import digest
from tests.remediation.test_round11_ocr import fixture
from tests.ocr_v3_helpers import bind_fixture_tile


def bound_identity_artifact(pages):
    base,candidate=fixture()
    artifact=ParseArtifact(source_file_hash=base.source_file_hash,page_count=len(pages),ocr_model_fingerprint=base.ocr_model_fingerprint)
    template=next(iter(base.coordinate_transforms.values()))
    for page,texts in enumerate(pages,1):
        source=copy.deepcopy(candidate.provenance)
        source['raw_prediction']['overall_ocr_res']={'rec_texts':texts,'rec_scores':[.995]*len(texts),
            'rec_polys':[[[0,i*25],[400,i*25],[400,i*25+15],[0,i*25+15]] for i in range(len(texts))]}
        source['raw_prediction_sha256']=digest(source['raw_prediction'])
        transform=copy.deepcopy(template)
        transform['physical_page']=page
        transform['pixel_to_pdf_matrix']=[1,0,0,1,0,0]
        transform['page_request']={'physical_page':page,**{key:source[key] for key in
            ('source_sha256','render_sha256','input_sha256','schema_version','raw_prediction_sha256','model_fingerprint','pipeline_fingerprint')}}
        bind_fixture_tile(SimpleNamespace(page_number=page,provenance=source),transform)
        tid=f'ocr_render_page_{page}'
        artifact.coordinate_transforms[tid]=transform
        artifact.text_blocks.extend(ParsedTextBlock(f'{tid}:block-{i}',page,text,(0,i*25,400,i*25+15),.995,'ocr') for i,text in enumerate(texts))
    return artifact
