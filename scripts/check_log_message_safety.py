#!/usr/bin/env python
"""静态检查：禁止把可能含材料原文/凭据的整个对象拼进日志 message（Task C）。

为什么需要这个检查
------------------
`src/utils/logging_config.StructuredFormatter` 只对 `extras` 走 `redact_log_fields`
脱敏，`record.getMessage()` 是 Python logging 的固有边界——**不经过任何脱敏**就进入
JSON 的 `message` 字段。所以
`logger.warning(f"转换失败: {e}, issue={issue}")` 会把整条 finding（含 `evidence_text`
即 PDF 原文）落盘。这类调用没法靠"写代码时注意"防住，必须有机器检查。

检查规则（只抓"整个对象"这一类，避免误报淹没信号）
------------------------------------------------
命中即违规：
1. f-string / `%s` / `.format()` / 字符串拼接把**裸变量名**插进日志 message，
   且该变量名在 `RISKY_OBJECT_NAMES` 里（例如 `issue`、`row`、`payload`、`value`）。
2. 插值表达式是敏感属性或敏感下标，例如 `{finding.evidence_text}`、
   `{issue["snippet"]}`——敏感判定复用 `logging_config.is_sensitive_log_key`，
   与运行时脱敏共用同一份口径。

不算违规（有意放过，保证信号密度）：
- `{len(rows)}`、`{issue["rule_id"]}`、`{finding.rule_id}`：只取安全子字段或统计量。
- `extra={...}`：这条路径已由 `redact_log_fields` 兜底脱敏。
- 异常对象 `{e}`：异常消息本身不含原文是各调用点的责任（Task C 约定），
  强行禁掉 `{e}` 会毁掉排障能力。

用法
----
    python scripts/check_log_message_safety.py
    python scripts/check_log_message_safety.py --paths src api --json
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.utils.logging_config import is_sensitive_log_key  # noqa: E402

#: 默认扫描范围。`scripts/` 也扫：导入脚本同样会读到材料数据。
DEFAULT_PATHS: Sequence[str] = ("api", "src", "scripts")

#: 目录级排除（缓存/虚拟环境/前端产物）
_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
    }
)

#: logging 方法名。`log` 需要跳过首个 level 参数。
_LOG_METHOD_NAMES = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
)

#: 被视为 logger 的接收者名字（`logger` / `log` / `LOGGER` / `self.logger` / `logging`）
_LOGGER_RECEIVER_NAMES = frozenset({"logger", "log", "logging", "_logger", "audit_logger"})

#: 裸变量名黑名单：这些名字在本仓库里几乎总是"整条业务对象/整行数据/整段文本"，
#: 直接插进 message 等于把材料原文或凭据写进日志。
RISKY_OBJECT_NAMES = frozenset(
    {
        # 业务对象整体
        "issue",
        "issues",
        "raw_issue",
        "raw_issues",
        "finding",
        "findings",
        "result",
        "results",
        "rule_result",
        "rule_results",
        "record",
        "records",
        "item",
        "items",
        "payload",
        "response",
        "resp",
        "body",
        "data",
        "detail",
        "details",
        # 表格 / 行 / 单元格数据
        "row",
        "rows",
        "cell",
        "cells",
        "table",
        "tables",
        "line",
        "lines",
        # 原始值 / 文本
        "value",
        "values",
        "raw",
        "raw_value",
        "text",
        "content",
        "snippet",
        "evidence",
        "prompt",
        "page_text",
        "page_texts",
        "full_text",
        # 凭据
        "api_key",
        "token",
        "secret",
        "password",
        "cookie",
        "headers",
        "authorization",
    }
)


#: raise 场景用的收窄黑名单。
#: 异常消息里出现 `payload` / `table` / `detail` 这类名字，在本仓库实测多是错误码、
#: 表标识或子进程回传的错误串（`memory_exceeded:MemoryError` 之类），不是材料原文；
#: 把它们一起拦掉只会制造噪声，让门禁失去可信度。这里只留"必定是内容"的名字。
RISKY_RAISE_NAMES = frozenset(
    {
        "issue",
        "issues",
        "raw_issue",
        "raw_issues",
        "finding",
        "findings",
        "result",
        "results",
        "row",
        "rows",
        "value",
        "values",
        "text",
        "content",
        "snippet",
        "evidence",
        "prompt",
        "page_text",
        "page_texts",
        "full_text",
        "api_key",
        "token",
        "secret",
        "password",
        "cookie",
        "authorization",
    }
)


@dataclass(frozen=True)
class Violation:
    """一条违规记录。"""

    path: str
    line: int
    col: int
    expression: str
    reason: str

    def format_text(self) -> str:
        return f"{self.path}:{self.line}:{self.col}: {self.reason}（表达式 `{self.expression}`）"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "line": self.line,
            "col": self.col,
            "expression": self.expression,
            "reason": self.reason,
        }


def _expr_source(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - ast.unparse 在 3.9+ 一直可用
        return node.__class__.__name__


def _receiver_name(node: ast.AST) -> str:
    """取方法调用接收者的末端名字：`logger` / `self.logger` -> `logger`。"""
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    return ""


def _is_logger_call(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _LOG_METHOD_NAMES:
        return False
    return _receiver_name(func.value) in _LOGGER_RECEIVER_NAMES


def _message_args(node: ast.Call) -> List[ast.expr]:
    """返回参与 message 拼装的实参（模板 + `%`-style 的填充参数）。"""
    args = list(node.args)
    if not args:
        return []
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "log":
        # logger.log(level, msg, *args)
        args = args[1:]
    return args


def _check_expression(
    expr: ast.expr,
    risky_names: frozenset = RISKY_OBJECT_NAMES,
) -> Optional[str]:
    """判断单个插值表达式是否违规；返回违规原因，安全则返回 None。"""
    if isinstance(expr, ast.Name):
        if expr.id.lower() in risky_names or is_sensitive_log_key(expr.id):
            return "整个对象被拼进日志 message，可能包含材料原文或凭据"
        return None
    if isinstance(expr, ast.Attribute):
        if is_sensitive_log_key(expr.attr):
            return "敏感属性被拼进日志 message"
        return None
    if isinstance(expr, ast.Subscript):
        key = expr.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            if is_sensitive_log_key(key.value):
                return "敏感字段被拼进日志 message"
        return None
    if isinstance(expr, ast.Call):
        # `traceback.format_exc()` 拼进 message：异常栈里会带第三方异常的消息，
        # 而 pydantic 之类的校验异常会把**输入值**（可能是 PDF 正文片段）写进消息。
        # 正确做法是 `logger.exception(...)`，异常栈进独立的 exception 字段，
        # message 保持干净。
        func = expr.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == "format_exc":
            return "完整异常栈被拼进日志 message，请改用 logger.exception"
        return None
    return None


def _iter_interpolated(node: ast.expr) -> Iterable[ast.expr]:
    """列出一个 message 实参里"被插值出去"的子表达式。"""
    if isinstance(node, ast.JoinedStr):
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                yield value.value
        return
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        # "...%s" % obj  /  "..." + str(obj)
        for side in (node.left, node.right):
            if isinstance(side, ast.Constant):
                continue
            if isinstance(side, ast.Tuple):
                yield from side.elts
            elif isinstance(side, ast.Call) and _is_str_cast(side):
                yield from side.args
            else:
                yield side
        return
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "format":
            yield from node.args
            for keyword in node.keywords:
                yield keyword.value
            return
        if _is_str_cast(node):
            yield from node.args
            return
        # 其它函数调用：整体交给 `_check_expression` 判定（例如 traceback.format_exc()）
        yield node
        return
    if isinstance(node, ast.Constant):
        return
    # 其它形态（裸变量作为 `%s` 填充参数等）直接按整体判定
    yield node


def _is_str_cast(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Name) and node.func.id in {"str", "repr"}


class _LogCallVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.violations: List[Violation] = []

    def _record(self, node: ast.AST, expr: ast.expr, reason: str) -> None:
        self.violations.append(
            Violation(
                path=self.path,
                line=getattr(expr, "lineno", getattr(node, "lineno", 0)),
                col=getattr(expr, "col_offset", getattr(node, "col_offset", 0)),
                expression=_expr_source(expr),
                reason=reason,
            )
        )

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 (ast 约定)
        if _is_logger_call(node):
            for arg in _message_args(node):
                for expr in _iter_interpolated(arg):
                    reason = _check_expression(expr)
                    if reason:
                        self._record(node, expr, reason)
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:  # noqa: N802 (ast 约定)
        """异常消息同样要查。

        Task C 的约定是：`logger.exception` / `logger.error("...%s", e)` 保留（异常栈对
        排障必要），但代价是**抛出的异常消息本身不能含原文**——否则原文会顺着 `{e}`
        进入 message 字段。所以在源头拦住
        `raise Exception(f"返回格式错误: {result}")` 这类写法。
        """
        exc = node.exc
        if isinstance(exc, ast.Call):
            candidates = list(exc.args) + [keyword.value for keyword in exc.keywords]
            for arg in candidates:
                for expr in _iter_interpolated(arg):
                    reason = _check_expression(expr, RISKY_RAISE_NAMES)
                    if reason:
                        self._record(node, expr, f"{reason}；该异常消息会随 logger 落盘")
        self.generic_visit(node)


def check_source(source: str, path: str = "<memory>") -> List[Violation]:
    """检查一段源码，返回违规列表（供测试直接调用）。"""
    tree = ast.parse(source)
    visitor = _LogCallVisitor(path)
    visitor.visit(tree)
    return visitor.violations


def iter_python_files(roots: Sequence[Path]) -> List[Path]:
    files: List[Path] = []
    seen: Set[Path] = set()
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            candidates = [root]
        elif root.is_dir():
            candidates = sorted(root.rglob("*.py"))
        else:
            continue
        for candidate in candidates:
            if any(part in _EXCLUDED_DIR_NAMES for part in candidate.parts):
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(candidate)
    return files


def check_paths(paths: Sequence[str], *, base: Optional[Path] = None) -> List[Violation]:
    base_dir = (base or _REPO_ROOT).resolve()
    roots = [(base_dir / item) if not Path(item).is_absolute() else Path(item) for item in paths]
    violations: List[Violation] = []
    for file_path in iter_python_files(roots):
        try:
            source = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            rel = file_path.resolve().relative_to(base_dir).as_posix()
        except ValueError:
            rel = file_path.as_posix()
        violations.extend(check_source(source, rel))
    return sorted(violations, key=lambda item: (item.path, item.line, item.col))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检查日志 message 是否拼入可能含材料原文/凭据的整个对象（Task C）",
    )
    parser.add_argument(
        "--paths",
        nargs="*",
        default=list(DEFAULT_PATHS),
        help=f"扫描路径，默认 {' '.join(DEFAULT_PATHS)}",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    violations = check_paths(args.paths)
    if args.json:
        print(
            json.dumps(
                {
                    "violation_count": len(violations),
                    "violations": [item.to_dict() for item in violations],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for violation in violations:
            print(violation.format_text())
        if violations:
            print(f"\n发现 {len(violations)} 处日志 message 泄漏风险调用。")
            print("整改方式：只记 ID / 数量 / 长度 / 哈希 / 错误码，原文改走 extra= 并由脱敏兜底。")
        else:
            print("日志 message 安全检查通过：未发现把整个业务对象拼进 message 的调用。")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
