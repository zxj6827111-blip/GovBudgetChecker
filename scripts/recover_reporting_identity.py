"""按单项恢复凭证恢复报送归属；默认只查看凭证，--apply 才实际恢复。"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.db.connection import DatabaseConnection
from src.services.reporting_identity import rollback_ancestors
from src.services.material_identity_repair import rollback_material_identity
from src.services.audit_log import append_audit_event

async def main(args):
    load_dotenv(ROOT/'.env')
    path=args.receipt.resolve()
    receipt=json.loads(path.read_text(encoding='utf-8'))
    if not args.apply:
        print(json.dumps({'state':receipt.get('state'),'kind':receipt.get('kind','historical_id'),'action':'预览；确认恢复时增加 --apply --actor 操作人'},ensure_ascii=False))
        return
    if not args.actor or not args.actor.strip():
        raise ValueError('恢复必须记录实际操作人 --actor')
    await DatabaseConnection.initialize()
    conn=await DatabaseConnection.acquire()
    try:
        if receipt.get('kind')=='ancestors':
            await rollback_ancestors(conn,path)
        else:
            from src.services.org_storage import _catalog_lock
            with _catalog_lock():
                await rollback_material_identity(conn,path.parent)
        append_audit_event(action='reporting_task.identity_recovery',actor=args.actor,result='success',resource_type='identity_repair',resource_id=path.parent.name)
        print(json.dumps({'state':'restored','receipt_id':path.parent.name},ensure_ascii=False))
    finally:
        await DatabaseConnection.release(conn)
        await DatabaseConnection.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt',required=True,type=Path)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--actor')
    asyncio.run(main(parser.parse_args()))
