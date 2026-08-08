"""Shared Pydantic base model for configuration DTOs.

This module intentionally lives outside the ``nanobot.config`` package so
runtime modules can define local config DTOs without importing the full root
configuration schema.
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Base(BaseModel):
    """Strict base model with camelCase JSON aliases and Python field names."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )
