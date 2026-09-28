"""Sourced research notes; intelligence only, never trade or execution authority.

Every statement is exactly one of: an observed source fact with provenance, a
metric derived from earlier facts/metrics, a labelled model interpretation, or
an explicit UNKNOWN. Facts observed after note generation are lookahead.
"""
from datetime import date, datetime
from typing import Annotated, Literal, Union

from pydantic import Field, field_validator, model_validator

from app.strategies.contracts import Contract

StatementId = Annotated[str, Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_.-]+$')]
Text = Annotated[str, Field(min_length=1, max_length=2000)]


def _aware(value):
    if value.utcoffset() is None:
        raise ValueError('timezone-aware timestamp required')
    return value


class Provenance(Contract):
    source_id: Text
    locator: Text
    observed_at: datetime
    as_of: date = Field(strict=False)

    _observed = field_validator('observed_at')(_aware)


class SourceFact(Contract):
    statement_id: StatementId
    kind: Literal['SOURCE_FACT']
    text: Text
    provenance: Provenance


class DerivedMetric(Contract):
    statement_id: StatementId
    kind: Literal['DERIVED_METRIC']
    text: Text
    inputs: tuple[StatementId, ...] = Field(min_length=1, strict=False)
    method: Text


class ModelInterpretation(Contract):
    statement_id: StatementId
    kind: Literal['MODEL_INTERPRETATION']
    text: Text
    inputs: tuple[StatementId, ...] = Field(min_length=1, strict=False)
    model: Text


class UnknownStatement(Contract):
    statement_id: StatementId
    kind: Literal['UNKNOWN']
    text: Text
    reason: Text


Statement = Annotated[Union[SourceFact, DerivedMetric, ModelInterpretation, UnknownStatement],
                      Field(discriminator='kind')]


class ResearchNote(Contract):
    schema_version: Literal['research-note-v1']
    market: Literal['EGX', 'US']
    subject: Text
    generated_at: datetime
    statements: tuple[Statement, ...] = Field(min_length=1, strict=False)

    _generated = field_validator('generated_at')(_aware)

    @model_validator(mode='after')
    def _lineage(self):
        kinds = {}
        for statement in self.statements:
            if statement.statement_id in kinds:
                raise ValueError('duplicate statement id')
            if isinstance(statement, SourceFact) and statement.provenance.observed_at > self.generated_at:
                raise ValueError('source fact observed after note generation')
            for key in getattr(statement, 'inputs', ()):
                if key not in kinds:
                    raise ValueError('unknown or later input statement')
                if kinds[key] not in ('SOURCE_FACT', 'DERIVED_METRIC'):
                    raise ValueError('derivations and interpretations must rest on facts or derived metrics')
            kinds[statement.statement_id] = statement.kind
        return self

    def projection(self):
        """JSON-safe view; execution authority is structurally absent."""
        return {**self.model_dump(mode='json'), 'execution_authority': 'NONE'}
