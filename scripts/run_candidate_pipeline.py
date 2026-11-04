# -*- coding: utf-8 -*-
"""对单个候选 PDF 运行固定快照 48ee4e28 的管线，保存 status/todo。

只作为候选筛选线索；金标结论仍须回原文核验。
"""
import argparse, gzip, json, os, re, shutil, sys, time
from pathlib import Path

SRC = Path("/Volumes/workSSD/software development/GovBudgetChecker")
sys.path.insert(0, str(SRC / "scripts"))
import gold_baseline_run as gbr  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-id", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--sha256", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--code-root", required=True)
    ap.add_argument("--kit", required=True)
    ap.add_argument("--python", required=True)
    args = ap.parse_args()
    sha = (args.sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise SystemExit("sha256 必须为 64 位十六进制（已 strip 尾随空白/CR）")
    args.sha256 = sha
    out = Path(args.out).resolve()
    code_root = Path(args.code_root).resolve()
    kit = Path(args.kit).resolve()
    profile = gbr.read_json(kit / "runtime_profile.json")["env"]
    base = out / "work" / args.sample_id
    org = base / "org"; org.mkdir(parents=True)
    for name in ("organizations.json", "job_org_links.json"):
        shutil.copy(kit / "org" / name, org / name)
    uploads = base / "uploads"; job = uploads / args.sample_id; job.mkdir(parents=True)
    pdf = Path(args.pdf).resolve()
    shutil.copy(pdf, job / pdf.name)
    (base / "home").mkdir(); (base / "tmp").mkdir()
    env = gbr.worker_env(profile, home=base / "home", tmp=base / "tmp", org_dir=org, upload_dir=uploads)
    # 1) upload inputs
    res = gbr.run_worker({"kind": "upload_inputs", "pdf": str(job / pdf.name), "job_dir": str(job),
                          "dotenv_names": [], "pinned_names": sorted(profile)},
                         code_root=code_root, python=args.python, env=env, work=base / "derive",
                         timeout=600, log_path=base / "derive.log")
    if not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False)[:1000]); sys.exit(2)
    status = dict(res["inputs"])
    status.update(job_id=args.sample_id, status="queued", checksum=args.sha256,
                  use_local_rules=True, use_ai_assist=False, mode="legacy")
    gbr.write_json(job / "status.json", status)
    # 2) pipeline
    t0 = time.time()
    res2 = gbr.run_worker({"kind": "pipeline", "job_dir": str(job), "dotenv_names": [],
                           "pinned_names": sorted(profile)},
                          code_root=code_root, python=args.python, env=env, work=base,
                          timeout=1200, log_path=out / "logs" / f"{args.sample_id}.log")
    final = gbr.read_json(job / "status.json")
    (out / "status").mkdir(parents=True, exist_ok=True)
    with gzip.open(out / "status" / f"{args.sample_id}.status.json.gz", "wt", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, sort_keys=True)
    if res2.get("ok"):
        (out / "todo").mkdir(parents=True, exist_ok=True)
        with gzip.open(out / "todo" / f"{args.sample_id}.todo.json.gz", "wt", encoding="utf-8") as f:
            json.dump(res2["todo"], f, ensure_ascii=False, sort_keys=True)
    print(json.dumps({"sample_id": args.sample_id, "ok": res2.get("ok"), "elapsed_s": round(time.time()-t0, 1),
                      "status": final.get("status"), "error": res2.get("error"),
                      "issues": len((final.get("review_report") or {}).get("issues") or [])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
