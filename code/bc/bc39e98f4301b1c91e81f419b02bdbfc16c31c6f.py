from pathlib import Path
import csv
import hashlib
import json
import re

ROOT = Path.cwd()
OUT = ROOT / "outputs/remediation_20261006_round11"


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


receipt = json.loads((OUT / "live_verified/service_receipt.json").read_text())
assert receipt["status"] == "passed" and not receipt["errors"]
source = json.loads((OUT / "source_snapshot.json").read_text())
assert all(sha(ROOT / row["path"]) == row["sha256"] for row in source["files"])
assert all(sha(ROOT / name) == digest for name, digest in receipt["code_fingerprints"].items())
replay = json.loads((OUT / "rules_replay.json").read_text())
assert all(row["expected"] == row["actual"] for row in replay["neighbors"])
test_log = (OUT / "full_pytest_final.log").read_text()
passed, skipped = map(int, re.search(r"(\d+) passed, (\d+) skipped", test_log).groups())
preflight = json.loads((OUT / "zc07_handoff_preflight.json").read_text())

items = [
    ("R10-01", "年度与未知主体误绑", "implemented_development_verified", "年度前/后置、未知主体、本单位真错误与他单位同句；原文锚点保持", "独立复核生产规则、回执及引文", ["src/engine/rules_v33.py", "tests/remediation/test_round11_contracts.py"]),
    ("R10-02", "跨分句往年口径丢失", "implemented_development_verified", "金额/比例共用主体年度阶段作用域；同年历史事实独立关系；本年重置", "独立复核历史无事实/有事实正反例与容差", ["src/engine/rules_v33.py", "tests/remediation/test_round11_contracts.py"]),
    ("R10-03", "配额与评测真值组契约不一致", "implemented_development_verified", "同一 canonical_truth_group；任意英文尾缀不剥离；窄旧格式兼容", "复核冻结分组未迎合系统输出调整", ["src/services/truth_grouping.py", "scripts/run_zc07_acceptance.py", "scripts/evaluate_golden_corpus.py"]),
    ("R10-04", "精确定位分母表述过宽", "implemented_development_verified", "defect 区域分母与缺陷组分母明确；其他标签单独诊断", "复核说明/实现/测试一致", ["docs/ZC07_真实验收数据格式.md", "docs/冻结盲测材料要求_20261005.md"]),
    ("R10-05", "可信OCR", "partially_implemented_not_closed", "逐格协议/真实原始分数/格框/ROI/资格/来源入库/外部认证；真实模型合成阳性与政府页安全阴性；OCR数值校正服务", "真实原始高质量扫描阳性、独立人工认证、阈值校准、扫描风险覆盖与业务门禁；续表自动合并未核销", ["ocr_service/app.py", "ocr_service/protocol.py", "src/services/ocr_qualification.py", "src/services/scan_evidence_certification.py", "outputs/remediation_20261006_round11/live_verified/service_receipt.json"]),
    ("R10-06", "独立冻结材料与正式四场景", "awaiting_independent_materials_not_closed", "5个明确身份开发服务场景通过，实际Worker/PG/OCR及7套四格式；冻结/认证模板与复跑脚本", "开发32/独立盲测32，8层各≥4，盲测≥100真实人审缺陷组；近重复/分歧/适用分母/区域真值/解封记录；正式四场景及两次同版回放", ["docs/冻结盲测材料要求_20261005.md", "docs/acceptance_templates", "scripts/run_isolated_service_validation.py"]),
]
rows = [dict(zip(("item_id", "title", "status", "implemented", "remaining", "evidence_paths"), item)) for item in items]
ledger = {
    "scope": "development_remediation_for_independent_review",
    "human_business_acceptance_performed": False,
    "business_acceptance": "not_passed",
    "metrics": {name: None for name in ("precision", "recall", "major_misses", "exact_location", "evidence_authenticity", "required_check_completion", "formal_repeatability")},
    "items": rows,
    "engineering": {"pytest_passed": passed, "pytest_skipped": skipped, "pytest_failed": 0, "round10_neighbors": len(replay["neighbors"]), "service_run_id": receipt["run_id"], "development_scenarios": len(receipt["scenarios"]), "service_status": receipt["status"], "exports_original_versions": 5, "exports_corrected_versions": 2},
    "known_limits": ["人工位置覆盖未应用，未宣称支持改框或身份字段校正", "派生混合OCR数值已改但关系状态未变化；明确算式阳性变化来自合成控制", "真实原始扫描阳性/独立人审认证未提供", "开发材料不得变成独立盲测", "最终OCR镜像为验证组合镜像，当前Dockerfile未重新从零构建", "重复下载与历史字节稳定不等于独立同版重复业务运行"],
    "source_snapshot_path": "outputs/remediation_20261006_round11/source_snapshot.json",
    "source_snapshot_sha256": sha(OUT / "source_snapshot.json"),
    "preflight_path": "outputs/remediation_20261006_round11/zc07_handoff_preflight.json",
    "preflight_business_acceptance": preflight["business_acceptance"],
}
(OUT / "remediation_ledger.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=2)+"\n")
with (OUT / "remediation_ledger.csv").open("w", encoding="utf-8-sig", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows({**row, "evidence_paths": " | ".join(row["evidence_paths"])} for row in rows)

integrity = []
for scene in receipt["scenarios"]:
    directory = OUT / "live_verified" / scene["scenario"]
    assert sha(directory / "source.pdf") == scene["source_sha256"]
    assert all(sha(directory / name) == digest for name, digest in scene["exports"].items())
    if "correction" in scene:
        assert all(sha(directory / "corrected" / name) == digest for name, digest in scene["correction"]["exports"].items())
        assert all(scene["correction"]["history_identical"].values())
    for phase in [directory, directory / "corrected"]:
        path = phase / "ocr_evidence/manifest.json"
        if path.is_file():
            proof = json.loads(path.read_text())
            assert proof["status"] == "verified_request_hashes" and not proof["errors"]
            for record in proof["records"]:
                for key in ("page_input", "page_response", "region_input", "region_response"):
                    if key in record:
                        asset = record[key]
                        assert sha(path.parent / asset["path"]) == asset["sha256"]
    integrity.append({"scenario": scene["scenario"], "source_exports_provenance_hashes": "matched", "terminal_status": scene["terminal_status"], "correction_rule_status_changed": scene.get("correction",{}).get("rule_status_changed")})
(OUT / "handoff_integrity.json").write_text(json.dumps({"status": "matched", "source_files": len(source["files"]), "runtime_code_fingerprints": "matched", "scenarios": integrity}, ensure_ascii=False, indent=2)+"\n")

manifest = {"scope": "engineering_evidence_not_formal_acceptance", "files": []}
for path in sorted(OUT.rglob("*")):
    if path.is_file() and path.name != "artifact_manifest.json":
        manifest["files"].append({"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "sha256": sha(path)})
(OUT / "artifact_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
print(json.dumps({"integrity": "matched", "artifacts": len(manifest["files"]), "pytest": [passed, skipped, 0], "business_acceptance": "not_passed", "artifact_manifest_sha256": sha(OUT / "artifact_manifest.json")}, ensure_ascii=False))
