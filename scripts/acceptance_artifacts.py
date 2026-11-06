"""验收数据契约：保留原始分层与来源，缺字段不等于空结果。

此模块只做适配，不签发事实真值或问题撤销授权。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

ADAPTER_VERSION = "review-report-adapter-v3"


class AdapterError(ValueError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def finding_content(value):
    """内容投影剔除运行生成字段；分析代际仍由 source_binding 单独约束。"""
    if isinstance(value, dict):
        return {k: finding_content(v) for k, v in value.items()
                if k not in {"id", "created_at", "analysis_revision"}}
    if isinstance(value, list):
        return [finding_content(v) for v in value]
    return value


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
            ids = []
            for item in value:
                if any(not str(item.get(k) or "").strip() for k in ("id", "rule_id", "severity")):
                    raise AdapterError("正式问题缺少 id/rule_id/severity")
                if not str(item.get("message") or item.get("title") or "").strip():
                    raise AdapterError("正式问题缺少可核对的内容")
                ids.append(item["id"])
            if len(ids) != len(set(ids)):
                raise AdapterError("正式问题 id 重复，不能覆盖条目")
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
    if not isinstance(meta.get("profile"), dict):
        raise AdapterError(f"{sample_id}: 缺少显式运行配置")
    result_meta = (status.get("result") or {}).get("meta") or {}
    if not result_meta.get("rule_policy_version") or not result_meta.get("rule_registry_version"):
        raise AdapterError(f"{sample_id}: 缺少规则/政策版本")
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
    id_to_fp = {i["id"]: digest(finding_content(i)) for i in layers["formal"]}
    result["findings"] = sorted(id_to_fp.values())
    result["finding_bodies"] = {digest(finding_content(i)): finding_content(i) for i in layers["formal"]}
    replacements = {old: digest(finding_content(body)) for old, body in record.get("finding_bodies", {}).items()}

    def remap(value):
        if isinstance(value, str):
            if value.startswith("id:") and value[3:] in id_to_fp:
                return id_to_fp[value[3:]]
            return replacements.get(value, value)
        if isinstance(value, list):
            return [remap(v) for v in value]
        if isinstance(value, dict):
            return {k: remap(v) for k, v in value.items()}
        return value

    result["todo"] = remap(record.get("todo"))
    if isinstance(result["todo"], dict) and isinstance(result["todo"].get("problems"), list):
        # 旧提取器先按含 revision 的指纹排序；重新映射之后必须按新身份
        # 重排这个无序队列集合，不能把运行生成的排列当成业务变化。
        for problem in result["todo"]["problems"]:
            if isinstance(problem.get("members"), list):
                problem["members"] = sorted(problem["members"])
        result["todo"]["problems"] = sorted(result["todo"]["problems"], key=canonical)
    result["receipts"] = [{**r, **({"issue_refs": sorted(id_to_fp.get(str(ref), f"unmapped:{ref}")
                                                      for ref in r["issue_refs"])} if "issue_refs" in r else {})}
                          for r in status["review_report"]["check_receipts"]]
    return result
