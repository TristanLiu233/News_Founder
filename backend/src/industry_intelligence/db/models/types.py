"""SQLAlchemy types shared by ORM models."""

from __future__ import annotations

from sqlalchemy import Enum as SQLAlchemyEnum

from industry_intelligence.domain.enums import Domain, SubjectType


def _enum_values(enum_class: type[Domain] | type[SubjectType]) -> list[str]:
    return [member.value for member in enum_class]


def domain_enum() -> SQLAlchemyEnum:
    return SQLAlchemyEnum(
        Domain,
        name="domain_enum",
        values_callable=_enum_values,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
    )


def subject_type_enum() -> SQLAlchemyEnum:
    return SQLAlchemyEnum(
        SubjectType,
        name="subject_type_enum",
        values_callable=_enum_values,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
    )
