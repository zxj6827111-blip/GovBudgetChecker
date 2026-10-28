"""年度模板的可追溯数据基础；生命周期操作必须在服务层鉴权并事务化。"""
MIGRATION = {
    'id': '2026-10-28_0042_annual_templates',
    'description': '模板来源、不可变发布包、年度绑定修订和独立写法决定',
    'sql': [
        """CREATE TABLE IF NOT EXISTS disclosure_template_sources (
            id UUID PRIMARY KEY, source_sha256 TEXT NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
            storage_key TEXT NOT NULL, report_kind TEXT NOT NULL CHECK (report_kind IN ('budget','final')),
            material_type TEXT NOT NULL CHECK (material_type IN ('unit','department')),
            source_report_year INTEGER NOT NULL CHECK (source_report_year BETWEEN 1900 AND 2100),
            issuer TEXT NOT NULL, issue_date DATE, source_description TEXT NOT NULL,
            extraction_status TEXT NOT NULL CHECK (extraction_status IN ('failed','needs_visual_verification','verified')),
            converter TEXT, extraction_error TEXT, actor TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS disclosure_template_packages (
            id UUID PRIMARY KEY, version TEXT NOT NULL, report_kind TEXT NOT NULL CHECK (report_kind IN ('budget','final')),
            source_report_year INTEGER NOT NULL CHECK (source_report_year BETWEEN 1900 AND 2100),
            content JSONB NOT NULL, content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
            unit_source_id UUID NOT NULL REFERENCES disclosure_template_sources(id),
            department_source_id UUID NOT NULL REFERENCES disclosure_template_sources(id),
            predecessor_id UUID REFERENCES disclosure_template_packages(id),
            capability_scope JSONB NOT NULL, review_record JSONB NOT NULL, validation_record JSONB NOT NULL,
            actor TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (unit_source_id <> department_source_id), UNIQUE (report_kind, version)
        )""",
        """CREATE TABLE IF NOT EXISTS disclosure_template_binding_revisions (
            region TEXT NOT NULL, overlay TEXT NOT NULL DEFAULT '',
            report_kind TEXT NOT NULL CHECK (report_kind IN ('budget','final')),
            target_year INTEGER NOT NULL CHECK (target_year BETWEEN 1900 AND 2100),
            revision INTEGER NOT NULL CHECK (revision > 0),
            package_id UUID NOT NULL REFERENCES disclosure_template_packages(id),
            adoption_kind TEXT NOT NULL CHECK (adoption_kind IN ('issued','verified_same','not_received','historical_reference','rollback')),
            capability_scope JSONB NOT NULL, basis TEXT NOT NULL, actor TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (region, overlay, report_kind, target_year, revision)
        )""",
        """CREATE TABLE IF NOT EXISTS disclosure_template_current_bindings (
            region TEXT NOT NULL, overlay TEXT NOT NULL DEFAULT '', report_kind TEXT NOT NULL,
            target_year INTEGER NOT NULL, revision INTEGER NOT NULL,
            PRIMARY KEY (region, overlay, report_kind, target_year),
            FOREIGN KEY (region, overlay, report_kind, target_year, revision)
                REFERENCES disclosure_template_binding_revisions(region, overlay, report_kind, target_year, revision)
        )""",
        """CREATE TABLE IF NOT EXISTS disclosure_template_audit (
            id UUID PRIMARY KEY, action TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
            before_state JSONB, after_state JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS document_writing_scope_decisions (
            request_id UUID PRIMARY KEY, job_uuid VARCHAR(36) NOT NULL REFERENCES analysis_jobs(job_uuid),
            document_version_id BIGINT NOT NULL REFERENCES fiscal_document_versions(id),
            file_hash TEXT NOT NULL CHECK (file_hash ~ '^[0-9a-f]{64}$'),
            revision INTEGER NOT NULL CHECK (revision > 0), analysis_basis_token TEXT NOT NULL,
            writing_scope TEXT NOT NULL CHECK (writing_scope IN ('unit','department','unknown')),
            note TEXT NOT NULL, evidence_reference TEXT NOT NULL, source_evidence JSONB NOT NULL,
            conflict_acknowledged BOOLEAN NOT NULL, actor TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            dispatch_status TEXT NOT NULL DEFAULT 'pending' CHECK (dispatch_status IN ('pending','queued','applied')),
            dispatch_error TEXT, dispatch_attempts INTEGER NOT NULL DEFAULT 0, retry_not_before TIMESTAMPTZ,
            UNIQUE(job_uuid, document_version_id, revision)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_writing_scope_pending ON document_writing_scope_decisions(created_at) WHERE dispatch_status='pending'",
        """CREATE OR REPLACE FUNCTION protect_writing_scope_decision() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'writing scope decisions are immutable'; END IF;
            IF ROW(NEW.request_id, NEW.job_uuid, NEW.document_version_id, NEW.file_hash, NEW.revision,
                   NEW.analysis_basis_token, NEW.writing_scope, NEW.note, NEW.evidence_reference,
                   NEW.source_evidence, NEW.conflict_acknowledged, NEW.actor, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.request_id, OLD.job_uuid, OLD.document_version_id, OLD.file_hash, OLD.revision,
                   OLD.analysis_basis_token, OLD.writing_scope, OLD.note, OLD.evidence_reference,
                   OLD.source_evidence, OLD.conflict_acknowledged, OLD.actor, OLD.created_at) THEN
                RAISE EXCEPTION 'writing scope decisions are immutable';
            END IF;
            RETURN NEW;
        END $$""",
        """DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='immutable_writing_scope_decision'
                           AND tgrelid=to_regclass('document_writing_scope_decisions') AND NOT tgisinternal) THEN
                CREATE TRIGGER immutable_writing_scope_decision BEFORE UPDATE OR DELETE ON document_writing_scope_decisions
                    FOR EACH ROW EXECUTE FUNCTION protect_writing_scope_decision();
            END IF;
        END $$""",
        """CREATE OR REPLACE FUNCTION reject_disclosure_template_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'template history is immutable'; END $$""",
        """DO $$ DECLARE t TEXT; BEGIN
            FOREACH t IN ARRAY ARRAY['disclosure_template_sources','disclosure_template_packages',
                'disclosure_template_binding_revisions','disclosure_template_audit'] LOOP
                IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='immutable_' || t
                               AND tgrelid=to_regclass(t) AND NOT tgisinternal) THEN
                    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION reject_disclosure_template_history_change()', 'immutable_' || t, t);
                END IF;
            END LOOP;
        END $$""",
    ],
}
