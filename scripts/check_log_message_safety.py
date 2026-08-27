#!/usr/bin/env python
"""静态检查：禁止把可能含材料原文/凭据的整个对象拼进日志 message（Task C）。

为什么需要这个检查
------------------
`src/utils/logging_config.StructuredFormatter` 只对 `extras` 走 `redact_log_fields`
脱敏，`record.getMessage()` 是 Python logging 的固有边界——**不经过任何脱敏**就进入
JSON 的 `message` 字段。所以
`logger.warning(f"转换失败: {e}, issue={issue}")` 会把整条 finding（含 `evidence_text`
即 PDF 原文）落盘。这类调用没法靠"写代码时注意"防住，必须有机器检查。

为什么是 fail-closed（白名单）而不是黑名单
--------------------------------------
本检查最初用"危险名字黑名单"实现，并据此宣布全仓 0 违规。独立复核推翻了这个结论：
`src/engine/ai/extractor_client.py` 有 4 处 `logger.warning(f"...: {hit}")`，而 `hit`
的必需字段就包含 `budget_text` / `final_text` / `stmt_text`（送检材料原文）——只因为
`hit` 这个名字不在黑名单里就被放过了。黑名单对"没被想到的名字"天然漏报，而漏报的
代价是原文落盘，所以这里改成 fail-closed：

    裸变量插进 message ⇒ 默认违规，除非该名字被判定安全。

"判定安全"有三条来源，按顺序：
1. `SAFE_LOG_NAMES`：逐个登记过的名字，每条都带判断理由（新增一个名字 = 一行可
   审查的 diff，强迫作者当场说明这个变量为什么不含原文）。
2. `_SAFE_NAME_SUFFIXES` / `_SAFE_NAME_PREFIXES`：机械可判的标量语义（`*_id`、
   `*_count`、`*_ms`、`is_*` …），覆盖绝大多数排障字段，避免白名单变成几百行。
3. 全大写常量名（`MAX_UPLOAD_MB`）：模块级配置阈值。

两条**不可被白名单覆盖**的红线（先判，且优先级最高）：
- `logging_config.is_sensitive_log_key` 命中（`*_text` / `*_token` / `snippet` …），
  与运行时 `redact_log_fields` 共用同一份口径；
- `RISKY_OBJECT_NAMES`：已知会承载整条业务对象/整行数据的名字。

检查规则
--------
命中即违规：
1. f-string / `%s` / `.format()` / 字符串拼接把**裸变量名**插进日志 message，
   且该名字未被判定安全（fail-closed，见上）。
2. 插值表达式是敏感属性或敏感下标，例如 `{finding.evidence_text}`、
   `{issue["snippet"]}`。
3. `traceback.format_exc()` 拼进 message（应改用 `logger.exception`）。
4. `raise` 的异常消息拼入 `RISKY_RAISE_NAMES` 里的名字——异常消息会顺着上游的
   `{e}` 进入 message。

不算违规（有意放过，保证信号密度）：
- `{len(rows)}`、`{issue["rule_id"]}`、`{finding.rule_id}`：只取安全子字段或统计量。
- `extra={...}`：这条路径已由 `redact_log_fields` 兜底脱敏。
- 异常对象 `{e}`：异常消息本身不含原文是各调用点的责任（由规则 4 在源头把守），
  强行禁掉 `{e}` 会毁掉排障能力。

已知局限（写在这里，不要再当成"全覆盖"）
------------------------------------
- **`raise` 路径仍是黑名单**，不是 fail-closed。异常消息里的裸变量在本仓库实测
  几乎全是配置值与 SQL 标识符（`model`、`timeout`、`table`、`col`、`migration_id`），
  一律 fail-closed 只会产出几十条噪声，且原文要落盘必须先经过某个 logger——那一侧
  已经 fail-closed。代价是：若将来有人 `raise ValueError(f"bad {new_obj}")` 且
  `new_obj` 不在 `RISKY_RAISE_NAMES` 里，这里不会报，需要靠 code review。
- 只看**语法形态**，不做取值分析：`logger.info(f"{obj.field}")` 里 `field` 若不在敏感
  键口径内就会放过；`safe = row["snippet"]; logger.info(f"{safe}")` 这种先赋值再打印
  也绕得过去（`safe` 需要被登记，但登记时看不出它的来源）。
- 覆盖范围是 `api` / `src` / `scripts` 的 Python 代码，不含前端与 SQL。

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
        # AI 抽取器返回的命中对象。required_fields 明确含 budget_text / final_text /
        # stmt_text（送检材料原文），见 src/engine/ai/extractor_client.py。
        # 独立复核就是在这里抓到 4 处真实泄漏的。
        "hit",
        "hits",
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


#: 逐个登记过的安全名字。**新增条目必须在注释里说明为什么它不含材料原文/凭据。**
#: 这份名单是 fail-closed 的另一半：不在这里、也不匹配下面的机械模式，就报违规。
SAFE_LOG_NAMES = frozenset(
    {
        # --- 异常对象本身。异常消息不含原文由 visit_Raise 在源头把守 ---
        "e",
        "err",
        "error",
        "exc",
        "exception",
        "last_exception",
        "error_msg",
        "msg",
        # --- 标识符 / 路径。路径含文件名，但文件名不是材料正文 ---
        "path",
        "filename",
        "archive",
        "identifier",
        "col",  # SQL 列名（src/db/safe_ops.py 的白名单校验）
        "schema",  # PG schema 名
        "scope_key",  # 报告身份键：checksum 派生，不可反推原文
        "username",  # 认证日志需要定位到人；非材料原文
        # --- 枚举 / 状态 / 配置取值 ---
        "code",
        "reason",
        "stage",
        "status",
        "role",
        "name",
        "default",
        "model",  # AI 模型名
        "provider",
        "preferred_provider",
        "executor",  # 执行器类型标记
        "resumed",
        "api_key_env",  # 环境变量**名**（如 ARK_API_KEY），不是 key 本身
        # --- 计数 / 耗时 / 规模。只有量，没有内容 ---
        "attempt",
        "delay",
        "elapsed",
        "errors",
        "remaining",
        "returncode",
        "timeout",
        "totals",  # 纯计数字典，见 src/services/merge_findings.py
        "row_order",  # 行序号
        # --- 具名条目：宁可登记长名字，也不放过泛名 ---
        # `description` 泛名在别处可能承载 finding 描述（含材料金额），不进白名单；
        # 这一条只放过迁移定义里写死的静态说明文字。
        "migration_description",
    }
)

#: 机械可判的安全后缀：这些后缀在本仓库里稳定表示标量（ID / 计数 / 耗时 / 枚举）。
#: 注意不要加 `_key`——`secret_key` 会被顺带放过；也不要加 `_span`——AI 返回的 span
#: 形状不可信，正是靠它落到 fail-closed 分支才发现问题的。
_SAFE_NAME_SUFFIXES = (
    "_id",
    "_ids",
    "_uuid",
    "_idx",
    "_index",
    "_count",
    "_counts",
    "_total",
    "_totals",
    "_sum",
    "_ms",
    "_sec",
    "_secs",
    "_seconds",
    "_size",
    "_bytes",
    "_len",
    "_length",
    "_ratio",
    "_pct",
    "_percent",
    "_score",
    "_number",
    "_order",
    "_year",
    "_version",
    "_revision",
    "_path",
    "_dir",
    "_file",
    "_filename",
    "_code",
    "_status",
    "_stage",
    "_state",
    "_role",
    "_mode",
    "_kind",
    "_type",
    "_name",
    # 字段名清单（"缺了哪些字段"这类信息只含字段名，不含取值）。
    # 刻意不加 `_keys`：`api_keys` 会被顺带放过，那是凭据。
    "_field",
    "_fields",
    "_enabled",
    "_flag",
    "_strategy",
    "_error",
    "_err",
    "_exc",
    "_exception",
)

#: 机械可判的安全前缀（布尔量、上下界、计数）。
_SAFE_NAME_PREFIXES = (
    "is_",
    "has_",
    "can_",
    "use_",
    "allow_",
    "enable",
    "max_",
    "min_",
    "num_",
    "n_",
    "total_",
    "count_",
    "elapsed_",
    "duration_",
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
        "hit",
        "hits",
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


def classify_bare_name(name: str, *, fail_closed: bool) -> Optional[str]:
    """判断"裸变量名被整体插进 message"是否违规；返回原因，安全则返回 None。

    `fail_closed=True`（logger message 路径）时，未登记的名字一律违规——黑名单漏掉
    `hit` 的教训就在这里兜住。`fail_closed=False`（raise 路径）时只查黑名单。
    """
    lowered = name.lower()

    # 两条红线，白名单不可覆盖
    if is_sensitive_log_key(lowered):
        return "敏感字段名被整体拼进日志 message，可能是材料原文或凭据"
    deny = RISKY_OBJECT_NAMES if fail_closed else RISKY_RAISE_NAMES
    if lowered in deny:
        return "整个对象被拼进日志 message，可能包含材料原文或凭据"

    if not fail_closed:
        return None

    if lowered in SAFE_LOG_NAMES:
        return None
    if lowered.endswith(_SAFE_NAME_SUFFIXES) or lowered.startswith(_SAFE_NAME_PREFIXES):
        return None
    # 全大写（含前导下划线）视为模块级配置常量：MAX_UPLOAD_MB / _WORKFLOW_DB_TIMEOUT_SECONDS
    stripped = name.lstrip("_")
    if stripped and stripped.isupper():
        return None
    if name.startswith("__") and name.endswith("__"):
        return None  # __name__ 之类的 dunder

    return (
        "未登记的变量被整体拼进日志 message（fail-closed）："
        "请改为只记 ID / 数量 / 长度 / 哈希 / 错误码，"
        "或在 SAFE_LOG_NAMES 登记该名字并注明它为何不含材料原文"
    )


def _check_expression(
    expr: ast.expr,
    *,
    fail_closed: bool = True,
) -> Optional[str]:
    """判断单个插值表达式是否违规；返回违规原因，安全则返回 None。"""
    if isinstance(expr, ast.Name):
        return classify_bare_name(expr.id, fail_closed=fail_closed)
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
                    reason = _check_expression(expr, fail_closed=True)
                    if reason:
                        self._record(node, expr, reason)
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:  # noqa: N802 (ast 约定)
        """异常消息同样要查。

        Task C 的约定是：`logger.exception` / `logger.error("...%s", e)` 保留（异常栈对
        排障必要），但代价是**抛出的异常消息本身不能含原文**——否则原文会顺着 `{e}`
        进入 message 字段。所以在源头拦住
        `raise Exception(f"返回格式错误: {result}")` 这类写法。

        这一侧刻意**不**用 fail-closed：实测异常消息里的裸变量几乎全是配置值与 SQL
        标识符，全量拦截只出噪声；且原文要落盘必须先经过某个 logger，而那一侧已经
        fail-closed。局限已写进模块 docstring。
        """
        exc = node.exc
        if isinstance(exc, ast.Call):
            candidates = list(exc.args) + [keyword.value for keyword in exc.keywords]
            for arg in candidates:
                for expr in _iter_interpolated(arg):
                    reason = _check_expression(expr, fail_closed=False)
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
