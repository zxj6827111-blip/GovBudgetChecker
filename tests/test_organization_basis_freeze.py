"""阶段 0：冻结"上传所选组织"依据（api.runtime.resolve_organization_basis）。

依据来自任务 status.json 记录的所选组织；目录查不到、未选择或存储异常时
level=None，下游按"未选择"处理，原路径不变。
"""
from types import SimpleNamespace

import api.runtime as runtime


class _FakeStorage:
    def __init__(self, org):
        self._org = org

    def get_by_id(self, org_id):
        if self._org is not None and self._org.id == org_id:
            return self._org
        return None


def test_basis_records_id_name_level_and_source(monkeypatch):
    org = SimpleNamespace(id="org-1", name="上海市测试中心", level="unit")
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", True)
    monkeypatch.setattr(runtime, "require_org_storage", lambda: _FakeStorage(org))
    basis = runtime.resolve_organization_basis("org-1")
    assert basis == {"organization_id": "org-1", "organization_name": "上海市测试中心",
                     "level": "unit", "source": "upload_selection"}
    # resolve_organization_level 与冻结依据同源。
    assert runtime.resolve_organization_level("org-1") == "unit"


def test_basis_unselected_or_missing_org_has_no_level(monkeypatch):
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", True)
    monkeypatch.setattr(runtime, "require_org_storage", lambda: _FakeStorage(None))
    for value in (None, "", "missing-org"):
        basis = runtime.resolve_organization_basis(value)
        assert basis["level"] is None
        assert basis["organization_name"] is None
        assert basis["source"] == "upload_selection"


def test_basis_storage_failure_keeps_level_none(monkeypatch):
    def _boom():
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(runtime, "ORG_AVAILABLE", True)
    monkeypatch.setattr(runtime, "require_org_storage", _boom)
    basis = runtime.resolve_organization_basis("org-1")
    assert basis["level"] is None and basis["organization_name"] is None


def test_basis_without_org_module_returns_unselected(monkeypatch):
    monkeypatch.setattr(runtime, "ORG_AVAILABLE", False)
    basis = runtime.resolve_organization_basis("org-1")
    assert basis["level"] is None and basis["source"] == "upload_selection"
