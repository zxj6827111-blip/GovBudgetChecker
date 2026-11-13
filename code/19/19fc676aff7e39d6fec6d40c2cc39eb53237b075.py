"""Wait for host-port SQL readiness before using the disposable validation stack.
Docker pg_isready can succeed against the image's temporary initialization server.
The shared harness and production services remain unchanged.
"""
import asyncio,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import asyncpg
from scripts import run_isolated_service_validation as validation
prepare=validation.prepare_db
async def ready_then_prepare(dsn):
    deadline=time.monotonic()+60
    while True:
        conn=None
        try:
            conn=await asyncpg.connect(dsn,timeout=3)
            await conn.fetchval('SELECT 1')
            break
        except (ConnectionError,OSError,asyncpg.PostgresError):
            if time.monotonic()>=deadline:raise RuntimeError('Disposable PostgreSQL host-port readiness timed out') from None
            await asyncio.sleep(.5)
        finally:
            if conn is not None:await conn.close()
    await prepare(dsn)
validation.prepare_db=ready_then_prepare
sys.exit(validation.main())
