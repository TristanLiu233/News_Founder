from industry_intelligence.domain.enums import Domain, SubjectType
from industry_intelligence.domain.models import SubjectReference


def test_domain_enum() -> None:
    assert [domain.value for domain in Domain] == ["computer", "finance", "gaming"]


def test_subject_type_enum() -> None:
    assert [subject_type.value for subject_type in SubjectType] == [
        "event",
        "company",
        "stock",
        "game",
        "repository",
        "paper",
        "model",
        "technology",
    ]


def test_subject_reference_is_framework_independent() -> None:
    subject = SubjectReference(
        domain=Domain.COMPUTER,
        subject_type=SubjectType.MODEL,
        canonical_name="example-model",
    )

    assert subject.canonical_name == "example-model"
