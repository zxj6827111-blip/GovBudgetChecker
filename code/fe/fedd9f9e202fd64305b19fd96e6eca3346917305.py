import json
from pathlib import Path

import pytest

from api import runtime
from src.services.reanalyze_inheritance import build_options_preview
from tests.test_independent_reanalyze_review import configure, isolated_queue, seed


@pytest.mark.asyncio
async def test_warning_is_not_available_from_current_job_after_start(isolated_queue):
    root, _, _ = isolated_queue
    configure(default_on=False)
    directory, payload = seed(root, 'review-warning-lifetime', ai=True)
    payload['ai_configuration']['model'] = 'previous-model-before-edit'
    (directory / 'status.json').write_text(json.dumps(payload))
    response = await runtime.reanalyze_job(directory.name, {'check_method': 'inherit'})
    stored = json.loads((directory / 'status.json').read_text())
    detail = runtime.get_job_status_payload(directory.name)
    preview = build_options_preview(directory, stored)
    evidence = {
        'reanalysisResponseWarnings': response.get('inheritance_warnings'),
        'persistedStatusHasWarnings': 'inheritance_warnings' in stored,
        'jobDetailHasWarnings': 'inheritance_warnings' in detail,
        'newPreviewModelState': preview['model']['state'],
        'newPreviewModelMessage': preview['model']['message'],
    }
    Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/independent_reanalyze_inherit_20261109_r2/warning-lifetime-results.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    assert any('model' in item for item in response['inheritance_warnings'])
    assert evidence['persistedStatusHasWarnings'] is False
    assert evidence['jobDetailHasWarnings'] is False
    assert preview['model']['state'] == 'ok'
