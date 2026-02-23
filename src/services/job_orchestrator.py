"""
Job Orchestration Service

Manages the 4-stage document processing pipeline:
1. Parse: PDF → fiscal_table_cells
2. Materialize: cells → fact_fiscal_line_items  
3. QC: Execute QC rules
4. Report: Generate PDF report
"""

import logging
import os
from typing import Dict, Any, Optional
from pathlib import Path
import asyncpg

logger = logging.getLogger(__name__)

# Upload directory
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


class JobOrchestrator:
    """Manages job execution through 4 stages."""
    
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn
    
    async def create_job(self, document_version_id: int, batch_id: Optional[int] = None) -> int:
        """Create a new job for document processing."""
        job_id = await self.conn.fetchval("""
            INSERT INTO jobs (document_version_id, batch_id, current_stage, status, started_at)
            VALUES ($1, $2, 'parse', 'queued', NOW())
            RETURNING id
        """, document_version_id, batch_id)
        
        logger.info(f"Created job {job_id} for document_version {document_version_id}")
        return job_id
    
    async def get_job_status(self, job_id: int) -> Dict[str, Any]:
        """Get current job status with stage breakdown."""
        job = await self.conn.fetchrow("""
            SELECT j.*, 
                   d.org_unit_id, d.fiscal_year, d.doc_type,
                   o.org_name
            FROM jobs j
            LEFT JOIN fiscal_document_versions v ON j.document_version_id = v.id
            LEFT JOIN fiscal_documents d ON v.document_id = d.id
            LEFT JOIN org_units o ON d.org_unit_id = o.id
            WHERE j.id = $1
        """, job_id)
        
        if not job:
            return None
        
        # Build stage status
        stages = {
            "parse": await self._get_stage_status(job, "parse"),
            "materialize": await self._get_stage_status(job, "materialize"),
            "qc": await self._get_stage_status(job, "qc"),
            "report": await self._get_stage_status(job, "report")
        }
        
        return {
            "job_id": job['id'],
            "document_version_id": job['document_version_id'],
            "org_name": job.get('org_name'),
            "fiscal_year": job.get('fiscal_year'),
            "doc_type": job.get('doc_type'),
            "current_stage": job['current_stage'],
            "status": job['status'],
            "stages": stages,
            "report_path": job['report_path'],
            "started_at": job['started_at'],
            "completed_at": job['completed_at'],
            "failure_reason": job['failure_reason']
        }
    
    async def _get_stage_status(self, job: asyncpg.Record, stage: str) -> Dict[str, Any]:
        """Get status for a specific stage."""
        current_stage = job['current_stage']
        status = job['status']
        
        # Determine stage status
        stage_order = ['parse', 'materialize', 'qc', 'report']
        current_idx = stage_order.index(current_stage) if current_stage in stage_order else -1
        stage_idx = stage_order.index(stage)
        
        if stage_idx < current_idx:
            stage_status = "completed"
        elif stage_idx == current_idx:
            stage_status = status  # queued/running/completed/failed
        else:
            stage_status = "pending"
        
        # Get stage-specific details
        details = {}
        if stage == 'parse':
            details = job.get('parse_log', {}) or {}
        elif stage == 'materialize':
            details = job.get('materialize_log', {}) or {}
        elif stage == 'qc' and job.get('qc_run_id'):
            # Get QC run details
            qc_info = await self.conn.fetchrow("""
                SELECT COUNT(*) as findings_count,
                       SUM(CASE WHEN status = 'pass' THEN 1 ELSE 0 END) as pass_count,
                       SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) as fail_count
                FROM qc_findings_v2
                WHERE run_id = $1
            """, job['qc_run_id'])
            if qc_info:
                details = dict(qc_info)
            details['run_id'] = job['qc_run_id']
        elif stage == 'report' and job.get('report_path'):
            details = {'report_path': job['report_path']}
        
        return {
            "status": stage_status,
            "details": details
        }
    
    async def update_job_stage(self, job_id: int, new_stage: str, log_data: Optional[Dict] = None):
        """Update job to next stage."""
        import json
        log_field = f"{new_stage}_log" if new_stage in ['parse', 'materialize'] else None
        
        if log_field and log_data:
            # Convert dict to JSON string for PostgreSQL JSONB
            await self.conn.execute(f"""
                UPDATE jobs 
                SET current_stage = $1, {log_field} = $2::jsonb
                WHERE id = $3
            """, new_stage, json.dumps(log_data), job_id)
        else:
            await self.conn.execute("""
                UPDATE jobs 
                SET current_stage = $1
                WHERE id = $2
            """, new_stage, job_id)
        
        logger.info(f"Job {job_id} moved to stage: {new_stage}")
    
    async def update_job_status(self, job_id: int, status: str):
        """Update job status (queued/running/completed/failed)."""
        await self.conn.execute("""
            UPDATE jobs 
            SET status = $1
            WHERE id = $2
        """, status, job_id)
    
    async def complete_job(self, job_id: int, report_path: Optional[str] = None):
        """Mark job as completed."""
        await self.conn.execute("""
            UPDATE jobs 
            SET status = 'completed', 
                completed_at = NOW(),
                report_path = $1
            WHERE id = $2
        """, report_path, job_id)
        
        logger.info(f"Job {job_id} completed successfully")
    
    async def fail_job(self, job_id: int, reason: str):
        """Mark job as failed."""
        await self.conn.execute("""
            UPDATE jobs 
            SET status = 'failed', 
                failed_at = NOW(),
                failure_reason = $1
            WHERE id = $2
        """, reason, job_id)
        
        logger.error(f"Job {job_id} failed: {reason}")
    
    async def execute_pipeline(self, job_id: int):
        """
        Execute the full 4-stage pipeline for a job.
        
        Stages:
        1. Parse: PDF → fiscal_table_cells (skipped, assumes data exists)
        2. Materialize: Table recognition + column mapping
        3. QC: Execute QC rules V3 (R001-R010)
        4. Report: Generate PDF report
        """
        from src.qc.runner_v3 import QCRunnerV3
        from src.services.table_recognizer import TableRecognizer
        from src.services.pdf_generator import generate_pdf_report
        from src.services.pdf_parser import PDFParser
        
        try:
            # Update to running
            await self.update_job_status(job_id, 'running')
            
            # Get job info
            job = await self.conn.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
            version_id = job['document_version_id']
            
            # Get version and file path
            version = await self.conn.fetchrow("""
                SELECT v.*, d.org_unit_id 
                FROM fiscal_document_versions v
                JOIN fiscal_documents d ON v.document_id = d.id
                WHERE v.id = $1
            """, version_id)
            
            # Stage 1: Parse (PDF → cells)
            pdf_path = version.get('storage_path') if version else None
            
            if pdf_path and Path(pdf_path).exists():
                # Parse PDF file
                logger.info(f"Job {job_id}: Stage 1 - Parse (extracting from PDF)")
                parser = PDFParser(self.conn)
                parse_result = await parser.parse_pdf(pdf_path, version_id)
                
                cells_count = parse_result.get('cells_count', 0)
                tables_count = parse_result.get('tables_count', 0)
                
                if cells_count == 0:
                    await self.fail_job(job_id, f"PDF parsing extracted 0 cells: {parse_result.get('errors', 'unknown error')}")
                    return
                
                await self.update_job_stage(job_id, 'materialize', {
                    'cells_count': cells_count, 
                    'tables_count': tables_count,
                    'source': 'pdf_parse'
                })
            else:
                # Check for existing cells (imported via CSV)
                logger.info(f"Job {job_id}: Stage 1 - Parse (using existing cells)")
                cells_count = await self.conn.fetchval("""
                    SELECT COUNT(*) FROM fiscal_table_cells WHERE document_version_id = $1
                """, version_id)
                
                if cells_count == 0:
                    await self.fail_job(job_id, "No cells found and no PDF available for parsing")
                    return
                
                await self.update_job_stage(job_id, 'materialize', {
                    'cells_count': cells_count
                })
            
            # Stage 2: Materialize (Table Recognition)
            logger.info(f"Job {job_id}: Stage 2 - Materialize (Table Recognition)")
            recognizer = TableRecognizer(self.conn)
            instances = await recognizer.recognize_tables(version_id)
            await recognizer.save_table_instances(version_id, instances)
            
            facts_count = await self.conn.fetchval("""
                SELECT COUNT(*) FROM fact_fiscal_line_items WHERE document_version_id = $1
            """, version_id)
            
            await self.update_job_stage(job_id, 'qc', {
                'facts_count': facts_count,
                'tables_recognized': len(instances)
            })
            
            # Stage 3: QC (Using V3 with R001-R010)
            logger.info(f"Job {job_id}: Stage 3 - QC (V3 with 10 rules)")
            runner = QCRunnerV3(self.conn, version_id)
            qc_run_id = await runner.execute()
            
            await self.conn.execute("""
                UPDATE jobs SET qc_run_id = $1 WHERE id = $2
            """, qc_run_id, job_id)
            await self.update_job_stage(job_id, 'report')
            
            # Stage 4: Report (PDF Generation)
            logger.info(f"Job {job_id}: Stage 4 - Report (PDF)")
            pdf_path = await generate_pdf_report(qc_run_id, self.conn)
            
            # Complete job
            await self.complete_job(job_id, str(pdf_path))
            logger.info(f"Job {job_id} completed successfully, report: {pdf_path}")
            
        except Exception as e:
            logger.exception(f"Job {job_id} failed")
            await self.fail_job(job_id, str(e))
            raise
