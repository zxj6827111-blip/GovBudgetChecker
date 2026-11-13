"""在 JSONB 中保存规范 JSON 原文，避免数据库数字正规化改变证据摘要。"""

import hashlib
import json
import base64
import zlib

STORAGE_VERSION = "gbc.lossless-json.v1"


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def pack_jsonb(value, *, query_fields=()):
    text = canonical_json(value)
    raw = text.encode()
    return {"storage_format": STORAGE_VERSION, "encoding": "zlib+base64",
            "payload": base64.b64encode(zlib.compress(raw)).decode("ascii"),
            "uncompressed_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            **{key: value[key] for key in query_fields if key in value}}


def unpack_jsonb(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("lossless JSON requires an object")
    if "storage_format" not in value:
        return value  # 已有历史对象只读，不在读取时自动改写。
    if value.get("storage_format") != STORAGE_VERSION:
        raise ValueError("unsupported lossless JSON storage format")
    if value.get("encoding") != "zlib+base64":
        raise ValueError("unsupported lossless JSON encoding")
    size = value.get("uncompressed_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size < 2:
        raise ValueError("invalid lossless JSON size")
    try:
        compressed = base64.b64decode(value["payload"], validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, size + 1)
        if len(raw) != size or not decoder.eof or decoder.unused_data:
            raise ValueError("lossless JSON decompression size mismatch")
        text = raw.decode("utf-8")
    except (KeyError, TypeError, zlib.error, UnicodeError) as exc:
        raise ValueError("invalid lossless JSON compressed payload") from exc
    if not isinstance(text, str) or hashlib.sha256(text.encode()).hexdigest() != value.get("sha256"):
        raise ValueError("lossless JSON SHA-256 mismatch")
    payload = json.loads(text)
    if not isinstance(payload, dict) or canonical_json(payload) != text:
        raise ValueError("lossless JSON canonical payload mismatch")
    for key in set(value) - {"storage_format", "encoding", "payload", "uncompressed_bytes", "sha256"}:
        if value[key] != payload.get(key):
            raise ValueError("lossless JSON query projection mismatch")
    return payload
