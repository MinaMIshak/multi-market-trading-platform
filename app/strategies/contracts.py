"""Versioned, immutable, research-only strategy boundary."""
import hashlib
import json
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False, strict=True)


class StrategyConfig(Contract):
    config_version: str = Field(min_length=1)

    @property
    def identity(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timezone-aware timestamp required')
    return value


class Candidate(Contract):
    contract_version: Literal['strategy-candidate-v1'] = 'strategy-candidate-v1'
    strategy_id: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    config_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    decision_time: datetime
    data_cutoff: datetime
    direction: Literal['LONG'] = 'LONG'
    state: Literal['WATCH', 'READY', 'CONTINUATION_CONFIRMED', 'REVERSAL_CONFIRMED',
                   'HIGH_RISK', 'INVALIDATED', 'NO_CONFIRMATION']
    entry_reference: float | None = Field(gt=0)
    stop_reference: float | None = Field(gt=0)
    target_reference: float | None = Field(default=None, gt=0)
    evidence_json: str
    validation_status: Literal['UNVALIDATED'] = 'UNVALIDATED'
    execution_allowed: Literal[False] = False

    @model_validator(mode='after')
    def check(self):
        if aware(self.data_cutoff) > aware(self.decision_time):
            raise ValueError('future evidence')
        return self


def evidence_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f'unsupported evidence type: {type(value).__name__}')


def candidate(engine, config, symbol, at, cutoff, state, entry=None, stop=None,
              *, strategy_version, **evidence):
    return Candidate(strategy_id=engine, strategy_version=strategy_version,
                     config_id=config.identity, symbol=symbol, decision_time=at,
                     data_cutoff=cutoff, state=state, entry_reference=entry,
                     stop_reference=stop, evidence_json=json.dumps(
                         {'config': config.model_dump(), **evidence}, sort_keys=True,
                         separators=(',', ':'), allow_nan=False, default=evidence_default))
