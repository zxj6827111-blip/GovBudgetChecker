from pathlib import Path
import subprocess, os, time, json

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
env = os.environ.copy()
for key in list(env):
    if key.startswith(("GOVBUDGET_TEST_AI_", "OPENAI_", "GEMINI_", "ZHIPU_", "DEEPSEEK_")):
        env.pop(key)
env.update(DATABASE_URL="", GOVBUDGET_TEST_DATABASE_URL="", AI_ASSIST_ENABLED="false", AI_MAIN_ENABLED="false", AI_BACKUP_ENABLED="false", AI_LOCATOR_ENABLED="false", PYTHON_DOTENV_DISABLED="1", UPLOAD_DIR=str(OUT / "isolated_uploads"))
commands = [
    ("full_pytest", [".venv/bin/python", "-m", "pytest", "-q"]),
    ("remediation_pytest", [".venv/bin/python", "-m", "pytest", "tests/remediation", "-q", "-rs"]),
    ("frontend_unit", ["npm", "--prefix", "app", "run", "test:unit"]),
    ("frontend_build", ["npm", "--prefix", "app", "run", "build"]),
    ("ruff", [".venv/bin/ruff", "check", "."]),
    ("env_consistency", [".venv/bin/python", "scripts/check_env_consistency.py"]),
    ("log_safety", [".venv/bin/python", "scripts/check_log_message_safety.py"]),
    ("zc07_preflight", [".venv/bin/python", "scripts/run_zc07_acceptance.py", "--output", str(OUT / "zc07_preflight.json")]),
]
for name, cmd in commands:
    started = time.monotonic()
    with (OUT / (name + ".log")).open("w") as log:
        result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    data = {"command": cmd, "exit_code": result.returncode, "elapsed_seconds": round(time.monotonic()-started, 2)}
    (OUT / (name + "_run.json")).write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(name, json.dumps(data, ensure_ascii=False), flush=True)
