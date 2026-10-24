"""旧 SQL 规则不理解逐格资格与完整身份，禁止消费新协议的兼容展示表。"""


async def require_legacy_qc_source(conn, document_version_id):
    table = await conn.fetchval("SELECT to_regclass(current_schema() || '.published_fact_projections')::text")
    if not isinstance(table, str) or not table:
        return  # 没有新协议迁移的历史数据库继续沿用旧规则。
    bound = await conn.fetchval('SELECT EXISTS(SELECT 1 FROM published_fact_projections '
                                'WHERE document_version_id=$1)', document_version_id)
    if bound is True:
        raise ValueError('snapshot_bound_document_requires_current_analysis_rules: '
                         '请读取绑定分析版本的审校报告；旧 SQL 规则不能重新解释有效事实展示投影')
