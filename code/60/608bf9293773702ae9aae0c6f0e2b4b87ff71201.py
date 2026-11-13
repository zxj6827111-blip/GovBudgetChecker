"""为一次性回归核验固定随机尾缀，不参与常规测试收集。"""
import uuid
import pytest

@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    if item.name == 'test_organization_jobs_scope_controls_child_inclusion':
        monkeypatch = item._request.getfixturevalue('monkeypatch')
        value = uuid.UUID('abcd2024-0000-4000-8000-000000000001')
        monkeypatch.setattr(item.module.uuid, 'uuid4', lambda: value)
