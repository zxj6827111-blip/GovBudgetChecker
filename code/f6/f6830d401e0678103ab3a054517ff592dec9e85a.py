# Copyright (c) 2025 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import warnings
from .._utils.cli import (
    add_simple_inference_args,
    get_subcommand_args,
    perform_simple_inference,
    str2bool,
)
from .base import PaddleXPipelineWrapper, PipelineCLISubcommandExecutor
from .utils import create_config_from_structure
from ._patch_layout_parsing import apply_patches as _apply_layout_parsing_patches

_apply_layout_parsing_patches()

_SUPPORTED_OCR_VERSIONS = ["PP-OCRv3", "PP-OCRv4", "PP-OCRv5"]


class PPStructureV3(PaddleXPipelineWrapper):
    def __init__(
        self,
        layout_detection_model_name=None,
        layout_detection_model_dir=None,
        layout_threshold=None,
        layout_nms=None,
        layout_unclip_ratio=None,
        layout_merge_bboxes_mode=None,
        chart_recognition_model_name=None,
        chart_recognition_model_dir=None,
        chart_recognition_batch_size=None,
        region_detection_model_name=None,
        region_detection_model_dir=None,
        doc_orientation_classify_model_name=None,
        doc_orientation_classify_model_dir=None,
        doc_unwarping_model_name=None,
        doc_unwarping_model_dir=None,
        text_detection_model_name=None,
        text_detection_model_dir=None,
        text_det_limit_side_len=None,
        text_det_limit_type=None,
        text_det_thresh=None,
        text_det_box_thresh=None,
        text_det_unclip_ratio=None,
        textline_orientation_model_name=None,
        textline_orientation_model_dir=None,
        textline_orientation_batch_size=None,
        text_recognition_model_name=None,
        text_recognition_model_dir=None,
        text_recognition_batch_size=None,
        text_rec_score_thresh=None,
        table_classification_model_name=None,
        table_classification_model_dir=None,
        wired_table_structure_recognition_model_name=None,
        wired_table_structure_recognition_model_dir=None,
        wireless_table_structure_recognition_model_name=None,
        wireless_table_structure_recognition_model_dir=None,
        wired_table_cells_detection_model_name=None,
        wired_table_cells_detection_model_dir=None,
        wireless_table_cells_detection_model_name=None,
        wireless_table_cells_detection_model_dir=None,
        table_orientation_classify_model_name=None,
        table_orientation_classify_model_dir=None,
        seal_text_detection_model_name=None,
        seal_text_detection_model_dir=None,
        seal_det_limit_side_len=None,
        seal_det_limit_type=None,
        seal_det_thresh=None,
        seal_det_box_thresh=None,
        seal_det_unclip_ratio=None,
        seal_text_recognition_model_name=None,
        seal_text_recognition_model_dir=None,
        seal_text_recognition_batch_size=None,
        seal_rec_score_thresh=None,
        formula_recognition_model_name=None,
        formula_recognition_model_dir=None,
        formula_recognition_batch_size=None,
        use_doc_orientation_classify=None,
        use_doc_unwarping=None,
        use_textline_orientation=None,
        use_seal_recognition=None,
        use_table_recognition=None,
        use_formula_recognition=None,
        use_chart_recognition=None,
        use_region_detection=None,
        format_block_content=None,
        markdown_ignore_labels=None,
        lang=None,
        ocr_version=None,
        **kwargs,
    ):
        if ocr_version is not None and ocr_version not in _SUPPORTED_OCR_VERSIONS:
            raise ValueError(
                f"Invalid OCR version: {ocr_version}. Supported values are {_SUPPORTED_OCR_VERSIONS}."
            )

        if all(
            map(
                lambda p: p is None,
                (
                    text_detection_model_name,
                    text_detection_model_dir,
                    text_recognition_model_name,
                    text_recognition_model_dir,
                ),
            )
        ):
            if lang is not None or ocr_version is not None:
                det_model_name, rec_model_name = self._get_ocr_model_names(
                    lang, ocr_version
                )
                if det_model_name is None or rec_model_name is None:
                    raise ValueError(
                        f"No models are available for the language {repr(lang)} and OCR version {repr(ocr_version)}."
                    )
                text_detection_model_name = det_model_name
                text_recognition_model_name = rec_model_name
        else:
            if lang is not None or ocr_version is not None:
                warnings.warn(
                    "`lang` and `ocr_version` will be ignored when model names or model directories are not `None`.",
                    stacklevel=2,
                )
        params = locals().copy()
        params["text_detection_model_name"] = text_detection_model_name
        params["text_recognition_model_name"] = text_recognition_model_name
        params.pop("self")
        params.pop("kwargs")
        self._params = params

        super().__init__(**kwargs)

    @property
    def _paddlex_pipeline_name(self):
        return "PP-StructureV3"

    def predict_iter(
        self,
        input,
        *,
        use_doc_orientation_classify=None,
        use_doc_unwarping=None,
        use_textline_orientation=None,
        use_seal_recognition=None,
        use_table_recognition=None,
        use_formula_recognition=None,
        use_chart_recognition=None,
        use_region_detection=None,
        format_block_content=None,
        layout_threshold=None,
        layout_nms=None,
        layout_unclip_ratio=None,
        layout_merge_bboxes_mode=None,
        text_det_limit_side_len=None,
        text_det_limit_type=None,
        text_det_thresh=None,
        text_det_box_thresh=None,
        text_det_unclip_ratio=None,
        text_rec_score_thresh=None,
        seal_det_limit_side_len=None,
        seal_det_limit_type=None,
        seal_det_thresh=None,
        seal_det_box_thresh=None,
        seal_det_unclip_ratio=None,
        seal_rec_score_thresh=None,
        use_wired_table_cells_trans_to_html=False,
        use_wireless_table_cells_trans_to_html=False,
        use_table_orientation_classify=True,
        use_ocr_results_with_table_cells=True,
        use_e2e_wired_table_rec_model=False,
        use_e2e_wireless_table_rec_model=True,
        markdown_ignore_labels=None,
        **kwargs,
    ):
        return self.paddlex_pipeline.predict(
            input,
            use_doc_orientation_classify=use_doc_orientation_classify,
            use_doc_unwarping=use_doc_unwarping,
            use_textline_orientation=use_textline_orientation,
            use_seal_recognition=use_seal_recognition,
            use_table_recognition=use_table_recognition,
            use_formula_recognition=use_formula_recognition,
            use_chart_recognition=use_chart_recognition,
            use_region_detection=use_region_detection,
            format_block_content=format_block_content,
            layout_threshold=layout_threshold,
            layout_nms=layout_nms,
            layout_unclip_ratio=layout_unclip_ratio,
            layout_merge_bboxes_mode=layout_merge_bboxes_mode,
            text_det_limit_side_len=text_det_limit_side_len,
            text_det_limit_type=text_det_limit_type,
            text_det_thresh=text_det_thresh,
            text_det_box_thresh=text_det_box_thresh,
            text_det_unclip_ratio=text_det_unclip_ratio,
            text_rec_score_thresh=text_rec_score_thresh,
            seal_det_limit_side_len=seal_det_limit_side_len,
            seal_det_limit_type=seal_det_limit_type,
            seal_det_thresh=seal_det_thresh,
            seal_det_box_thresh=seal_det_box_thresh,
            seal_det_unclip_ratio=seal_det_unclip_ratio,
            seal_rec_score_thresh=seal_rec_score_thresh,
            use_wired_table_cells_trans_to_html=use_wired_table_cells_trans_to_html,
            use_wireless_table_cells_trans_to_html=use_wireless_table_cells_trans_to_html,
            use_table_orientation_classify=use_table_orientation_classify,
            use_ocr_results_with_table_cells=use_ocr_results_with_table_cells,
            use_e2e_wired_table_rec_model=use_e2e_wired_table_rec_model,
            use_e2e_wireless_table_rec_model=use_e2e_wireless_table_rec_model,
            markdown_ignore_labels=markdown_ignore_labels,
            **kwargs,
        )

    def predict(
        self,
        input,
        *,
        use_doc_orientation_classify=None,
        use_doc_unwarping=None,
        use_textline_orientation=None,
        use_seal_recognition=None,
        use_table_recognition=None,
        use_formula_recognition=None,
        use_chart_recognition=None,
        use_region_detection=None,
        format_block_content=None,
        layout_threshold=None,
        layout_nms=None,
        layout_unclip_ratio=None,
        layout_merge_bboxes_mode=None,
        text_det_limit_side_len=None,
        text_det_limit_type=None,
        text_det_thresh=None,
        text_det_box_thresh=None,
        text_det_unclip_ratio=None,
        text_rec_score_thresh=None,
        seal_det_limit_side_len=None,
        seal_det_limit_type=None,
        seal_det_thresh=None,
        seal_det_box_thresh=None,
        seal_det_unclip_ratio=None,
        seal_rec_score_thresh=None,
        use_wired_table_cells_trans_to_html=False,
        use_wireless_table_cells_trans_to_html=False,
        use_table_orientation_classify=True,
        use_ocr_results_with_table_cells=True,
        use_e2e_wired_table_rec_model=False,
        use_e2e_wireless_table_rec_model=True,
        markdown_ignore_labels=None,
        **kwargs,
    ):
        return list(
            self.predict_iter(
                input,
                use_doc_orientation_classify=use_doc_orientation_classify,
                use_doc_unwarping=use_doc_unwarping,
                use_textline_orientation=use_textline_orientation,
                use_seal_recognition=use_seal_recognition,
                use_table_recognition=use_table_recognition,
                use_formula_recognition=use_formula_recognition,
                use_chart_recognition=use_chart_recognition,
                use_region_detection=use_region_detection,
                format_block_content=format_block_content,
                layout_threshold=layout_threshold,
                layout_nms=layout_nms,
                layout_unclip_ratio=layout_unclip_ratio,
                layout_merge_bboxes_mode=layout_merge_bboxes_mode,
                text_det_limit_side_len=text_det_limit_side_len,
                text_det_limit_type=text_det_limit_type,
                text_det_thresh=text_det_thresh,
                text_det_box_thresh=text_det_box_thresh,
                text_det_unclip_ratio=text_det_unclip_ratio,
                text_rec_score_thresh=text_rec_score_thresh,
                seal_det_limit_side_len=seal_det_limit_side_len,
                seal_det_limit_type=seal_det_limit_type,
                seal_det_thresh=seal_det_thresh,
                seal_det_box_thresh=seal_det_box_thresh,
                seal_det_unclip_ratio=seal_det_unclip_ratio,
                seal_rec_score_thresh=seal_rec_score_thresh,
                use_wired_table_cells_trans_to_html=use_wired_table_cells_trans_to_html,
                use_wireless_table_cells_trans_to_html=use_wireless_table_cells_trans_to_html,
                use_table_orientation_classify=use_table_orientation_classify,
                use_ocr_results_with_table_cells=use_ocr_results_with_table_cells,
                use_e2e_wired_table_rec_model=use_e2e_wired_table_rec_model,
                use_e2e_wireless_table_rec_model=use_e2e_wireless_table_rec_model,
                markdown_ignore_labels=markdown_ignore_labels,
                **kwargs,
            )
        )

    def concatenate_markdown_pages(self, markdown_list):
        return self.paddlex_pipeline.concatenate_markdown_pages(markdown_list)

    @classmethod
    def get_cli_subcommand_executor(cls):
        return PPStructureV3CLISubcommandExecutor()

    def _get_paddlex_config_overrides(self):
        STRUCTURE = {
            "SubPipelines.DocPreprocessor.use_doc_orientation_classify": self._params[
                "use_doc_orientation_classify"
            ],
            "SubPipelines.DocPreprocessor.use_doc_unwarping": self._params[
                "use_doc_unwarping"
            ],
            "use_doc_preprocessor": self._params["use_doc_orientation_classify"]
            or self._params["use_doc_unwarping"],
            "SubPipelines.GeneralOCR.use_textline_orientation": self._params[
                "use_textline_orientation"
            ],
            "use_seal_recognition": self._params["use_seal_recognition"],
            "use_table_recognition": self._params["use_table_recognition"],
            "use_formula_recognition": self._params["use_formula_recognition"],
            "use_chart_recognition": self._params["use_chart_recognition"],
            "use_region_detection": self._params["use_region_detection"],
            "format_block_content": self._params["format_block_content"],
            "markdown_ignore_labels": self._params["markdown_ignore_labels"],
            "SubModules.LayoutDetection.model_name": self._params[
                "layout_detection_model_name"
            ],
            "SubModules.LayoutDetection.model_dir": self._params[
                "layout_detection_model_dir"
            ],
            "SubModules.LayoutDetection.threshold": self._params["layout_threshold"],
            "SubModules.LayoutDetection.layout_nms": self._params["layout_nms"],
            "SubModules.LayoutDetection.layout_unclip_ratio": self._params[
                "layout_unclip_ratio"
            ],
            "SubModules.LayoutDetection.layout_merge_bboxes_mode": self._params[
                "layout_merge_bboxes_mode"
            ],
            "SubModules.ChartRecognition.model_name": self._params[
                "chart_recognition_model_name"
            ],
            "SubModules.ChartRecognition.model_dir": self._params[
                "chart_recognition_model_dir"
            ],
            "SubModules.ChartRecognition.batch_size": self._params[
                "chart_recognition_batch_size"
            ],
            "SubModules.RegionDetection.model_name": self._params[
 
