#!/usr/bin/env python
"""环境变量三方一致性检查（Task 14.1 / 缺口 B-10）。

对账三方：
1. **代码**实际读取的变量（`os.getenv` / `_env_flag` 等包装函数 / 动态前缀拼装）；
2. **`.env.example`** 声明的变量（含注释掉的 `# NAME=` 行，视为"已文档化的可选项"）；
3. **compose 文件**里设置或引用的变量。

会报错的三类不一致：
- `dead_compose_var`：compose 设了某个变量，但代码从来不读它 —— 部署以为配了，实际无效。
  历史真实案例：`RULES_FILE_PATH=/app/engine/rules_v33.py`（代码读的是 `RULES_FILE`，
  而且指向的是 YAML 规则集而非 .py 模块）。
- `dead_example_var`：`.env.example` 声明了代码不读的变量，误导运维。
- `undeclared_code_var`：代码读了但 `.env.example` 完全没提，运维无从得知。

不算不一致（需在下面的白名单里显式登记，附理由）：
- 只给基础设施用的变量（`POSTGRES_*` 给 postgres 镜像、`HOST_*` 给 compose 挂载）；
- 只在测试/诊断脚本里用的开关（`TESTING`、`*_TEST_*`、`DIAG_*`）。

用法：
    python scripts/check_env_consistency.py
    python scripts/check_env_consistency.py --json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Set

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

CODE_ROOTS: Sequence[str] = ("api", "src", "scripts")
COMPOSE_FILES: Sequence[str] = ("docker-compose.yml", "docker-compose.ai.yml")
ENV_EXAMPLE = ".env.example"

_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")

#: 读环境变量的包装函数名（除 os.getenv / os.environ.get）
_ENV_HELPERS = frozenset(
    {
        "_env_flag",
        "_env_text",
        "_env_int",
        "_env_float",
        "_env_bool",
        "env_flag",
        "_read_int_env",
        "_read_bool_env",
        "_read_float_env",
        "_read_str_env",
    }
)

#: 代码里用 f-string 前缀动态拼出来的变量（`src/services/ai_client.py:_build_env_provider_slot`）。
#: AST 抓不到这类名字，必须显式登记，否则会被误判成 dead var。
_DYNAMIC_PREFIXES: Sequence[str] = ("AI_MAIN", "AI_BACKUP", "AI_LOCATOR")
_DYNAMIC_SUFFIXES: Sequence[str] = (
    "PROVIDER_TYPE",
    "BASE_URL",
    "API_KEY",
    "MODEL",
    "ENABLED",
    "TIMEOUT_S",
    "RETRIES",
)

#: 只给基础设施 / compose 自身用，代码不读也正常
_INFRA_ONLY: Dict[str, str] = {
    "POSTGRES_USER": "postgres 官方镜像读取",
    "POSTGRES_PASSWORD": "postgres 官方镜像读取",
    "POSTGRES_DB": "postgres 官方镜像读取",
    "HOST_DATA_DIR": "compose 卷挂载路径",
    "HOST_LOGS_DIR": "compose 卷挂载路径",
    "HOST_SAMPLES_DIR": "compose 卷挂载路径",
    "HOST_UPLOADS_DIR": "compose 卷挂载路径",
    "COMPOSE_FILE": "docker compose 自身的多文件开关",
    "BACKEND_URL": "Next.js 前端（app/ 下 TS 代码）读取，不在 Python 侧",
    # 下面两个是给 ai-extractor 这个**独立服务**透传的：仓库自带的最小实现
    # （ai_extractor_service.py）不读，但换成真实抽取器实现时需要，
    # 所以刻意保留在 compose 的 ai-extractor 段，并在此登记以免被判成死变量。
    "ARK_BASE_URL": "ai-extractor 独立服务的透传配置，Python 后端不读",
    "ARK_MODEL": "ai-extractor 独立服务的透传配置，Python 后端不读",
}

#: 只在测试 / 本地诊断里用，刻意不进 .env.example
_INTERNAL_ONLY: Dict[str, str] = {
    "TESTING": "pytest 环境标记",
    "PDF_PARSE_TEST_ALLOCATE_MB": "解析隔离测试钩子",
    "PDF_PARSE_TEST_DELAY_SECONDS": "解析隔离测试钩子",
    "PDF_PARSE_PROCESS_START_METHOD": "跨平台测试用的进程启动方式覆盖",
    "RULES_PROCESS_TEST_DELAY_SECONDS": "规则进程测试钩子",
    "RULES_PROCESS_START_METHOD": "跨平台测试用的进程启动方式覆盖",
    "DIAG_BACKEND_URL": "scripts/diagnose_* 本地诊断脚本",
    "DIAG_FRONTEND_URL": "scripts/diagnose_* 本地诊断脚本",
    "BACKEND_API_KEY": "诊断脚本里 GOVBUDGET_API_KEY 的兼容别名",
    "CODESPACE_NAME": "GitHub Codespaces 注入",
    "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "GitHub Codespaces 注入",
    "ALLOW_INSECURE_DEFAULT_ADMIN": "仅测试放行弱默认口令，生产不应出现",
    "GOVBUDGET_TEST_DATABASE_URL": "连库测试专用",
    "ZHIPU_API_KEY": "config/providers.yaml 里 api_key_env 的取值之一",
}


def code_env_names() -> Dict[str, List[str]]:
    """代码实际读取的环境变量 -> 出现位置。"""
    found: Dict[str, List[str]] = {}

    def record(name: str, location: str) -> None:
        if _ENV_NAME.match(name):
            found.setdefault(name, []).append(location)

    for root in CODE_ROOTS:
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            rel = path.relative_to(_REPO_ROOT).as_posix()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                matched = False
                if isinstance(func, ast.Attribute) and func.attr in {"getenv", "get"}:
                    owner = func.value
                    if isinstance(owner, ast.Attribute) and owner.attr == "environ":
                        matched = True
                    elif isinstance(owner, ast.Name) and owner.id in {"os", "environ"}:
                        matched = True
                elif isinstance(func, ast.Name) and func.id in _ENV_HELPERS:
                    matched = True
                elif isinstance(func, ast.Attribute) and func.attr in _ENV_HELPERS:
                    matched = True
                if not matched:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    record(first.value, f"{rel}:{node.lineno}")

    for prefix in _DYNAMIC_PREFIXES:
        for suffix in _DYNAMIC_SUFFIXES:
            record(
                f"{prefix}_{suffix}",
                "src/services/ai_client.py:_build_env_provider_slot（前缀拼装）",
            )

    providers = _REPO_ROOT / "config" / "providers.yaml"
    if providers.is_file():
        for match in re.finditer(
            r"api_key_env:\s*\"?([A-Z][A-Z0-9_]*)\"?", providers.read_text(encoding="utf-8")
        ):
            record(match.group(1), "config/providers.yaml:api_key_env")

    return found


_ENV_DECLARATION = re.compile(r"^#?\s*([A-Z][A-Z0-9_]{2,})\s*=")


def parse_env_declarations(text: str) -> Set[str]:
    """解析 `.env` 风格文本里声明的变量名。

    注释掉的 `# NAME=` 也算"已文档化的可选项"——本检查的目的是文档覆盖率，
    不是运行时取值；把注释行排除会把大量刻意留默认值的可选项误判成未声明。
    """
    names: Set[str] = set()
    for line in text.splitlines():
        match = _ENV_DECLARATION.match(line.strip())
        if match:
            names.add(match.group(1))
    return names


def env_example_names() -> Set[str]:
    return parse_env_declarations((_REPO_ROOT / ENV_EXAMPLE).read_text(encoding="utf-8"))


_COMPOSE_REFERENCE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")
_COMPOSE_ASSIGNMENT = re.compile(r"^\s*-?\s*([A-Z][A-Z0-9_]{2,})\s*[:=]")


def parse_compose_env(text: str) -> Dict[str, List[int]]:
    """解析 compose 文本里设置（`KEY: v` / `- KEY=v`）或引用（`${KEY}`）的变量 -> 行号。

    整行注释跳过：被注释掉的示例配置不构成"部署以为配了"的风险。
    """
    found: Dict[str, List[int]] = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("#"):
            continue
        names = set(_COMPOSE_REFERENCE.findall(line))
        match = _COMPOSE_ASSIGNMENT.match(line)
        if match:
            names.add(match.group(1))
        for name in names:
            found.setdefault(name, []).append(lineno)
    return found


def compose_env_names() -> Dict[str, List[str]]:
    found: Dict[str, List[str]] = {}
    for rel in COMPOSE_FILES:
        path = _REPO_ROOT / rel
        if not path.is_file():
            continue
        for name, lines in parse_compose_env(path.read_text(encoding="utf-8")).items():
            found.setdefault(name, []).extend(f"{rel}:{line}" for line in lines)
    return found


def analyze() -> Dict[str, object]:
    code = code_env_names()
    example = env_example_names()
    compose = compose_env_names()
    code_names = set(code)
    known = set(_INFRA_ONLY) | set(_INTERNAL_ONLY)

    dead_compose = sorted(
        {name for name in compose if name not in code_names and name not in known}
    )
    dead_example = sorted(
        {name for name in example if name not in code_names and name not in known}
    )
    undeclared = sorted(
        {name for name in code_names if name not in example and name not in known}
    )

    violations: List[Dict[str, object]] = []
    for name in dead_compose:
        violations.append(
            {
                "kind": "dead_compose_var",
                "name": name,
                "detail": f"compose 设置了 {name}，但代码从不读取（{', '.join(compose[name][:2])}）",
            }
        )
    for name in dead_example:
        violations.append(
            {
                "kind": "dead_example_var",
                "name": name,
                "detail": f".env.example 声明了 {name}，但代码从不读取",
            }
        )
    for name in undeclared:
        violations.append(
            {
                "kind": "undeclared_code_var",
                "name": name,
                "detail": f"代码读取 {name}（{code[name][0]}），但 .env.example 未声明",
            }
        )

    return {
        "counts": {
            "code": len(code_names),
            "env_example": len(example),
            "compose": len(compose),
        },
        "violations": violations,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="环境变量三方一致性检查（代码 / .env.example / compose）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    return parser


def main(argv: List[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report = analyze()
    violations = report["violations"]
    assert isinstance(violations, list)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in violations:
            print(f"[{item['kind']}] {item['detail']}")
        if violations:
            print(f"\n发现 {len(violations)} 处环境变量不一致。")
        else:
            counts = report["counts"]
            assert isinstance(counts, dict)
            print(
                "环境变量三方对账通过："
                f"代码 {counts['code']} 个 / .env.example {counts['env_example']} 个 / "
                f"compose {counts['compose']} 个，无死变量、无未声明变量。"
            )
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
