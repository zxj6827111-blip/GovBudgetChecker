from urllib.parse import quote

import pytest

from src.db.connection import DatabaseConnection


def test_validate_database_url_rejects_placeholder_password():
    with pytest.raises(ValueError, match="placeholder password"):
        DatabaseConnection._validate_database_url(
            "postgres://postgres:your_password@localhost:5432/fiscal_db"
        )


def test_validate_database_url_accepts_real_password():
    DatabaseConnection._validate_database_url(
        f"postgres://postgres:{quote('postgres')}@localhost:5432/fiscal_db"
    )
