from pathlib import Path

from scripts.sync_ps_organizations import (
    PSDepartment,
    PSUnit,
    _parse_copy_table,
    build_store,
    load_from_sql_dump,
)


def test_parse_copy_table_from_pg_dump_text():
    sql = """COPY public.org_department (id, code, name, parent_id, created_at, updated_at, sort_order) FROM stdin;
dept-1\tD001\t上海市普陀区人民政府办公室\t\\N\t2026-01-01\t2026-01-01\t2
\\.
COPY public.org_unit (id, department_id, code, name, created_at, updated_at, sort_order) FROM stdin;
unit-1\tdept-1\tU001\t上海市普陀区人民政府办公室单位\t2026-01-01\t2026-01-01\t1
\\.
"""
    rows = _parse_copy_table(sql, "org_department")

    assert len(rows) == 1
    assert rows[0]["id"] == "dept-1"
    assert rows[0]["code"] == "D001"
    assert rows[0]["parent_id"] is None


def test_load_from_sql_dump_reads_departments_and_units(tmp_path: Path):
    dump_path = tmp_path / "govbudget.sql"
    dump_path.write_text(
        """COPY public.org_department (id, code, name, parent_id, created_at, updated_at, sort_order) FROM stdin;
dept-1\tD001\t上海市普陀区人民政府办公室\t\\N\t2026-01-01\t2026-01-01\t2
\\.
COPY public.org_unit (id, department_id, code, name, created_at, updated_at, sort_order) FROM stdin;
unit-1\tdept-1\tU001\t上海市普陀区人民政府办公室单位\t2026-01-01\t2026-01-01\t1
\\.
""",
        encoding="utf-8",
    )

    departments, units = load_from_sql_dump(dump_path)

    assert len(departments) == 1
    assert departments[0].name == "上海市普陀区人民政府办公室"
    assert len(units) == 1
    assert units[0].name == "上海市普陀区人民政府办公室单位"


def test_build_store_generates_city_district_department_unit_tree():
    store = build_store(
        departments=[
            PSDepartment(
                id="dept-1",
                code="D001",
                name="上海市普陀区商务委员会",
                parent_id=None,
                sort_order=1,
            )
        ],
        units=[
            PSUnit(
                id="unit-1",
                department_id="dept-1",
                code="U001",
                name="上海市普陀区商务委员会单位",
                sort_order=1,
            )
        ],
        city_name="上海市",
        district_name="普陀区",
    )

    assert len(store.organizations) == 4
    levels = [org.level for org in store.organizations]
    assert levels == ["city", "district", "department", "unit"]
    assert store.organizations[2].code == "D001"
    assert store.organizations[3].code == "U001"
    assert store.organizations[3].parent_id == store.organizations[2].id


def test_build_store_promotes_department_like_unit_with_unrelated_parent():
    store = build_store(
        departments=[
            PSDepartment(
                id="dept-env",
                code="D-ENV",
                name="上海市普陀区生态环境局",
                parent_id=None,
                sort_order=1,
            )
        ],
        units=[
            PSUnit(
                id="unit-office",
                department_id="dept-env",
                code="U-OFFICE",
                name="上海市普陀区人民政府办公室",
                sort_order=1,
            )
        ],
        city_name="上海市",
        district_name="普陀区",
    )

    promoted_department = next(
        org for org in store.organizations if org.level == "department" and org.name == "上海市普陀区人民政府办公室"
    )
    office_unit = next(
        org for org in store.organizations if org.level == "unit" and org.name == "上海市普陀区人民政府办公室"
    )

    assert promoted_department.code is None
    assert office_unit.code == "U-OFFICE"
    assert office_unit.parent_id == promoted_department.id
