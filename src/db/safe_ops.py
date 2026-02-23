"""
Safe Database Operations Module

Provides secure database operations with parameterized queries and schema validation.
"""
import re
import logging
from typing import Optional, List, Any, Dict
import asyncpg

logger = logging.getLogger(__name__)

SAFE_SCHEMA_PATTERN = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def validate_schema_name(schema: str) -> bool:
    if not schema:
        return False
    if len(schema) > 63:
        return False
    if schema.lower() in ('pg_', 'information_schema', 'pg_catalog'):
        return False
    return bool(SAFE_SCHEMA_PATTERN.match(schema))


def safe_identifier(identifier: str) -> str:
    if not identifier:
        raise ValueError("Identifier cannot be empty")
    if not SAFE_SCHEMA_PATTERN.match(identifier):
        raise ValueError(f"Invalid identifier: {identifier}")
    return identifier


class SafeConnection:
    def __init__(self, conn: asyncpg.Connection, schema: str = "public"):
        self._conn = conn
        self._schema = schema
        
        if not validate_schema_name(schema):
            raise ValueError(f"Invalid schema name: {schema}")
    
    async def set_schema(self):
        await self._conn.execute(
            'SET search_path TO "$1", public',
            self._schema
        )
    
    async def execute(self, query: str, *args) -> str:
        return await self._conn.execute(query, *args)
    
    async def fetch(self, query: str, *args) -> List[asyncpg.Record]:
        return await self._conn.fetch(query, *args)
    
    async def fetchrow(self, query: str, *args) -> Optional[asyncpg.Record]:
        return await self._conn.fetchrow(query, *args)
    
    async def fetchval(self, query: str, *args) -> Any:
        return await self._conn.fetchval(query, *args)
    
    async def execute_many(self, query: str, args_list: List[tuple]) -> None:
        await self._conn.executemany(query, args_list)


async def safe_set_schema(conn: asyncpg.Connection, schema: str) -> None:
    if not validate_schema_name(schema):
        logger.error(f"Attempted to use invalid schema name: {schema}")
        raise ValueError(f"Invalid schema name: {schema}")
    
    await conn.execute(f'SET search_path TO "{schema}", public')


async def safe_insert(
    conn: asyncpg.Connection,
    table: str,
    data: Dict[str, Any]
) -> asyncpg.Record:
    if not SAFE_SCHEMA_PATTERN.match(table):
        raise ValueError(f"Invalid table name: {table}")
    
    columns = list(data.keys())
    for col in columns:
        if not SAFE_SCHEMA_PATTERN.match(col):
            raise ValueError(f"Invalid column name: {col}")
    
    placeholders = [f"${i+1}" for i in range(len(data))]
    values = list(data.values())
    
    query = f'''
        INSERT INTO {table} ({', '.join(columns)})
        VALUES ({', '.join(placeholders)})
        RETURNING *
    '''
    
    return await conn.fetchrow(query, *values)


async def safe_update(
    conn: asyncpg.Connection,
    table: str,
    data: Dict[str, Any],
    where_clause: str,
    where_params: tuple
) -> str:
    if not SAFE_SCHEMA_PATTERN.match(table):
        raise ValueError(f"Invalid table name: {table}")
    
    set_clauses = []
    values = []
    for i, (col, val) in enumerate(data.items()):
        if not SAFE_SCHEMA_PATTERN.match(col):
            raise ValueError(f"Invalid column name: {col}")
        set_clauses.append(f"{col} = ${i+1}")
        values.append(val)
    
    values.extend(where_params)
    
    query = f'''
        UPDATE {table}
        SET {', '.join(set_clauses)}
        WHERE {where_clause}
    '''
    
    return await conn.execute(query, *values)


async def safe_select(
    conn: asyncpg.Connection,
    table: str,
    columns: List[str] = None,
    where_clause: str = None,
    where_params: tuple = None,
    order_by: str = None,
    limit: int = None
) -> List[asyncpg.Record]:
    if not SAFE_SCHEMA_PATTERN.match(table):
        raise ValueError(f"Invalid table name: {table}")
    
    if columns:
        for col in columns:
            if col != '*' and not SAFE_SCHEMA_PATTERN.match(col):
                raise ValueError(f"Invalid column name: {col}")
        col_str = ', '.join(columns)
    else:
        col_str = '*'
    
    query = f"SELECT {col_str} FROM {table}"
    
    if where_clause:
        query += f" WHERE {where_clause}"
    
    if order_by and SAFE_SCHEMA_PATTERN.match(order_by.replace(' DESC', '').replace(' ASC', '').strip()):
        query += f" ORDER BY {order_by}"
    
    if limit:
        query += f" LIMIT {int(limit)}"
    
    if where_params:
        return await conn.fetch(query, *where_params)
    return await conn.fetch(query)
