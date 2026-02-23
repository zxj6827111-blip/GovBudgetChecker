"""
PDF Parser Service

Extracts tables from PDF documents using pdfplumber and persists to fiscal_table_cells.
Supports multi-table documents with automatic table detection.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import asyncpg
import re

logger = logging.getLogger(__name__)


class PDFParser:
    """
    Parses PDF documents and extracts fiscal tables.
    """
    
    # Table name patterns for identification
    TABLE_PATTERNS = {
        'FIN_01': r'收入.*?支出.*?总表|收支.*?总表',
        'FIN_02': r'收入.*?决算表|收入表',
        'FIN_03': r'支出.*?决算表|支出表',
        'FIN_04': r'财政拨款.*?收入支出|拨款.*?总表',
        'FIN_05': r'一般公共.*?支出|功能.*?支出',
        'FIN_06': r'基本支出.*?经济|经济分类.*?支出',
        'FIN_07': r'三公.*?经费|"三公"',
        'FIN_08': r'政府性基金',
        'FIN_09': r'国有资本',
    }
    
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn
    
    async def parse_pdf(self, pdf_path: str, document_version_id: int) -> Dict[str, Any]:
        """
        Parse a PDF file and extract all fiscal tables.
        
        Args:
            pdf_path: Path to PDF file
            document_version_id: Target document version ID
        
        Returns:
            Dict with parsing results (tables_count, cells_count, errors)
        """
        try:
            import pdfplumber
        except ImportError:
            logger.error("pdfplumber not installed. Run: pip install pdfplumber")
            return {"error": "pdfplumber not installed", "tables_count": 0, "cells_count": 0}
        
        path = Path(pdf_path)
        if not path.exists():
            return {"error": f"File not found: {pdf_path}", "tables_count": 0, "cells_count": 0}
        
        logger.info(f"Parsing PDF: {pdf_path}")
        
        tables_count = 0
        cells_count = 0
        errors = []
        
        try:
            with pdfplumber.open(path) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    # Extract tables from page
                    tables = page.extract_tables({
                        'snap_tolerance': 3,
                        'join_tolerance': 3,
                        'edge_min_length': 3,
                    })
                    
                    if not tables:
                        continue
                    
                    for table_idx, table in enumerate(tables):
                        if not table or len(table) < 2:
                            continue
                        
                        # Identify table type
                        table_code = self._identify_table(table, page_num, table_idx)
                        
                        # Extract cells
                        extracted = await self._extract_and_save_cells(
                            table, table_code, document_version_id, page_num
                        )
                        
                        tables_count += 1
                        cells_count += extracted
                        
                        logger.info(f"Page {page_num}, Table {table_idx}: {table_code} ({extracted} cells)")
            
        except Exception as e:
            logger.error(f"PDF parsing failed: {e}")
            errors.append(str(e))
        
        return {
            "tables_count": tables_count,
            "cells_count": cells_count,
            "errors": errors if errors else None,
            "success": len(errors) == 0
        }
    
    def _identify_table(self, table: List[List[str]], page_num: int, table_idx: int) -> str:
        """
        Identify table type based on content patterns.
        
        Returns table code like 'FIN_01_income_expenditure_total'.
        """
        # Get first few rows for analysis
        sample_text = " ".join(
            str(cell) for row in table[:3] for cell in row if cell
        ).lower()
        
        # Try to match against patterns
        for code, pattern in self.TABLE_PATTERNS.items():
            if re.search(pattern, sample_text, re.IGNORECASE):
                # Return full canonical code
                code_map = {
                    'FIN_01': 'FIN_01_income_expenditure_total',
                    'FIN_02': 'FIN_02_income',
                    'FIN_03': 'FIN_03_expenditure',
                    'FIN_04': 'FIN_04_fiscal_grant_total',
                    'FIN_05': 'FIN_05_general_public_expenditure',
                    'FIN_06': 'FIN_06_basic_expenditure',
                    'FIN_07': 'FIN_07_three_public',
                    'FIN_08': 'FIN_08_gov_fund',
                    'FIN_09': 'FIN_09_state_capital',
                }
                return code_map.get(code, code)
        
        # Fallback: use page and index
        return f'UNKNOWN_P{page_num}_T{table_idx}'
    
    async def _extract_and_save_cells(
        self, 
        table: List[List[str]], 
        table_code: str, 
        document_version_id: int,
        page_num: int
    ) -> int:
        """Extract cells from table and save to database."""
        cells_saved = 0
        
        for row_idx, row in enumerate(table):
            for col_idx, cell_value in enumerate(row):
                if cell_value is None:
                    cell_value = ''
                
                # Clean cell value
                raw_text = str(cell_value).strip()
                
                # Try to parse as number
                numeric_value = self._parse_numeric(raw_text)
                
                # Save to database
                await self.conn.execute("""
                    INSERT INTO fiscal_table_cells 
                    (document_version_id, table_code, row_idx, col_idx, raw_text, numeric_value, page_number)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (document_version_id, table_code, row_idx, col_idx) 
                    DO UPDATE SET raw_text = EXCLUDED.raw_text, numeric_value = EXCLUDED.numeric_value
                """, document_version_id, table_code, row_idx, col_idx, raw_text, numeric_value, page_num)
                
                cells_saved += 1
        
        return cells_saved
    
    def _parse_numeric(self, text: str) -> Optional[float]:
        """Parse text as numeric value if possible."""
        if not text:
            return None
        
        # Remove common formatting
        cleaned = text.replace(',', '').replace(' ', '').replace('，', '')
        
        # Handle negative with parentheses
        if cleaned.startswith('(') and cleaned.endswith(')'):
            cleaned = '-' + cleaned[1:-1]
        
        try:
            return float(cleaned)
        except ValueError:
            return None


async def parse_pdf_document(
    pdf_path: str, 
    document_version_id: int, 
    conn: asyncpg.Connection
) -> Dict[str, Any]:
    """
    Convenience function to parse a PDF and save to database.
    
    Returns parsing result dict.
    """
    parser = PDFParser(conn)
    return await parser.parse_pdf(pdf_path, document_version_id)
