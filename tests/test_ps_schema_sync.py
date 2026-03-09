from src.services.ps_schema_sync import PSSharedSchemaSync


def test_normalize_report_type():
    sync = PSSharedSchemaSync(None)  # type: ignore[arg-type]

    assert sync._normalize_report_type("budget") == "BUDGET"
    assert sync._normalize_report_type("预算") == "BUDGET"
    assert sync._normalize_report_type("final") == "FINAL"
    assert sync._normalize_report_type("决算") == "FINAL"


def test_derive_scope_names_for_unit_report():
    sync = PSSharedSchemaSync(None)  # type: ignore[arg-type]

    department_name, unit_name = sync._derive_scope_names("上海市普陀区商务委员会单位")

    assert department_name == "上海市普陀区商务委员会"
    assert unit_name == "上海市普陀区商务委员会单位"


def test_build_table_payload_tracks_pages_and_shape():
    sync = PSSharedSchemaSync(None)  # type: ignore[arg-type]

    payload = sync.build_table_payload(
        table_code="FIN_06_basic_expenditure",
        source_title="2025年部门一般公共预算基本支出部门预算经济分类预算表",
        cells=[
            {"row_idx": 0, "col_idx": 0, "raw_text": "项目", "normalized_text": "项目", "numeric_value": None, "page_number": 27, "bbox": None, "is_header": True, "confidence": 0.9},
            {"row_idx": 0, "col_idx": 1, "raw_text": "合计", "normalized_text": "合计", "numeric_value": None, "page_number": 27, "bbox": None, "is_header": True, "confidence": 0.9},
            {"row_idx": 1, "col_idx": 0, "raw_text": "工资福利支出", "normalized_text": "工资福利支出", "numeric_value": None, "page_number": 27, "bbox": None, "is_header": False, "confidence": 0.9},
            {"row_idx": 1, "col_idx": 1, "raw_text": "100", "normalized_text": "100", "numeric_value": 100.0, "page_number": 28, "bbox": None, "is_header": False, "confidence": 0.9},
        ],
    )

    assert payload["page_numbers"] == [27, 28]
    assert payload["row_count"] == 2
    assert payload["col_count"] == 2
    assert payload["data_json"]["table_key"] == "FIN_06_basic_expenditure"
    assert len(payload["data_json"]["rows"]) == 2


def test_build_line_item_rows_groups_measures_per_row():
    sync = PSSharedSchemaSync(None)  # type: ignore[arg-type]

    rows = sync.build_line_item_rows(
        [
            {
                "table_code": "FIN_02_income",
                "row_order": 8,
                "classification_type": "function",
                "classification_code": "2010507",
                "classification_name": "专项普查活动",
                "measure": "fiscal_allocation",
                "amount": 257700,
                "source_page_number": 10,
                "parse_confidence": 0.92,
            },
            {
                "table_code": "FIN_02_income",
                "row_order": 8,
                "classification_type": "function",
                "classification_code": "2010507",
                "classification_name": "专项普查活动",
                "measure": "total_actual",
                "amount": 257700,
                "source_page_number": 10,
                "parse_confidence": 0.91,
            },
        ]
    )

    assert len(rows) == 1
    assert rows[0]["class_code"] == "201"
    assert rows[0]["type_code"] == "20105"
    assert rows[0]["item_code"] == "2010507"
    assert rows[0]["values_json"]["fiscal_allocation"] == 257700.0
    assert rows[0]["values_json"]["total_actual"] == 257700.0
    assert rows[0]["values_json"]["_meta"]["source_page_numbers"] == [10]


def test_resolve_scope_uses_selected_unit_organization():
    sync = PSSharedSchemaSync(None)  # type: ignore[arg-type]

    resolved = sync.resolve_scope(
        org_name="上海市普陀区商务委员会单位",
        organization_id="unit-1",
        org_records=[
            {
                "id": "dept-1",
                "name": "上海市普陀区商务委员会",
                "level": "department",
                "code": "D001",
                "parent_id": None,
                "keywords": ["商务委员会"],
            },
            {
                "id": "unit-1",
                "name": "上海市普陀区商务委员会单位",
                "level": "unit",
                "code": "U001",
                "parent_id": "dept-1",
                "keywords": ["商务委员会单位"],
            },
        ],
    )

    assert resolved["department_name"] == "上海市普陀区商务委员会"
    assert resolved["unit_name"] == "上海市普陀区商务委员会单位"
    assert resolved["department_code"] == "D001"
    assert resolved["unit_code"] == "U001"
    assert resolved["match_mode"] == "organization_id"


def test_resolve_scope_falls_back_to_name_matching():
    sync = PSSharedSchemaSync(None)  # type: ignore[arg-type]

    resolved = sync.resolve_scope(
        org_name="上海市普陀区人民政府办公室",
        org_records=[
            {
                "id": "dept-office",
                "name": "上海市普陀区人民政府办公室",
                "level": "department",
                "code": "D002",
                "parent_id": None,
                "keywords": ["办公室"],
            }
        ],
    )

    assert resolved["department_name"] == "上海市普陀区人民政府办公室"
    assert resolved["unit_name"] == "上海市普陀区人民政府办公室"
    assert resolved["department_code"] == "D002"
    assert resolved["unit_code"] == "D002__SELF"
    assert resolved["match_mode"] == "name_department"


def test_resolve_scope_promotes_department_like_unit_when_parent_mismatched():
    sync = PSSharedSchemaSync(None)  # type: ignore[arg-type]

    resolved = sync.resolve_scope(
        org_name="上海市普陀区人民政府办公室",
        organization_id="unit-office",
        org_records=[
            {
                "id": "dept-env",
                "name": "上海市普陀区生态环境局",
                "level": "department",
                "code": "D-ENV",
                "parent_id": None,
                "keywords": ["生态环境局"],
            },
            {
                "id": "unit-office",
                "name": "上海市普陀区人民政府办公室",
                "level": "unit",
                "code": "U-OFFICE",
                "parent_id": "dept-env",
                "keywords": ["人民政府办公室"],
            },
        ],
    )

    assert resolved["department_name"] == "上海市普陀区人民政府办公室"
    assert resolved["unit_name"] == "上海市普陀区人民政府办公室"
    assert resolved["department_code"] is None
    assert resolved["unit_code"] == "U-OFFICE"
    assert resolved["match_mode"] == "organization_id"


def test_resolve_scope_promotes_name_matched_department_like_unit_when_parent_mismatched():
    sync = PSSharedSchemaSync(None)  # type: ignore[arg-type]

    resolved = sync.resolve_scope(
        org_name="上海市普陀区人民政府办公室",
        org_records=[
            {
                "id": "dept-env",
                "name": "上海市普陀区生态环境局",
                "level": "department",
                "code": "D-ENV",
                "parent_id": None,
                "keywords": ["生态环境局"],
            },
            {
                "id": "unit-office",
                "name": "上海市普陀区人民政府办公室",
                "level": "unit",
                "code": "U-OFFICE",
                "parent_id": "dept-env",
                "keywords": ["人民政府办公室"],
            },
        ],
    )

    assert resolved["department_name"] == "上海市普陀区人民政府办公室"
    assert resolved["unit_name"] == "上海市普陀区人民政府办公室"
    assert resolved["department_code"] is None
    assert resolved["unit_code"] == "U-OFFICE"
    assert resolved["match_mode"] == "name_unit_promoted"


def test_should_not_reuse_self_unit_when_real_unit_code_arrives():
    sync = PSSharedSchemaSync(None)  # type: ignore[arg-type]

    assert sync._should_reuse_existing_named_unit("DEPT_X001__SELF", "UNIT_X001") is False
    assert sync._should_reuse_existing_named_unit("AUTO_UNIT_ABCDEF", "UNIT_X001") is False
    assert sync._should_reuse_existing_named_unit("UNIT_X001", "UNIT_X001") is True
    assert sync._should_reuse_existing_named_unit("UNIT_X001", "UNIT_X002") is True
