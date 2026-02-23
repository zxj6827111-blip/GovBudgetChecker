"""
Fiscal Data Import Script

Imports CSV data into PostgreSQL tables:
1. Creates org_unit and fiscal_document if not exist
2. Creates a document_version
3. Imports raw_cells.csv into fiscal_table_cells
4. Imports facts_line_items.csv into fact_fiscal_line_items

Usage:
    python scripts/import_fiscal_csv.py

Requires:
    - PostgreSQL running with fiscal_db database
    - DATABASE_URL in .env
    - CSV files in tests/ directory
"""

import asyncio
import os
import sys
import csv
import hashlib
import json
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.db.connection import DatabaseConnection
from src.db.migrations import run_migrations

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Default values for this import (from CSV data)
DEFAULT_REGION = "上海市普陀区"
DEFAULT_ORG_UNIT = "上海市普陀区人民政府办公室"
DEFAULT_FISCAL_YEAR = 2024
DEFAULT_DOC_TYPE = "final_accounts"
FILE_HASH = hashlib.md5(b"putuo_govoffice_2024_sample").hexdigest()

# CSV file paths
PROJECT_ROOT = Path(__file__).parent.parent
CELLS_CSV = PROJECT_ROOT / "tests" / "putuo_govoffice_2024_raw_cells.csv"
FACTS_CSV = PROJECT_ROOT / "tests" / "putuo_govoffice_2024_facts_line_items.csv"


async def ensure_org_and_document(conn) -> int:
    """Create org_unit and fiscal_document, return document_version_id."""
    
    # 1. Upsert org_unit
    org_id = await conn.fetchval("""
        INSERT INTO org_units (org_name, region)
        VALUES ($1, $2)
        ON CONFLICT (org_name) DO UPDATE SET region = EXCLUDED.region
        RETURNING id
    """, DEFAULT_ORG_UNIT, DEFAULT_REGION)
    logger.info(f"Org unit id: {org_id}")
    
    # 2. Upsert fiscal_document
    doc_id = await conn.fetchval("""
        INSERT INTO fiscal_documents (org_unit_id, fiscal_year, doc_type)
        VALUES ($1, $2, $3)
        ON CONFLICT (org_unit_id, fiscal_year, doc_type) DO NOTHING
        RETURNING id
    """, org_id, DEFAULT_FISCAL_YEAR, DEFAULT_DOC_TYPE)
    
    if doc_id is None:
        doc_id = await conn.fetchval("""
            SELECT id FROM fiscal_documents
            WHERE org_unit_id = $1 AND fiscal_year = $2 AND doc_type = $3
        """, org_id, DEFAULT_FISCAL_YEAR, DEFAULT_DOC_TYPE)
    logger.info(f"Document id: {doc_id}")
    
    # 3. Upsert document_version
    version_id = await conn.fetchval("""
        INSERT INTO fiscal_document_versions (document_id, file_hash)
        VALUES ($1, $2)
        ON CONFLICT (document_id, file_hash) DO NOTHING
        RETURNING id
    """, doc_id, FILE_HASH)
    
    if version_id is None:
        version_id = await conn.fetchval("""
            SELECT id FROM fiscal_document_versions
            WHERE document_id = $1 AND file_hash = $2
        """, doc_id, FILE_HASH)
    logger.info(f"Document version id: {version_id}")
    
    return version_id


async def import_cells(conn, version_id: int) -> int:
    """Import raw_cells.csv into fiscal_table_cells."""
    
    # Clear existing cells for this version (idempotent)
    await conn.execute("""
        DELETE FROM fiscal_table_cells WHERE document_version_id = $1
    """, version_id)
    
    count = 0
    # Use newline='' to handle embedded newlines and utf-8-sig to handle BOM
    with open(CELLS_CSV, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if 'table_code' not in row:
                logger.warning(f"Skipping malformed row: {row}")
                continue
            await conn.execute("""
                INSERT INTO fiscal_table_cells 
                (document_version_id, table_code, row_idx, col_idx, raw_text)
                VALUES ($1, $2, $3, $4, $5)
            """, 
                version_id,
                row['table_code'],
                int(row['row_idx']),
                int(row['col_idx']),
                row['raw_text']
            )
            count += 1
    
    return count


async def import_facts(conn, version_id: int) -> int:
    """Import facts_line_items.csv into fact_fiscal_line_items."""
    
    # Clear existing facts for this version (idempotent)
    await conn.execute("""
        DELETE FROM fact_fiscal_line_items WHERE document_version_id = $1
    """, version_id)
    
    count = 0
    # Use newline='' to handle embedded newlines and utf-8-sig to handle BOM
    with open(FACTS_CSV, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if 'table_code' not in row:
                logger.warning(f"Skipping malformed row: {row}")
                continue
            
            # Parse amount
            amount = None
            if row.get('amount'):
                try:
                    amount = float(row['amount'])
                except ValueError:
                    pass
            
            # Parse extra_dims
            extra_dims = {}
            if row.get('extra_dims'):
                try:
                    extra_dims = json.loads(row['extra_dims'])
                except json.JSONDecodeError:
                    pass
            
            # Parse row_order
            row_order = None
            if row.get('row_order'):
                try:
                    row_order = float(row['row_order'])
                except ValueError:
                    pass
            
            await conn.execute("""
                INSERT INTO fact_fiscal_line_items 
                (document_version_id, table_code, statement_code, classification_type,
                 classification_code, classification_name, measure, amount, extra_dims, row_order)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
                version_id,
                row['table_code'],
                row['statement_code'],
                row['classification_type'],
                row.get('classification_code') or None,
                row.get('classification_name') or None,
                row['measure'],
                amount,
                json.dumps(extra_dims),
                row_order
            )
            count += 1
    
    return count


async def main():
    logger.info("Starting fiscal data import...")
    
    # Initialize DB and run migrations
    await DatabaseConnection.initialize()
    await run_migrations()
    
    pool = await DatabaseConnection.get_pool()
    schema = DatabaseConnection.get_schema()
    
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{schema}", public')
        
        async with conn.transaction():
            # Create org/doc/version
            version_id = await ensure_org_and_document(conn)
            
            # Import cells
            cells_count = await import_cells(conn, version_id)
            logger.info(f"Imported {cells_count} cells")
            
            # Import facts
            facts_count = await import_facts(conn, version_id)
            logger.info(f"Imported {facts_count} facts")
    
    # Verify counts
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{schema}", public')
        
        cells_total = await conn.fetchval("SELECT COUNT(*) FROM fiscal_table_cells")
        facts_total = await conn.fetchval("SELECT COUNT(*) FROM fact_fiscal_line_items")
        
        logger.info(f"✓ Total cells in DB: {cells_total}")
        logger.info(f"✓ Total facts in DB: {facts_total}")
    
    await DatabaseConnection.close()
    logger.info("✓ Import completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
