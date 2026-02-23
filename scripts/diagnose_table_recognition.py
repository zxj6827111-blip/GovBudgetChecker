"""
Diagnostic script to analyze table recognition issues
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.db.connection import DatabaseConnection


async def main():
    await DatabaseConnection.initialize()
    pool = await DatabaseConnection.get_pool()
    schema = DatabaseConnection.get_schema()
    
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{schema}", public')
        
        # Get document version
        version = await conn.fetchrow("""
            SELECT id FROM fiscal_document_versions LIMIT 1
        """)
        
        if not version:
            print("No document versions found")
            return
        
        version_id = version['id']
        print(f"Analyzing version {version_id}\n")
        
        # Get all tables
        tables = await conn.fetch("""
            SELECT DISTINCT table_code
            FROM fiscal_table_cells
            WHERE document_version_id = $1
            ORDER BY table_code
        """, version_id)
        
        for tbl in tables:
            table_code = tbl['table_code']
            print(f"=== {table_code} ===")
            
            # Get first 5 rows
            cells = await conn.fetch("""
                SELECT row_idx, col_idx, raw_text
                FROM fiscal_table_cells
                WHERE document_version_id = $1 AND table_code = $2
                ORDER BY row_idx, col_idx
                LIMIT 30
            """, version_id, table_code)
            
            # Group by row
            rows = {}
            for c in cells:
                if c['row_idx'] not in rows:
                    rows[c['row_idx']] = []
                rows[c['row_idx']].append(c['raw_text'] or '')
            
            # Print first 3 rows
            for row_idx in sorted(rows.keys())[:3]:
                row_text = ' | '.join(rows[row_idx])
                print(f"  Row {row_idx}: {row_text}")
            
            print()
    
    await DatabaseConnection.close()


if __name__ == "__main__":
    asyncio.run(main())
