"""PostgreSQL connection management with schema support."""

import os
import asyncpg
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """Manages PostgreSQL connection pool with schema configuration."""
    
    _pool: Optional[asyncpg.Pool] = None
    _schema: str = "public"
    
    @classmethod
    async def initialize(cls, database_url: Optional[str] = None) -> asyncpg.Pool:
        """Initialize the connection pool and configure schema.
        
        Args:
            database_url: Database connection URL. If not provided, reads from DATABASE_URL env var.
            
        Returns:
            The initialized connection pool.
            
        Raises:
            ValueError: If DATABASE_URL is not set.
        """
        if cls._pool is not None:
            return cls._pool
        
        url = database_url or os.getenv("DATABASE_URL")
        if not url:
            raise ValueError("DATABASE_URL environment variable is required")
        
        cls._schema = os.getenv("PG_SCHEMA", "public")
        
        logger.info(f"Connecting to PostgreSQL with schema: {cls._schema}")
        
        cls._pool = await asyncpg.create_pool(
            url,
            min_size=2,
            max_size=10,
            command_timeout=60
        )
        
        # Initialize schema on first connection
        async with cls._pool.acquire() as conn:
            await cls._setup_schema(conn)
        
        logger.info("Database connection pool initialized successfully")
        return cls._pool
    
    @classmethod
    async def _setup_schema(cls, conn: asyncpg.Connection):
        """Create schema if not exists and set search_path."""
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{cls._schema}"')
        await conn.execute(f'SET search_path TO "{cls._schema}", public')
        logger.info(f"Schema '{cls._schema}' ready, search_path configured")
    
    @classmethod
    async def get_pool(cls) -> asyncpg.Pool:
        """Get the connection pool, initializing if needed.
        
        Returns:
            The connection pool.
        """
        if cls._pool is None:
            await cls.initialize()
        return cls._pool
    
    @classmethod
    async def acquire(cls) -> asyncpg.Connection:
        """Acquire a connection from the pool with schema set.
        
        Returns:
            A database connection with search_path configured.
        """
        pool = await cls.get_pool()
        conn = await pool.acquire()
        await conn.execute(f'SET search_path TO "{cls._schema}", public')
        return conn
    
    @classmethod
    async def release(cls, conn: asyncpg.Connection):
        """Release a connection back to the pool.
        
        Args:
            conn: The connection to release.
        """
        pool = await cls.get_pool()
        await pool.release(conn)
    
    @classmethod
    async def close(cls):
        """Close the connection pool."""
        if cls._pool is not None:
            await cls._pool.close()
            cls._pool = None
            logger.info("Database connection pool closed")
    
    @classmethod
    def get_schema(cls) -> str:
        return cls._schema
    
    @classmethod
    def is_initialized(cls) -> bool:
        return cls._pool is not None
    
    @classmethod
    async def set_connection_schema(cls, conn: asyncpg.Connection) -> None:
        import re
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', cls._schema):
            raise ValueError(f"Invalid schema name: {cls._schema}")
        await conn.execute(f'SET search_path TO "{cls._schema}", public')
