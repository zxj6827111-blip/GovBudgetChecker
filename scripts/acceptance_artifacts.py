"""验收数据契约：保留原始分层与来源，缺字段不等于空结果。

此模块只做适配，不签发事实真值或问题撤销授权。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

ADAPTER_VERSION = "review-report-adapter-v2"


class AdapterError(ValueError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def formal_layers(status: dict) -> dict:
    report = status.get("review_report")
    if not isinstance(report, dict):
        raise AdapterError("缺少 review_report；不得回退到空 rule_findings")
    layers = {}
    for name, field in (("formal", "issues"), ("candidate", "candidate_findings"),
                        ("tips", "tips"), ("manual_review", "manual_review")):
        # 当前报告 schema 的四层一律显式存在。旧格式应另行声明契约，不静默兼容。
        value = report.get(field)
        if not isinstance(value, list) or any(not isinstance(v, dict) for v in value):
            raise AdapterError(f"review_report.{field} 缺失或不是对象列表")
        if name == "formal":
            for item in value:
                if any(not str(item.get(k) or "").strip() for k in ("id", "rule_id", "severity")):
                    raise AdapterError("正式问题缺少 id/rule_id/severity")
        layers[name] = value
    return layers


def provenance(status: dict, meta: dict, sample_id: str, pdf_sha: str) -> dict:
    """分析身份由原始 status 和采集元数据绑定；不从回执推导。"""
    checksum = status.get("checksum")
    if checksum != pdf_sha or not pdf_sha or len(pdf_sha) != 64:
        raise AdapterError(f"{sample_id}: 原始 status 与材料 SHA 不一致")
    revision = status.get("analysis_revision")
    code = (meta.get("code") or {}).get("digest")
    if not revision or not code or meta.get("code_after") != code:
        raise AdapterError(f"{sample_id}: 缺少分析代际/代码身份，或采集期间代码漂移")
    result_meta = (status.get("result") or {}).get("meta") or {}
    return {"sample_id": sample_id, "pdf_sha256": pdf_sha, "analysis_revision": revision,
            "code_digest": code, "profile_digest": digest(meta.get("profile")),
            "rules_digest": digest({k: result_meta.get(k) for k in
                                    ("rule_policy_version", "rule_registry_version", "versions")}),
            "organization": status.get("organization_name"), "year": status.get("fiscal_year"),
            "report_kind": status.get("report_kind")}


def adapt_record(record: dict, status: dict, meta: dict) -> dict:
    """产生新记录，不修改历史工件；保留可独立核对的原始问题层。"""
    layers = formal_layers(status)
    result = dict(record)
    result["adapter_version"] = ADAPTER_VERSION
    result["source_binding"] = provenance(status, meta, record["sample_id"], record["sha256"])
    result["finding_layers"] = layers
    result["raw_issue_layers"] = (status.get("result") or {}).get("issues")
    result["raw_status_digest"] = digest(status)
    # 正式问题须取报告最终层，候选/提示不混入。
    result["findings"] = sorted(digest(i) for i in layers["formal"])
    result["finding_bodies"] = {digest(i): i for i in layers["formal"]}
    return result
