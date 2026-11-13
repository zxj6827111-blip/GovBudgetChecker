"""一次性脚本：把 ruff E702（分号连接多条语句）拆成独立物理行。

按整文件 tokenize 定位分号，用所在逻辑行的起始缩进重排剩余部分。
拆完的 ast.dump（去掉行号属性）必须与原文件逐字相同，否则不改该文件。
"""

from __future__ import annotations

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

COMPOUND_KEYWORDS = {
    "if", "elif", "else", "for", "while", "try", "except", "finally",
    "with", "def", "class", "async", "match", "case",
}


def ruff_e702_files() -> list[str]:
    out = subprocess.run(
        [sys.executable, "-m", "ruff", "check", ".", "--output-format=concise", "--select", "E702"],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout
    files: list[str] = []
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 4 and parts[1].strip().isdigit() and parts[0] not in files:
            files.append(parts[0])
    return files


def rewrite(text: str) -> str | None:
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError):
        return None

    semis = [tok for tok in toks if tok.type == tokenize.OP and tok.string == ";"]
    if not semis:
        return None

    rows = text.splitlines()
    indent_of_row: dict[int, str] = {}
    first_token_of_logical_line: dict[int, str] = {}
    current_indent = ""
    after_newline = True
    for tok in toks:
        if tok.type in (tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER):
            continue
        if tok.type == tokenize.NEWLINE:
            after_newline = True
            continue
        if not tok.string:
            continue
        if after_newline:
            head = rows[tok.start[0] - 1] if tok.start[0] - 1 < len(rows) else ""
            current_indent = head[: len(head) - len(head.lstrip())]
            first_token_of_logical_line[tok.start[0]] = tok.string
            after_newline = False
        indent_of_row[tok.start[0]] = current_indent

    by_row: dict[int, list[int]] = {}
    for tok in semis:
        by_row.setdefault(tok.start[0], []).append(tok.start[1])

    # 行内复合体（if/try/with ...: 语句; 语句）要先把头单独成行，
    # 再把手写的行内体放到 indent+4 的块里，否则拆出来就是 IndentationError。
    header_colon: dict[int, int] = {}
    depth = 0
    for tok in toks:
        if tok.type in (tokenize.NL, tokenize.NEWLINE):
            depth = 0
            continue
        if tok.type in (tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER):
            continue
        if tok.start[0] in by_row and tok.start[0] not in header_colon:
            if (
                depth == 0
                and tok.type == tokenize.OP
                and tok.string == ":"
                and first_token_of_logical_line.get(tok.start[0], "") in COMPOUND_KEYWORDS
            ):
                header_colon[tok.start[0]] = tok.start[1]
        if tok.type == tokenize.OP and tok.string in "([{":
            depth += 1
        elif tok.type == tokenize.OP and tok.string in ")]}":
            depth -= 1

    lines = text.splitlines(keepends=True)
    for row in sorted(by_row, reverse=True):
        idx = row - 1
        physical = lines[idx].rstrip("\n")
        indent = indent_of_row.get(row, physical[: len(physical) - len(physical.lstrip())])
        is_compound = first_token_of_logical_line.get(row, "") in COMPOUND_KEYWORDS
        body_indent = indent + "    " if is_compound else indent
        cut_cols = sorted(by_row[row])
        split_header = is_compound and row in header_colon and header_colon[row] < cut_cols[0]
        head_end = header_colon[row] if split_header else cut_cols[0]
        prev = head_end + 1
        body = []
        for col in cut_cols:
            body.append(physical[prev:col].strip())
            prev = col + 1
        body.append(physical[prev:].strip())
        body = [piece for piece in body if piece]
        head = physical[: head_end + 1].rstrip() if split_header else physical[:head_end].rstrip()
        if not body:
            continue
        lines[idx : idx + 1] = [head + "\n"] + [body_indent + piece + "\n" for piece in body]
    return "".join(lines)


def main() -> int:
    changed = 0
    for rel in ruff_e702_files():
        path = REPO / rel
        original = path.read_text(encoding="utf-8")
        try:
            before = ast.dump(ast.parse(original), include_attributes=False)
        except SyntaxError:
            print(f"SKIP-unparsable {rel}")
            continue
        new = rewrite(original)
        if new is None or new == original:
            print(f"NOCHANGE {rel}")
            continue
        try:
            after = ast.dump(ast.parse(new), include_attributes=False)
        except SyntaxError as exc:
            print(f"ABORT-syntax {rel}: {exc}")
            continue
        if before != after:
            print(f"SKIP-semantic-delta {rel}")
            continue
        path.write_text(new, encoding="utf-8")
        changed += 1
    print(f"files changed: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
