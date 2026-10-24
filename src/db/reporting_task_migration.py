"""年度任务迁移只新增结构，不生成或确认任何业务名册。"""

MIGRATION = {
    "id": "2026-10-23_0040_reporting_tasks",
    "description": "区域年度任务、名册草稿与不可变发布版本",
    "sql": [
        """CREATE TABLE IF NOT EXISTS reporting_tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            reporting_year INTEGER NOT NULL CHECK(reporting_year BETWEEN 2001 AND 2099),
            jurisdiction_org_id TEXT NOT NULL,
            components JSONB NOT NULL CHECK(jsonb_typeof(components)='array'),
            state TEXT NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','active','archived')),
            draft_roster JSONB NOT NULL DEFAULT '[]'::jsonb,
            draft_revision INTEGER NOT NULL DEFAULT 0 CHECK(draft_revision>=0),
            draft_catalog_token TEXT NOT NULL DEFAULT '',
            active_revision INTEGER,
            active_catalog_token TEXT NOT NULL DEFAULT '',
            active_components JSONB,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(reporting_year,jurisdiction_org_id)
        )""",
        "ALTER TABLE supervision_baselines ADD COLUMN IF NOT EXISTS task_id UUID REFERENCES reporting_tasks(id) ON DELETE RESTRICT",
        "ALTER TABLE supervision_baselines ADD COLUMN IF NOT EXISTS roster_revision INTEGER",
        "ALTER TABLE supervision_baselines ADD COLUMN IF NOT EXISTS roster_snapshot JSONB",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_roster_component ON supervision_baselines(task_id,roster_revision,report_kind,fiscal_year) WHERE task_id IS NOT NULL",
    ],
}
