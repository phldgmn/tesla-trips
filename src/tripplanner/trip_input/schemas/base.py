"""Shared base models for the /trips API schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelCaseAPI(BaseModel):
    """Request model: JSON keys are camelCase, snake_case keys are rejected."""

    model_config = ConfigDict(alias_generator=to_camel, serialize_by_alias=True, extra="forbid")


class CamelCaseResponseAPI(CamelCaseAPI):
    """Response model: built in Python by field name, serialized as camelCase."""

    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True, extra="ignore")
