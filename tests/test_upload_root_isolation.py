"""任务产物目录隔离（Task 15.2 实测缺陷的回归线）。

实测事实：`api/runtime.py:33` 把 `UPLOAD_ROOT` 解析成仓库根目录下的 `uploads/`，
而有 5 个走上传接口的测试没有自己重定向，于是**每跑一次全量测试就往真实产物目录
写 5 个任务**（CI 全新检出跑完 pytest 后 uploads/ 里出现 5 个任务目录；
本地 uploads/ 已从 661 涨到 783 个目录，`split_mode.pdf` 反复重现）。
这会污染被回放度量的历史语料，也让 CI 的业务门禁指标失真。

断言意图（正反对照）：
- 正例：任意测试里拿到的 `runtime.UPLOAD_ROOT` 必须是可写的临时目录，
  且 `UPLOAD_DIR` 环境变量与之一致（`analysis_result_store` 走的是环境变量那条路）；
- **反例**：它绝不能是仓库里的 `uploads/`；且经由 runtime 写出的产物必须落在临时目录，
  仓库 `uploads/` 下不得出现该任务目录。
"""

from __future__ import annotations

import os
from pathlib import Path

from api import runtime

_REPO_UPLOADS = (Path(__file__).resolve().parents[1] / "uploads").resolve()


def test_upload_root_is_redirected_to_tmp() -> None:
    active = Path(runtime.UPLOAD_ROOT).resolve()

    # 反例：绝不能指向仓库里的真实产物目录
    assert active != _REPO_UPLOADS
    assert _REPO_UPLOADS not in active.parents

    # 正例：是存在且可写的目录，环境变量与模块属性一致
    assert active.is_dir()
    assert os.access(active, os.W_OK)
    assert Path(os.environ["UPLOAD_DIR"]).resolve() == active


def test_job_artifacts_land_in_tmp_not_in_repo_uploads() -> None:
    job_id = "job-isolation-probe"
    job_dir = Path(runtime.UPLOAD_ROOT) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    runtime.write_json_file(job_dir / "status.json", {"status": "queued", "job_id": job_id})

    # 正例：产物确实写出来了
    assert (job_dir / "status.json").is_file()
    # 反例：仓库 uploads/ 下不得出现这个任务目录
    assert not (_REPO_UPLOADS / job_id).exists()


def test_isolation_fixture_is_autouse_for_every_test() -> None:
    """这条用例刻意不请求任何 fixture：能通过就说明隔离是 autouse 生效的。"""
    assert Path(runtime.UPLOAD_ROOT).resolve() != _REPO_UPLOADS
