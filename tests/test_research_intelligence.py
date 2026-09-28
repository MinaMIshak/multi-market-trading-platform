"""Research intelligence notes separate facts, derivations, interpretation and UNKNOWN."""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.research.intelligence import ResearchNote

AT = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


def fact(statement_id='f1', observed_at=AT - timedelta(hours=1), **extra):
    return {'statement_id': statement_id, 'kind': 'SOURCE_FACT', 'text': 'Close 81.20 EGP',
            'provenance': {'source_id': 'platform-daily-canonical', 'locator': 'artifact:abc',
                           'observed_at': observed_at, 'as_of': '2026-09-27'}, **extra}


def note(*statements, **extra):
    return {'schema_version': 'research-note-v1', 'market': 'EGX', 'subject': 'COMI',
            'generated_at': AT, 'statements': list(statements), **extra}


def derived(inputs=('f1',)):
    return {'statement_id': 'd1', 'kind': 'DERIVED_METRIC', 'text': '1-day return +1.1%',
            'inputs': list(inputs), 'method': 'close[t]/close[t-1]-1'}


def interpretation(inputs=('d1',)):
    return {'statement_id': 'm1', 'kind': 'MODEL_INTERPRETATION', 'text': 'Momentum is modest',
            'inputs': list(inputs), 'model': 'analyst-template-v1'}


def unknown():
    return {'statement_id': 'u1', 'kind': 'UNKNOWN', 'text': 'Free float',
            'reason': 'No platform-owned source'}


def test_complete_note_keeps_every_statement_class_distinct():
    parsed = ResearchNote.model_validate(note(fact(), derived(), interpretation(), unknown()))
    projection = parsed.projection()
    assert projection['execution_authority'] == 'NONE'
    assert [row['kind'] for row in projection['statements']] == [
        'SOURCE_FACT', 'DERIVED_METRIC', 'MODEL_INTERPRETATION', 'UNKNOWN']
    assert projection['statements'][0]['provenance']['source_id'] == 'platform-daily-canonical'
    assert datetime.fromisoformat(projection['generated_at']) == AT


def test_source_fact_requires_provenance():
    raw = fact()
    raw.pop('provenance')
    with pytest.raises(ValidationError):
        ResearchNote.model_validate(note(raw))


def test_fact_observed_after_generation_is_lookahead():
    with pytest.raises(ValidationError, match='after note generation'):
        ResearchNote.model_validate(note(fact(observed_at=AT + timedelta(seconds=1))))


def test_naive_timestamps_rejected():
    with pytest.raises(ValidationError):
        ResearchNote.model_validate(note(fact(observed_at=datetime(2026, 9, 28, 11))))
    with pytest.raises(ValidationError):
        ResearchNote.model_validate(note(fact(), generated_at=datetime(2026, 9, 28, 12)))


def test_derived_metric_requires_known_prior_inputs():
    with pytest.raises(ValidationError, match='unknown or later input'):
        ResearchNote.model_validate(note(fact(), derived(inputs=('missing',))))
    with pytest.raises(ValidationError, match='unknown or later input'):
        ResearchNote.model_validate(note(derived(), fact()))
    with pytest.raises(ValidationError):
        ResearchNote.model_validate(note(fact(), derived(inputs=())))


def test_derivation_cannot_rest_on_interpretation_or_unknown():
    with pytest.raises(ValidationError, match='must rest on facts or derived metrics'):
        ResearchNote.model_validate(note(unknown(), derived(inputs=('u1',))))
    bad = derived(inputs=('m1',))
    with pytest.raises(ValidationError, match='must rest on facts or derived metrics'):
        ResearchNote.model_validate(note(fact(), {**derived(), 'statement_id': 'd0'},
                                         interpretation(inputs=('d0',)), bad))


def test_interpretation_may_not_rest_on_unknown_only():
    with pytest.raises(ValidationError, match='must rest on facts or derived metrics'):
        ResearchNote.model_validate(note(unknown(), interpretation(inputs=('u1',))))


def test_unknown_cannot_carry_provenance_or_inputs():
    with pytest.raises(ValidationError):
        ResearchNote.model_validate(note({**unknown(), 'provenance': fact()['provenance']}))
    with pytest.raises(ValidationError):
        ResearchNote.model_validate(note(fact(), {**unknown(), 'inputs': ['f1']}))


def test_duplicate_statement_ids_rejected():
    with pytest.raises(ValidationError, match='duplicate statement'):
        ResearchNote.model_validate(note(fact(), fact()))


@pytest.mark.parametrize('field', ['execution_authority', 'order', 'side', 'quantity', 'fill'])
def test_no_execution_or_order_fields_accepted(field):
    with pytest.raises(ValidationError):
        ResearchNote.model_validate(note(fact(), **{field: 'BUY'}))


def test_unknown_market_rejected():
    with pytest.raises(ValidationError):
        ResearchNote.model_validate(note(fact(), market='LSE'))


def test_product_api_research_section_is_fail_closed(monkeypatch):
    for name in ('EGX_DB_PATH', 'EGX_SCAN_HISTORY_PATH', 'EGX_SCAN_LEDGER_PATH', 'EGX_SHADOW_DIRECTORY'):
        monkeypatch.delenv(name, raising=False)
    from app.main import product, root
    state = product('EGX', 'RESEARCH')
    assert state['live'] == 'DISABLED'
    assert state['research']['notes'] is None
    services = state['research']['financial_services']
    assert services['execution_authority'] == 'NONE'
    assert {row['status'] for row in services['connectors']} == {'FAIL_CLOSED'}
    assert 'Sourced research notes: UNKNOWN' in root('EGX', 'RESEARCH').body.decode()
