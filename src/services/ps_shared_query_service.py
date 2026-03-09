"""
Read-only query service for the PS shared schema.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import asyncpg


class PSSharedQueryService:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def list_reports(
        self,
        *,
        department_id: Optional[str] = None,
        unit_id: Optional[str] = None,
        year: Optional[int] = None,
        report_type: Optional[str] = None,
        keyword: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        where_sql, params = self._build_report_filters(
            department_id=department_id,
            unit_id=unit_id,
            year=year,
            report_type=report_type,
            keyword=keyword,
        )

        total = await self.conn.fetchval(
            f"""
            SELECT COUNT(*)
            FROM org_dept_annual_report report
            JOIN org_department department ON department.id = report.department_id
            JOIN org_unit unit ON unit.id = report.unit_id
            {where_sql}
            """,
            *params,
        )

        rows = await self.conn.fetch(
            f"""
            SELECT
                report.id::text AS report_id,
                report.department_id::text AS department_id,
                report.unit_id::text AS unit_id,
                report.year,
                report.report_type,
                report.file_name,
                report.file_path,
                report.file_hash,
                report.file_size,
                report.created_at,
                report.updated_at,
                department.code AS department_code,
                department.name AS department_name,
                unit.code AS unit_code,
                unit.name AS unit_name,
                COALESCE(table_counts.table_count, 0) AS table_count,
                COALESCE(line_counts.line_item_count, 0) AS line_item_count
            FROM org_dept_annual_report report
            JOIN org_department department ON department.id = report.department_id
            JOIN org_unit unit ON unit.id = report.unit_id
            LEFT JOIN (
                SELECT report_id, COUNT(*) AS table_count
                FROM org_dept_table_data
                GROUP BY report_id
            ) table_counts ON table_counts.report_id = report.id
            LEFT JOIN (
                SELECT report_id, COUNT(*) AS line_item_count
                FROM org_dept_line_items
                GROUP BY report_id
            ) line_counts ON line_counts.report_id = report.id
            {where_sql}
            ORDER BY report.year DESC, report.updated_at DESC, report.file_name ASC
            LIMIT ${len(params) + 1}
            OFFSET ${len(params) + 2}
            """,
            *params,
            limit,
            offset,
        )

        return {
            "items": [dict(row) for row in rows],
            "total": int(total or 0),
            "limit": limit,
            "offset": offset,
        }

    async def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        row = await self.conn.fetchrow(
            """
            SELECT
                report.id::text AS report_id,
                report.department_id::text AS department_id,
                report.unit_id::text AS unit_id,
                report.year,
                report.report_type,
                report.file_name,
                report.file_path,
                report.file_hash,
                report.file_size,
                report.created_at,
                report.updated_at,
                department.code AS department_code,
                department.name AS department_name,
                unit.code AS unit_code,
                unit.name AS unit_name,
                COALESCE(table_counts.table_count, 0) AS table_count,
                COALESCE(line_counts.line_item_count, 0) AS line_item_count
            FROM org_dept_annual_report report
            JOIN org_department department ON department.id = report.department_id
            JOIN org_unit unit ON unit.id = report.unit_id
            LEFT JOIN (
                SELECT report_id, COUNT(*) AS table_count
                FROM org_dept_table_data
                GROUP BY report_id
            ) table_counts ON table_counts.report_id = report.id
            LEFT JOIN (
                SELECT report_id, COUNT(*) AS line_item_count
                FROM org_dept_line_items
                GROUP BY report_id
            ) line_counts ON line_counts.report_id = report.id
            WHERE report.id = $1::uuid
            """,
            report_id,
        )
        return dict(row) if row is not None else None

    async def list_report_tables(
        self,
        report_id: str,
        *,
        table_key: Optional[str] = None,
        include_data: bool = True,
    ) -> Dict[str, Any]:
        params: List[Any] = [report_id]
        filters = ["report_id = $1::uuid"]
        if table_key:
            params.append(table_key)
            filters.append(f"table_key = ${len(params)}")
        where_sql = "WHERE " + " AND ".join(filters)

        select_data = "data_json" if include_data else "NULL::jsonb AS data_json"
        rows = await self.conn.fetch(
            f"""
            SELECT
                id::text AS id,
                report_id::text AS report_id,
                department_id::text AS department_id,
                year,
                report_type,
                table_key,
                table_title,
                page_numbers,
                row_count,
                col_count,
                {select_data},
                created_at,
                updated_at
            FROM org_dept_table_data
            {where_sql}
            ORDER BY table_key ASC
            """,
            *params,
        )

        return {
            "report_id": report_id,
            "table_key": table_key,
            "include_data": include_data,
            "items": [dict(row) for row in rows],
            "total": len(rows),
        }

    async def list_report_line_items(
        self,
        report_id: str,
        *,
        table_key: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> Dict[str, Any]:
        params: List[Any] = [report_id]
        filters = ["report_id = $1::uuid"]
        if table_key:
            params.append(table_key)
            filters.append(f"table_key = ${len(params)}")
        where_sql = "WHERE " + " AND ".join(filters)

        total = await self.conn.fetchval(
            f"""
            SELECT COUNT(*)
            FROM org_dept_line_items
            {where_sql}
            """,
            *params,
        )

        rows = await self.conn.fetch(
            f"""
            SELECT
                id::text AS id,
                report_id::text AS report_id,
                department_id::text AS department_id,
                year,
                report_type,
                table_key,
                row_index,
                class_code,
                type_code,
                item_code,
                item_name,
                values_json,
                created_at,
                updated_at
            FROM org_dept_line_items
            {where_sql}
            ORDER BY table_key ASC, row_index ASC, item_name ASC
            LIMIT ${len(params) + 1}
            OFFSET ${len(params) + 2}
            """,
            *params,
            limit,
            offset,
        )

        return {
            "report_id": report_id,
            "table_key": table_key,
            "items": [dict(row) for row in rows],
            "total": int(total or 0),
            "limit": limit,
            "offset": offset,
        }

    def _build_report_filters(
        self,
        *,
        department_id: Optional[str],
        unit_id: Optional[str],
        year: Optional[int],
        report_type: Optional[str],
        keyword: Optional[str],
    ) -> Tuple[str, Sequence[Any]]:
        params: List[Any] = []
        filters: List[str] = []

        if department_id:
            params.append(department_id)
            filters.append(f"report.department_id = ${len(params)}::uuid")
        if unit_id:
            params.append(unit_id)
            filters.append(f"report.unit_id = ${len(params)}::uuid")
        if year is not None:
            params.append(int(year))
            filters.append(f"report.year = ${len(params)}")
        if report_type:
            params.append(str(report_type).strip().upper())
            filters.append(f"UPPER(report.report_type) = ${len(params)}")
        if keyword:
            params.append(f"%{str(keyword).strip()}%")
            keyword_param = f"${len(params)}"
            filters.append(
                "("
                f"report.file_name ILIKE {keyword_param} OR "
                f"department.name ILIKE {keyword_param} OR "
                f"unit.name ILIKE {keyword_param}"
                ")"
            )

        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        return where_sql, params
