"""检查主体：关联到市/区级上级节点时，规则以封面单位为本单位。

真值：2026-10-22 上海市退役军人事务局 2023 年度决算手动关联到"上海市"，
规则把说明原文"上海市退役军人事务局2023年度财政拨款收入支出总计"判成
其他主体，V33-105/V33-221/V33-234 全部 insufficient_data，转成人工补核。
"""

from src.engine.native_narrative_checks import _foreign_owner
from src.services.check_subject import resolve_check_subject
from src.services.structured_ingest_runner import build_ingest_metadata

COVER = "上海市退役军人事务局"
SENTENCE = "上海市退役军人事务局2023年度财政拨款收入支出总计108,190.13万元"


def _resolve(**overrides):
    params = dict(
        organization_id="3034274efad9",
        organization_name="上海市",
        organization_level="city",
        cover_name=COVER,
        cover_conflicted=False,
        subject_level="department",
    )
    params.update(overrides)
    return resolve_check_subject(**params)


def test_city_link_with_department_cover_checks_the_cover_unit():
    subject = _resolve()
    assert subject["name"] == COVER
    assert subject["source"] == "cover"
    assert subject["organization_name"] == "上海市"
    assert subject["conflict"]["code"] == "linked_parent_organization"
    assert "上海市" in subject["conflict"]["message"] and COVER in subject["conflict"]["message"]


def test_the_original_false_foreign_owner_is_gone():
    # 以"上海市"为本单位时，剩下的"退役军人事务局"被判成其他主体。
    assert _foreign_owner(SENTENCE, "上海市")
    assert not _foreign_owner(SENTENCE, _resolve()["name"])


def test_district_link_with_department_cover():
    subject = _resolve(organization_name="普陀区", organization_level="district",
                       cover_name="上海市普陀区财政局")
    assert subject["name"] == "上海市普陀区财政局"
    assert "区级节点" in subject["conflict"]["message"]


def test_cover_that_is_another_district_is_not_a_department():
    subject = _resolve(cover_name="上海市普陀区")
    assert subject["name"] == "上海市"
    assert subject["conflict"] is None


def test_department_link_is_kept():
    subject = _resolve(organization_name=COVER, organization_level="department")
    assert subject["name"] == COVER
    assert subject["source"] == "organization"
    assert subject["conflict"] is None


def test_unrelated_cover_is_not_adopted():
    subject = _resolve(cover_name="北京市民政局")
    assert subject["name"] == "上海市"
    assert subject["conflict"] is None


def test_conflicted_cover_or_government_material_is_not_adopted():
    assert _resolve(cover_conflicted=True)["name"] == "上海市"
    assert _resolve(subject_level="government")["name"] == "上海市"


def test_unknown_level_is_not_adopted():
    # 组织已从组织树删除时查不到层级，不能凭名字猜是上级节点。
    assert _resolve(organization_level=None)["name"] == "上海市"


def test_unlinked_job_keeps_the_profile_name():
    subject = _resolve(organization_id=None, organization_name=COVER, organization_level=None)
    assert subject["name"] == COVER
    assert subject["source"] == "document_profile"
    assert subject["conflict"] is None


def test_ingest_metadata_carries_the_check_subject_without_changing_the_link():
    payload = build_ingest_metadata(organization_name="上海市", check_subject_name=COVER)
    assert payload["organization_name"] == "上海市"
    assert payload["check_subject_name"] == COVER
