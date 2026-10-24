"""UAT构建不能污染当前前端缓存或复制环境凭据。"""
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts import validate_extended_acceptance as validation


def test_temporary_build_preserves_live_cache_and_excludes_environment_files(tmp_path,monkeypatch):
    root=tmp_path/'repo';app=root/'app';(app/'node_modules').mkdir(parents=True)
    (app/'app').mkdir();(app/'app/page.tsx').write_text('source')
    (app/'app/api/materials/coverage').mkdir(parents=True)
    (app/'app/api/materials/coverage/route.ts').write_text('actual business route')
    (app/'coverage').mkdir();(app/'coverage/generated.json').write_text('{}')
    (app/'.next').mkdir();(app/'.next/live-cache').write_text('do not touch')
    (app/'.env.local').write_text('PRIVATE=not-for-validation')
    (app/'next.config.js').write_text('module.exports = {}')
    work=tmp_path/'private';work.mkdir();out=tmp_path/'evidence';out.mkdir()
    monkeypatch.setattr(validation,'ROOT',root)
    def build(args,**kwargs):
        target=Path(kwargs['cwd'])
        assert target!=app and not (target/'.env.local').exists()
        assert not (target/'.next/live-cache').exists()
        assert (target/'app/api/materials/coverage/route.ts').read_text()=='actual business route'
        assert not (target/'coverage').exists()
        assert kwargs['env']['NEXT_TELEMETRY_DISABLED']=='1'
        (target/'.next').mkdir();(target/'.next/BUILD_ID').write_text('synthetic-build-control')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(validation.subprocess,'run',build)
    target=validation.prepare_isolated_frontend(work,out,{})
    assert (app/'.next/live-cache').read_text()=='do not touch'
    assert (app/'.env.local').exists()
    assert (target/'node_modules').is_symlink()
    assert (target/'app/page.tsx').read_text()=='source'


def test_failed_build_cannot_borrow_an_existing_live_production_build(tmp_path,monkeypatch):
    root=tmp_path/'repo';app=root/'app';(app/'node_modules').mkdir(parents=True)
    (app/'.next').mkdir();(app/'.next/BUILD_ID').write_text('existing-user-build')
    work=tmp_path/'private';work.mkdir();out=tmp_path/'evidence';out.mkdir()
    monkeypatch.setattr(validation,'ROOT',root)
    monkeypatch.setattr(validation.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError,match='frontend build failed'):
        validation.prepare_isolated_frontend(work,out,{})
    assert (app/'.next/BUILD_ID').read_text()=='existing-user-build'
