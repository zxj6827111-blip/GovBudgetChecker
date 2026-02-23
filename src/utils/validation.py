"""
Data Validation Utilities

Unified functions for handling null values, type conversions, and data validation
across the QC system.
"""

from typing import Any, Optional, Union
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


def safe_float(
    value: Any,
    null_as_zero: bool = False,
    raise_on_error: bool = False
) -> Optional[float]:
    """
    Convert value to float with consistent null handling.
    
    Args:
        value: Input value (int, float, Decimal, str, None)
        null_as_zero: If True, treat None/empty as 0.0
        raise_on_error: If True, raise ValueError on conversion failure
    
    Returns:
        Float value or None
    
    Examples:
        >>> safe_float(None, null_as_zero=True)
        0.0
        >>> safe_float(None, null_as_zero=False)
        None
        >>> safe_float(Decimal('123.45'))
        123.45
        >>> safe_float('N/A')
        None
    """
    # Handle None
    if value is None:
        return 0.0 if null_as_zero else None
    
    # Handle empty string or special values
    if isinstance(value, str):
        value_clean = value.strip()
        if not value_clean or value_clean.upper() in ('N/A', 'NULL', '-', ''):
            return 0.0 if null_as_zero else None
        
        try:
            return float(value_clean)
        except ValueError as e:
            if raise_on_error:
                raise ValueError(f"Cannot convert '{value}' to float") from e
            logger.warning(f"Failed to convert '{value}' to float, returning None")
            return None
    
    # Handle Decimal
    if isinstance(value, Decimal):
        return float(value)
    
    # Handle numeric types
    try:
        return float(value)
    except (ValueError, TypeError) as e:
        if raise_on_error:
            raise ValueError(f"Cannot convert {type(value).__name__} to float") from e
        logger.warning(f"Failed to convert {value} ({type(value)}) to float")
        return None


def safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """
    Convert value to int with error handling.
    
    Args:
        value: Input value
        default: Default value if conversion fails
    
    Returns:
        Integer value or default
    """
    if value is None:
        return default
    
    if isinstance(value, str):
        value = value.strip()
        if not value or value.upper() in ('N/A', 'NULL'):
            return default
    
    try:
        return int(float(value))  # Handle strings like "123.0"
    except (ValueError, TypeError):
        logger.warning(f"Failed to convert '{value}' to int, using default={default}")
        return default


def validate_amount(
    amount: Any,
    table_code: str,
    row_order: int,
    allow_negative: bool = False,
    max_value: float = 1e12
) -> Optional[float]:
    """
    Validate and normalize monetary amount.
    
    Args:
        amount: Raw amount value
        table_code: Source table code for logging
        row_order: Source row for logging
        allow_negative: Whether to allow negative values
        max_value: Maximum acceptable value
    
    Returns:
        Validated float or None
    
    Raises:
        ValueError: If amount fails validation
    """
    # Convert to float
    value = safe_float(amount)
    
    if value is None:
        return None
    
    # Check negative
    if not allow_negative and value < 0:
        logger.warning(
            f"{table_code}[{row_order}]: Negative amount {value} (not allowed)"
        )
        raise ValueError(f"Negative amount not allowed: {value}")
    
    # Check max value
    if abs(value) > max_value:
        logger.warning(
            f"{table_code}[{row_order}]: Amount {value} exceeds max {max_value}"
        )
        raise ValueError(f"Amount {value} exceeds maximum {max_value}")
    
    return value


def normalize_table_code(raw_code: str) -> str:
    """
    Normalize table code to canonical format.
    
    Args:
        raw_code: Raw table code (may have variations)
    
    Returns:
        Canonical table code
    
    Examples:
        >>> normalize_table_code('fin_03')
        'FIN_03_expenditure'
        >>> normalize_table_code('FIN03')
        'FIN_03_expenditure'
    """
    # Simple normalization (can be extended)
    code_upper = raw_code.upper().replace('-', '_')
    
    # Add underscores if missing
    if code_upper.startswith('FIN') and '_' not in code_upper:
        # FIN03 -> FIN_03
        code_upper = code_upper[:3] + '_' + code_upper[3:]
    
    return code_upper


def is_empty_cell(value: Any) -> bool:
    """
    Check if cell value is considered empty.
    
    Args:
        value: Cell value
    
    Returns:
        True if empty
    """
    if value is None:
        return True
    
    if isinstance(value, str):
        return not value.strip() or value.strip().upper() in ('N/A', 'NULL', '-')
    
    if isinstance(value, (int, float, Decimal)):
        return value == 0
    
    return False


class DataValidator:
    """
    Data validation helper for QC rules.
    """
    
    def __init__(self, null_as_zero: bool = False, tolerance: float = 0.01):
        self.null_as_zero = null_as_zero
        self.tolerance = tolerance
    
    def to_float(self, value: Any) -> Optional[float]:
        """Convert to float using validator's null_as_zero setting."""
        return safe_float(value, null_as_zero=self.null_as_zero)
    
    def check_equal(self, lhs: Any, rhs: Any) -> tuple[bool, float]:
        """
        Check if two values are equal within tolerance.
        
        Returns:
            (is_equal, difference)
        """
        lhs_f = self.to_float(lhs)
        rhs_f = self.to_float(rhs)
        
        # Handle both None
        if lhs_f is None and rhs_f is None:
            return True, 0.0
        
        # Handle one None
        if lhs_f is None:
            lhs_f = 0.0
        if rhs_f is None:
            rhs_f = 0.0
        
        diff = abs(lhs_f - rhs_f)
        return diff <= self.tolerance, diff
    
    def sum_values(self, *values: Any) -> float:
        """Sum multiple values with null handling."""
        total = 0.0
        for v in values:
            f = self.to_float(v)
            if f is not None:
                total += f
        return total
