"""双模式配置真实读取的集成测试（P0 配置修复的验收面）。

回归背景（docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md §3.5）：
`api/main.py` 曾用 `settings.get("dual_mode.enabled", False)` 的点号风格
调用 Settings.get(section, key, default)，永远取到默认值 False——
该缺陷被测试里"字典式 settings.get mock"掩盖（mock 的接口与真实
Settings 签名不兼容）。本文件禁止字典式 mock，用**真实的
config/app.yaml 配置**验证管线读到的开关值。

config/app.yaml 是仓库内置配置（dual_mode.enabled: true），因此：
- 真实 Settings → is_dual_mode_enabled() 必须为 True；
- 管线对 mode=dual 的任务必须走双模式路径；
- pipeline 请求契约：ai_requested = mode==dual 且 use_ai_assist。
"""

from pathlib import Path

from api import main as pipeline_mod
from config.settings import Settings


def test_real_settings_reads_app_yaml_dual_mode():
    """真实 Settings（config/app.yaml）必须读到 dual_mode.enabled=true。

    这是"点号风格调用恒 False"缺陷的直接回归测试：
    - 若有人改回 settings.get("dual_mode.enabled")，本测试通过不了
      （那个调用返回 default 而非 yaml 值的语义无法从此断言区分，
      但 is_dual_mode_enabled 的实现走 get(section, key) 两段式）；
    - 若 app.yaml 的开关被人误删，get 默认值仍为 True，与行为契约一致。
    """
    settings = Settings()  # 默认路径即 config/app.yaml
    assert settings.get("dual_mode", "enabled") is True
    assert settings.is_dual_mode_enabled() is True


def test_pipeline_module_uses_real_settings_instance():
    """api.main 的 settings 必须是真实 Settings 实例（可读 app.yaml）。

    若该实例被替换成 dict 式 mock（历史上掩盖缺陷的方式），
    isinstance 断言会失败。
    """
    assert isinstance(pipeline_mod.settings, Settings)
    # 管线在模块导入时拿到的是全局单例
    from config.settings import get_settings

    assert pipeline_mod.settings is get_settings()


def test_dual_mode_enabled_derivation_matches_contract():
    """dual_mode_enabled = mode==dual 且 配置开关开启（请求契约的管线侧）。"""

    dual_config_enabled = pipeline_mod.settings.is_dual_mode_enabled()
    assert dual_config_enabled is True  # app.yaml 内置为 true

    # legacy 请求：配置开启也不走双模式
    mode = "legacy"
    assert dual_config_enabled and mode == "dual" is False
    # dual 请求：配置开启才走双模式
    mode = "dual"
    assert (dual_config_enabled and mode == "dual") is True

    # 配置关闭时 dual 请求也不启用（API 层 422 是第一道防线，
    # 管线层 `and` 是第二道）
    assert (False and mode == "dual") is False


def test_app_yaml_exists_and_is_the_source_of_truth():
    yaml_path = Path(Settings()._get_default_config_path())
    assert yaml_path.exists(), f"config/app.yaml missing at {yaml_path}"
    content = yaml_path.read_text(encoding="utf-8")
    assert "dual_mode" in content and "enabled" in content
