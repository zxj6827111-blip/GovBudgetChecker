"""执行显式确认的单项组织归属修复，或按恢复记录回滚；不猜测映射。"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import dotenv_values
import asyncpg
from src.services.material_identity_repair import repair_material_identity, rollback_material_identity
from src.services.audit_log import append_audit_event
from src.services.org_storage import _catalog_lock


async def main(args):
    root=Path(__file__).resolve().parents[1]
    cfg=dotenv_values(root/".env")
    conn=await asyncpg.connect(cfg["DATABASE_URL"])
    try:
        # 归属修复期间保持组织目录与任务关联文件不被其他进程覆盖。
        with _catalog_lock():
            if args.rollback:
                receipt=await rollback_material_identity(conn,args.rollback)
            else:
                approval=json.loads(args.approval.read_text())
                if approval.get("confirmed") is not True:raise ValueError("缺少具体映射的确认记录")
                catalog=json.loads((root/"data/organizations.json").read_text(encoding="utf-8-sig"))["organizations"]
                receipt=await repair_material_identity(conn,old_id=approval["old_id"],new_id=approval["new_id"],
                    slot_ids=approval["slot_ids"],catalog=catalog,upload_root=root/"uploads",
                    links_file=root/"data/job_org_links.json",receipt_dir=args.receipt_dir,actor=approval["actor"])
        append_audit_event(action="material.identity.rollback" if args.rollback else "material.identity.repair",
            actor=receipt["actor"],result="success",resource_type="material_slot",
            resource_id=",".join(receipt["slot_ids"]),details={"old_id":receipt["old_id"],"new_id":receipt["new_id"],"state":receipt["state"]})
        print(json.dumps({"state":receipt["state"],"slot_ids":receipt["slot_ids"],"frozen_records_preserved":receipt["frozen_before"]==receipt["frozen_after"]},ensure_ascii=False))
    finally:await conn.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--approval",type=Path)
    group.add_argument("--rollback",type=Path)
    parser.add_argument("--receipt-dir",type=Path)
    args=parser.parse_args()
    if args.approval and not args.receipt_dir:parser.error("--approval requires --receipt-dir")
    asyncio.run(main(args))
