"""Regression coverage for SecurityMasterRepository provider-scoped alias resolution."""
from uuid import uuid4

import pytest

from app.data.security_master import CanonicalInstrument, InstrumentType
from app.storage.database import Database
from app.storage.security_master_repository import SecurityMasterRepository


def db(tmp_path):
    database = Database(tmp_path / 'security-master.db')
    database.initialize()
    return database


def instrument(instrument_id, *, provider, ticker, code):
    return CanonicalInstrument(
        instrument_id=instrument_id, instrument_type=InstrumentType.EQUITY,
        canonical_ticker=ticker, source_provider=provider, source_symbol_code=code,
        source_sha256='a' * 64,
    )


def test_provider_symbol_code_resolves_under_its_own_provider(tmp_path):
    """A source-symbol-code alias must be resolvable under the provider whose
    snapshot created it, not hardcoded to a fixed provider name."""
    instrument_id = uuid4()
    database = db(tmp_path)
    repository = SecurityMasterRepository(database)
    repository.replace_provider_snapshot(provider='fixture', instruments=[
        instrument(instrument_id, provider='fixture', ticker='COMI', code='COMI.EGX'),
    ])
    resolved = repository.resolve('COMI.EGX', provider='fixture')
    assert resolved['instrument_id'] == str(instrument_id)
    assert resolved['matched_provider'] == 'fixture'
    assert resolved['matched_alias_value'] == 'COMI.EGX'


def test_provider_symbol_code_does_not_resolve_under_a_different_provider(tmp_path):
    """An alias scoped to one provider must not resolve under an unrelated provider name."""
    database = db(tmp_path)
    repository = SecurityMasterRepository(database)
    repository.replace_provider_snapshot(provider='fixture', instruments=[
        instrument(uuid4(), provider='fixture', ticker='COMI', code='COMI.EGX'),
    ])
    with pytest.raises(KeyError):
        repository.resolve('COMI.EGX', provider='eodhd')


def test_two_providers_keep_independently_scoped_symbol_code_aliases(tmp_path):
    """Distinct provider snapshots for the same instrument must not collide or
    silently attribute one provider's alias to another."""
    database = db(tmp_path)
    repository = SecurityMasterRepository(database)
    fixture_id, eodhd_id = uuid4(), uuid4()
    repository.replace_provider_snapshot(provider='fixture', instruments=[
        instrument(fixture_id, provider='fixture', ticker='COMI', code='COMI.EGX'),
    ])
    repository.replace_provider_snapshot(provider='eodhd', instruments=[
        instrument(eodhd_id, provider='eodhd', ticker='COMI', code='COMI.CA'),
    ])
    fixture_resolved = repository.resolve('COMI.EGX', provider='fixture')
    eodhd_resolved = repository.resolve('COMI.CA', provider='eodhd')
    assert fixture_resolved['instrument_id'] == str(fixture_id)
    assert eodhd_resolved['instrument_id'] == str(eodhd_id)


def test_canonical_ticker_alias_remains_provider_agnostic(tmp_path):
    """The CANONICAL_TICKER alias intentionally uses the fixed pseudo-provider
    'canonical' regardless of the snapshot's source_provider."""
    database = db(tmp_path)
    repository = SecurityMasterRepository(database)
    repository.replace_provider_snapshot(provider='fixture', instruments=[
        instrument(uuid4(), provider='fixture', ticker='COMI', code='COMI.EGX'),
    ])
    resolved = repository.resolve('COMI', provider='canonical')
    assert resolved['matched_provider'] == 'canonical'


def test_list_equity_tickers_returns_distinct_sorted_canonical_tickers(tmp_path):
    """Two provider snapshots observing the same equity must not duplicate its
    ticker, and results must be deterministically ordered for scan scope use."""
    database = db(tmp_path)
    repository = SecurityMasterRepository(database)
    repository.replace_provider_snapshot(provider='fixture', instruments=[
        instrument(uuid4(), provider='fixture', ticker='FWRY', code='FWRY.EGX'),
        instrument(uuid4(), provider='fixture', ticker='COMI', code='COMI.EGX'),
    ])
    repository.replace_provider_snapshot(provider='eodhd', instruments=[
        instrument(uuid4(), provider='eodhd', ticker='COMI', code='COMI.CA'),
    ])
    assert repository.list_equity_tickers() == ('COMI', 'FWRY')


def test_list_equity_tickers_excludes_non_equity_instruments(tmp_path):
    database = db(tmp_path)
    repository = SecurityMasterRepository(database)
    index_instrument = instrument(uuid4(), provider='fixture', ticker='EGX30', code='EGX30.EGX')
    index_instrument = index_instrument.model_copy(update={'instrument_type': InstrumentType.INDEX})
    repository.replace_provider_snapshot(provider='fixture', instruments=[
        instrument(uuid4(), provider='fixture', ticker='COMI', code='COMI.EGX'),
        index_instrument,
    ])
    assert repository.list_equity_tickers() == ('COMI',)


def test_list_equity_tickers_empty_when_no_instruments_stored(tmp_path):
    database = db(tmp_path)
    repository = SecurityMasterRepository(database)
    assert repository.list_equity_tickers() == ()
