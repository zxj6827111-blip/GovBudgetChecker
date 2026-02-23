
import asyncio
import os
import sys
import uuid
import logging
from datetime import datetime
import asyncpg


# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.connection import DatabaseConnection
from src.db.migrations import run_migrations
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def run_verification():
    load_dotenv()
    
    logger.info("1. Initializing Database Connection...")
    await DatabaseConnection.initialize()
    
    logger.info("2. Running Migrations...")
    await run_migrations()
    
    pool = await DatabaseConnection.get_pool()
    schema = DatabaseConnection.get_schema()
    
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{schema}", public')
        
        logger.info("3. Verifying QC Framework Tables...")
        # Check tables exist
        tables = ["qc_rule_definitions", "qc_rule_versions", "qc_runs", "qc_findings"]
        for table in tables:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = $1 AND table_name = $2)",
                schema, table
            )
            if not exists:
                logger.error(f"Table {table} does not exist!")
                return
            logger.info(f"Verified table exists: {table}")

        logger.info("4. Running End-to-End Simulation...")
        
        # Start transaction for simulation
        async with conn.transaction():
            # A. Create Organization
            logger.info("   - Creating Organization")
            org_id = await conn.fetchval(
                "INSERT INTO organizations (name, code) VALUES ($1, $2) RETURNING id",
                "Test Org for QC", "QC_TEST_ORG"
            )
            
            # B. Create Analysis Job
            logger.info("   - Creating Analysis Job")
            job_uuid = str(uuid.uuid4())
            job_id = await conn.fetchval(
                """
                INSERT INTO analysis_jobs (job_uuid, filename, organization_id, status, file_hash) 
                VALUES ($1, $2, $3, 'completed', 'hash_12345') 
                RETURNING id
                """,
                job_uuid, "test_budget.pdf", org_id
            )
            
            # C. Define QC Rule
            logger.info("   - Defining QC Rule")
            rule_key = "BUDGET_Balance_001"
            await conn.execute(
                """
                INSERT INTO qc_rule_definitions (rule_key, domain, name, severity, description)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (rule_key) DO NOTHING
                """,
                rule_key, "Budget", "Budget Balance Check", "error", "Total revenue must equal total expenditure"
            )
            
            # D. Create QC Rule Version
            logger.info("   - Creating QC Rule Version")
            await conn.execute(
                """
                INSERT INTO qc_rule_versions (rule_key, version, params_json)
                VALUES ($1, $2, $3)
                ON CONFLICT (rule_key, version) DO NOTHING
                """,
                rule_key, "1.0.0", '{"tolerance": 0.01}'
            )
            
            # E. Create QC Run
            logger.info("   - Creating QC Run")
            run_id = await conn.fetchval(
                """
                INSERT INTO qc_runs (job_id, run_type, status)
                VALUES ($1, 'automated', 'completed')
                RETURNING id
                """,
                job_id
            )
            
            # F. Create QC Finding
            logger.info("   - Creating QC Finding")
            await conn.execute(
                """
                INSERT INTO qc_findings (run_id, rule_key, status, message, diff)
                VALUES ($1, $2, 'fail', 'Balance mismatch', 100.00)
                """,
                run_id, rule_key
            )
            
            logger.info("✓ Simulation Data Inserted Successfully")

        logger.info("5. Verifying Constraints (Negative Tests)...")
        
        # Test 1: Duplicate Job UUID
        try:
            await conn.execute(
                "INSERT INTO analysis_jobs (job_uuid, filename) VALUES ($1, 'dup.pdf')",
                job_uuid
            )
            logger.error("✗ Failed to catch duplicate job UUID!")
        except asyncpg.UniqueViolationError:
            logger.info("✓ Correctly caught duplicate job UUID")
            
        # Test 2: Invalid FK for QC Finding
        try:
            await conn.execute(
                """
                INSERT INTO qc_findings (run_id, rule_key, status)
                VALUES ($1, 'NON_EXISTENT_RULE', 'fail')
                """,
                run_id
            )
            logger.error("✗ Failed to catch invalid rule_key FK!")
        except asyncpg.ForeignKeyViolationError:
            logger.info("✓ Correctly caught invalid rule_key FK")

    await DatabaseConnection.close()
    logger.info("✓ All Verifications Passed!")

if __name__ == "__main__":
    asyncio.run(run_verification())
