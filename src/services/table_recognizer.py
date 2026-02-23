"""
Table Recognition Engine

Identifies fiscal tables from parsed cells and maps columns to canonical measures.
Supports flexible matching for regional variations in table titles and column names.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import re
import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class ColumnMapping:
    """Column mapping from source to canonical measure."""
    source_col_idx: int
    source_col_name: str
    canonical_measure: str
    confidence: float


@dataclass
class TableInstance:
    """Recognized table instance."""
    table_code: str
    source_title: str
    confidence: float
    page_number: Optional[int]
    row_start: Optional[int]
    row_end: Optional[int]
    column_mappings: List[ColumnMapping]


# Table Recognition Rules Database
TABLE_RECOGNITION_RULES = {
    "FIN_01_income_expenditure_total": {
        "title_keywords": ["收入支出.*?总表", "收支.*?总表", "收入支出决算总表"],
        "required_columns": ["本年收入", "本年支出"],
        "optional_columns": ["年初结转", "年末结转"],
        "measure_patterns": {
            r"本年收入|年度收入": "income_actual",
            r"本年支出|年度支出": "expenditure_actual",
            r"年初结转|期初结转": "beginning_balance",
            r"年末结转|期末结转": "ending_balance"
        }
    },
    
    "FIN_02_income": {
        "title_keywords": ["收入决算表", "收入表"],
        "required_columns": ["合计", "财政拨款"],
        "optional_columns": ["事业收入", "经营收入", "其他收入"],
        "measure_patterns": {
            r"合计|总计": "total_actual",
            r"财政拨款": "fiscal_allocation",
            r"事业收入": "business_income",
            r"经营收入": "operational_income",
            r"其他收入": "other_income"
        }
    },
    
    "FIN_03_expenditure": {
        "title_keywords": ["支出决算表", "支出表", "财政拨款支出"],
        "required_columns": ["合计", "基本支出", "项目支出"],
        "optional_columns": ["预算数"],
        "measure_patterns": {
            r"合计|总计": "total_actual",
            r"基本支出|基本": "basic_actual",
            r"项目支出|项目": "project_actual",
            r"预算数|预算": "total_budget"
        }
    },
    
    "FIN_04_project_expenditure": {
        "title_keywords": ["项目支出.*?明细", "项目.*?明细"],
        "required_columns": ["项目名称", "金额"],
        "measure_patterns": {
            r"金额|决算数": "project_amount",
            r"预算数": "project_budget"
        }
    },
    
    "FIN_05_function_classification": {
        "title_keywords": ["功能分类.*?支出", "功能.*?支出"],
        "required_columns": ["功能分类", "合计"],
        "measure_patterns": {
            r"合计|总计": "total_actual",
            r"基本支出|基本": "basic_actual",
            r"项目支出|项目": "project_actual"
        }
    },
    
    "FIN_06_basic_expenditure": {
        "title_keywords": ["基本支出.*?经济分类", "基本支出.*?明细"],
        "required_columns": ["经济分类", "决算数"],
        "measure_patterns": {
            r"决算数|金额": "actual",
            r"预算数": "budget"
        }
    },
    
    "FIN_07_three_public": {
        "title_keywords": ["三公.*?经费", '"三公".*?经费'],
        "required_columns": ["合计", "出国"],
        "optional_columns": ["公务用车", "公务接待"],
        "measure_patterns": {
            r"合计|总计": "total_actual",
            r"决算数|实际": "actual",
            r"预算数": "budget",
            r"出国|因公出国": "overseas",
            r"购置.*?车|车辆购置": "vehicle_purchase",
            r"运行.*?车|车辆运行": "vehicle_operation",
            r"接待|公务接待": "reception"
        }
    },
    
    "FIN_08_gov_fund": {
        "title_keywords": ["政府性基金", "基金.*?支出"],
        "required_columns": ["合计"],
        "measure_patterns": {
            r"合计|总计": "total_actual",
            r"基本支出|基本": "basic_actual",
            r"项目支出|项目": "project_actual"
        }
    },
    
    "FIN_09_state_capital": {
        "title_keywords": ["国有资本", "国资.*?支出"],
        "required_columns": ["合计"],
        "measure_patterns": {
            r"合计|总计": "total_actual",
            r"基本支出|基本": "basic_actual",
            r"项目支出|项目": "project_actual"
        }
    }
}


class TableRecognizer:
    """Recognizes fiscal tables and maps columns."""
    
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn
    
    async def recognize_tables(self, document_version_id: int) -> List[TableInstance]:
        """
        Recognize all tables in a document version.
        
        Returns list of recognized table instances with column mappings.
        """
        # Get all cells for this document
        cells = await self.conn.fetch("""
            SELECT table_code, row_idx, col_idx, raw_text
            FROM fiscal_table_cells
            WHERE document_version_id = $1
            ORDER BY table_code, row_idx, col_idx
        """, document_version_id)
        
        if not cells:
            logger.warning(f"No cells found for document_version {document_version_id}")
            return []
        
        # Group by table_code (assuming already has table_code from import)
        tables_by_code = {}
        for cell in cells:
            code = cell['table_code']
            if code not in tables_by_code:
                tables_by_code[code] = []
            tables_by_code[code].append(cell)
        
        # Recognize each table
        recognized = []
        for source_table_code, table_cells in tables_by_code.items():
            instance = await self._recognize_single_table(source_table_code, table_cells)
            if instance:
                recognized.append(instance)
                logger.info(f"Recognized {instance.table_code} (confidence: {instance.confidence:.2f})")
        
        return recognized
    
    async def _recognize_single_table(self, source_table_code: str, cells: List[asyncpg.Record]) -> Optional[TableInstance]:
        """Recognize a single table from its cells."""
        # Get table title (usually first row, first column or spanning cells)
        title_candidates = [c['raw_text'] for c in cells if c['row_idx'] == 0 and c['raw_text']]
        title = " ".join(title_candidates) if title_candidates else ""
        
        # IMPROVED: Also use source_table_code for matching
        # The table_code from import often already identifies the table
        combined_title = f"{source_table_code} {title}".lower()
        
        # Get header row (usually row 0, which often contains column names)
        headers = self._extract_headers(cells)
        
        # Try to match against each rule
        best_match = None
        best_score = 0.0
        
        for canonical_code, rule in TABLE_RECOGNITION_RULES.items():
            score = self._match_table_improved(combined_title, headers, rule, canonical_code, source_table_code)
            if score > best_score and score >= 0.5:  # Lowered threshold to 50%
                best_score = score
                best_match = canonical_code
        
        if not best_match:
            logger.warning(f"Could not recognize table: {source_table_code} (title: {title[:50]})")
            return None
        
        # Create column mappings
        rule = TABLE_RECOGNITION_RULES[best_match]
        mappings = self._create_column_mappings(headers, rule)
        
        return TableInstance(
            table_code=best_match,
            source_title=title,
            confidence=best_score,
            page_number=None,
            row_start=min(c['row_idx'] for c in cells),
            row_end=max(c['row_idx'] for c in cells),
            column_mappings=mappings
        )
    
    def _match_table_improved(self, combined_title: str, headers: List[str], rule: Dict, 
                             canonical_code: str, source_code: str) -> float:
        """
        Improved matching that considers table_code directly.
        
        Strategy:
        1. If source_code contains canonical code name, high score
        2. Otherwise use title + column matching
        """
        score = 0.0
        
        # Strategy 1: Direct code matching (weight: 0.6)
        # Extract key part from canonical code (e.g., "expenditure" from "FIN_03_expenditure")
        code_parts = canonical_code.lower().split('_')
        if len(code_parts) >= 3:
            key_word = code_parts[2]  # e.g., "expenditure", "income", etc.
            if key_word in source_code.lower() or key_word in combined_title:
                score += 0.6
        
        # Special handling for numbered tables (FIN_01, FIN_02, etc.)
        canonical_num = canonical_code.split('_')[1] if '_' in canonical_code else None
        source_num = source_code.split('_')[1] if '_' in source_code else None
        if canonical_num and source_num and canonical_num == source_num:
            score += 0.5  # Number match is strong signal
        
        # Strategy 2: Title keyword matching (weight: 0.2)
        title_match = False
        for pattern in rule['title_keywords']:
            if re.search(pattern, combined_title, re.IGNORECASE):
                title_match = True
                break
        score += 0.2 if title_match else 0.0
        
        # Strategy 3: Required columns matching (weight: 0.2)
        required_cols = rule['required_columns']
        matched_required = sum(1 for req in required_cols if self._find_column(req, headers))
        if required_cols:
            score += 0.2 * (matched_required / len(required_cols))
        
        return min(score, 1.0)  # Cap at 1.0
    
    def _extract_headers(self, cells: List[asyncpg.Record]) -> List[str]:
        """Extract column headers from table cells."""
        # Try row 1 first (common header row)
        headers_r1 = [c['raw_text'] for c in cells if c['row_idx'] == 1]
        if headers_r1 and any(h for h in headers_r1):
            return headers_r1
        
        # Fallback to row 2
        headers_r2 = [c['raw_text'] for c in cells if c['row_idx'] == 2]
        return headers_r2 if headers_r2 else []
    
    def _match_table(self, title: str, headers: List[str], rule: Dict) -> float:
        """Calculate match score for a table against a rule."""
        score = 0.0
        
        # Title matching (weight: 0.4)
        title_match = False
        for pattern in rule['title_keywords']:
            if re.search(pattern, title, re.IGNORECASE):
                title_match = True
                break
        score += 0.4 if title_match else 0.0
        
        # Required columns matching (weight: 0.5)
        required_cols = rule['required_columns']
        matched_required = sum(1 for req in required_cols if self._find_column(req, headers))
        if required_cols:
            score += 0.5 * (matched_required / len(required_cols))
        
        # Optional columns matching (weight: 0.1)
        optional_cols = rule.get('optional_columns', [])
        if optional_cols:
            matched_optional = sum(1 for opt in optional_cols if self._find_column(opt, headers))
            score += 0.1 * (matched_optional / len(optional_cols))
        
        return score
    
    def _find_column(self, keyword: str, headers: List[str]) -> bool:
        """Check if keyword appears in any header (fuzzy match)."""
        for header in headers:
            if header and keyword in header:
                return True
        return False
    
    def _create_column_mappings(self, headers: List[str], rule: Dict) -> List[ColumnMapping]:
        """Create column mappings from headers to canonical measures."""
        mappings = []
        
        for col_idx, header in enumerate(headers):
            if not header:
                continue
            
            # Try to match against measure patterns
            for pattern, canonical_measure in rule['measure_patterns'].items():
                if re.search(pattern, header, re.IGNORECASE):
                    mappings.append(ColumnMapping(
                        source_col_idx=col_idx,
                        source_col_name=header,
                        canonical_measure=canonical_measure,
                        confidence=0.9
                    ))
                    break
        
        return mappings
    
    async def save_table_instances(self, document_version_id: int, instances: List[TableInstance]):
        """Save recognized table instances and column mappings to database."""
        for instance in instances:
            # Insert table instance
            instance_id = await self.conn.fetchval("""
                INSERT INTO fiscal_table_instances 
                (document_version_id, table_code, source_title, confidence, page_number, row_start, row_end)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (document_version_id, table_code) 
                DO UPDATE SET source_title = EXCLUDED.source_title, confidence = EXCLUDED.confidence
                RETURNING id
            """, document_version_id, instance.table_code, instance.source_title, 
                instance.confidence, instance.page_number, instance.row_start, instance.row_end)
            
            # Insert column mappings
            for mapping in instance.column_mappings:
                await self.conn.execute("""
                    INSERT INTO fiscal_column_mappings 
                    (table_instance_id, source_col_idx, source_col_name, canonical_measure, confidence)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (table_instance_id, source_col_idx) 
                    DO UPDATE SET canonical_measure = EXCLUDED.canonical_measure
                """, instance_id, mapping.source_col_idx, mapping.source_col_name, 
                    mapping.canonical_measure, mapping.confidence)
        
        logger.info(f"Saved {len(instances)} table instances for document_version {document_version_id}")


async def recognize_and_save_tables(document_version_id: int, conn: asyncpg.Connection) -> int:
    """
    Convenience function to recognize and save tables for a document version.
    
    Returns: Number of tables recognized.
    """
    recognizer = TableRecognizer(conn)
    instances = await recognizer.recognize_tables(document_version_id)
    await recognizer.save_table_instances(document_version_id, instances)
    return len(instances)
