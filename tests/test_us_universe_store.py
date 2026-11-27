import copy
import fcntl
import json
import os
import stat
from datetime import timedelta
from hashlib import sha256

import pytest

from app.us.new_listing_retained import build_retained_new_listing_admissions
from app.us.universe_store import (
    MAX_OBSERVATION_LAG, OBSERVATION_CLOCK, RESULT_SCHEMA, RetainedUsUniverseError, UsUniverseStoreError,
    canonical_bytes, load_retained_us_universes, read_retained_us_universe, retain_us_universe)
from tests.test_us_new_listing_retained import AS_OF, BASELINE_AT, CURRENT_AT, pair, retain, summary


@pytest.fixture
def store(tmp_path):
    directory = tmp_path / "store"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    return directory


def keep(directory, universe, now):
    return retain_us_universe(str(directory), universe, now=now)


def snapshot(directory):
    return sorted((name, (directory / name).read_bytes()) for name in os.listdir(directory))


def refused(reason, function, *args, **kwargs):
    with pytest.raises(UsUniverseStoreError) as caught:
        function(*args, **kwargs)
    assert caught.value.reason == reason
    return caught.value


def test_retains_member_less_summary_write_once_and_loads_in_observation_order(tmp_path, store):
    baseline, current = pair(tmp_path / "data")
    assert "members" in current
    first = keep(store, current, CURRENT_AT + timedelta(minutes=5))
    second = keep(store, baseline, BASELINE_AT + timedelta(hours=1))
    assert first["schema_version"] == RESULT_SCHEMA and first["status"] == "RETAINED"
    assert second["status"] == "RETAINED" and second["retained_count"] == 2
    assert first["members_retained"] is False and first["observation_clock"] == OBSERVATION_CLOCK
    assert first["observed_at"] == (CURRENT_AT + timedelta(seconds=2)).isoformat()
    assert first["membership_interpretation"] == "NOT_ESTABLISHED"
    assert first["trading_eligibility"] == "NOT_ESTABLISHED" and first["trading_effect"] == "NONE"
    assert first["mode"] == "SHADOW" and first["live_money"] is False and first["execution_allowed"] is False

    path = store / f"{first['identity']}.json"
    payload = path.read_bytes()
    assert sha256(payload).hexdigest() == first["identity"]
    assert payload == canonical_bytes(summary(current))
    assert stat.S_IMODE(path.stat().st_mode) == 0o444

    loaded = load_retained_us_universes(str(store))
    assert [held.identity for held in loaded] == [second["identity"], first["identity"]]
    assert loaded[0].summary == summary(baseline) and loaded[1].summary == summary(current)
    assert loaded[0].observed_at == BASELINE_AT + timedelta(seconds=2)

    # Exact retry, with or without members, is idempotent and writes nothing.
    before = snapshot(store)
    again = keep(store, summary(current), CURRENT_AT + timedelta(minutes=6))
    assert again["status"] == "ALREADY_RETAINED" and again["identity"] == first["identity"]
    assert snapshot(store) == before


def test_retained_history_feeds_the_unchanged_new_listing_composition(tmp_path, store):
    data = tmp_path / "data"
    baseline, current = pair(data)
    keep(store, baseline, BASELINE_AT + timedelta(hours=1))
    keep(store, current, CURRENT_AT + timedelta(minutes=5))
    older, newer = load_retained_us_universes(str(store))
    from_store = build_retained_new_listing_admissions(data, older.summary, newer.summary, as_of=AS_OF)
    direct = build_retained_new_listing_admissions(data, summary(baseline), summary(current), as_of=AS_OF)
    assert from_store == direct
    assert [m["ticker"] for m in from_store["admissions"]["members"]] == ["NEWCO"]


def test_clock_refuses_future_and_backdated_provenance(tmp_path, store):
    universe = retain(tmp_path / "data", CURRENT_AT)
    refused("OBSERVED_IN_FUTURE", keep, store, universe, CURRENT_AT + timedelta(seconds=1))
    refused("OBSERVATION_BACKDATED", keep, store, universe, CURRENT_AT + MAX_OBSERVATION_LAG + timedelta(seconds=1))
    assert snapshot(store) == []
    held = keep(store, universe, CURRENT_AT + MAX_OBSERVATION_LAG)
    assert held["status"] == "RETAINED"
    # An exact retry after the window is still the same evidence, not a new observation.
    late = keep(store, universe, CURRENT_AT + timedelta(days=3))
    assert late["status"] == "ALREADY_RETAINED"
    with pytest.raises(TypeError):
        keep(store, universe, CURRENT_AT.replace(tzinfo=None))


def test_same_provenance_and_size_with_different_summary_is_a_conflict(tmp_path, store):
    data = tmp_path / "data"
    universe = retain(data, CURRENT_AT, size=5)
    now = CURRENT_AT + timedelta(minutes=5)
    keep(store, universe, now)
    altered = summary(universe)
    altered["rule"] = "a different rule over the same retained bytes"
    before = snapshot(store)
    refused("PROVENANCE_CONFLICT", keep, store, altered, now)
    assert snapshot(store) == before
    # A different declared size over the same retrieval is a different, legitimate summary.
    wider = retain(data, CURRENT_AT, size=6)
    assert wider["provenance"] == universe["provenance"]
    assert keep(store, wider, now)["status"] == "RETAINED"


def _mutations():
    return {
        "version": lambda s: s.update(version="US-UNIVERSE-v2"),
        "extra_field": lambda s: s.update(note="x"),
        "missing_field": lambda s: s.pop("rule"),
        "bool_size": lambda s: s.update(size=True),
        "count_mismatch": lambda s: s.update(eligible=s["eligible"] + 1),
        "floor_missing": lambda s: s.update(floor_avg_dollar_volume_30d=None),
        "floor_decimal": lambda s: s.update(floor_avg_dollar_volume_30d="1.5"),
        "excluded_keys": lambda s: s["excluded"].update(OTHER=0),
        "provenance_missing": lambda s: s["provenance"].pop(),
        "provenance_duplicate": lambda s: s["provenance"].__setitem__(1, dict(s["provenance"][0])),
        "provenance_url": lambda s: s["provenance"][2].update(url="https://example.invalid/scan"),
        "digest_upper": lambda s: s["provenance"][0].update(raw_sha256=s["provenance"][0]["raw_sha256"].upper()),
        "naive_time": lambda s: s["provenance"][0].update(retrieved_at="2026-11-20T04:15:00"),
        "unknown_header": lambda s: s["provenance"][0].update(server="x"),
        "nan": lambda s: s.update(size=float("nan")),
    }


@pytest.mark.parametrize("name", sorted(_mutations()))
def test_malformed_summaries_are_rejected_before_any_write(tmp_path, store, name):
    altered = summary(retain(tmp_path / "data", CURRENT_AT))
    _mutations()[name](altered)
    refused("RECORD_REJECTED", keep, store, altered, CURRENT_AT + timedelta(minutes=5))
    assert snapshot(store) == []


def test_reader_requires_exact_canonical_bytes(tmp_path):
    document = summary(retain(tmp_path / "data", CURRENT_AT))
    payload = canonical_bytes(document)
    held = read_retained_us_universe(payload, expected_sha256=sha256(payload).hexdigest())
    assert held.summary == document and held.observed_at == CURRENT_AT + timedelta(seconds=2)
    pretty = json.dumps(document, sort_keys=True, indent=1).encode()
    duplicate = payload[:-1] + b',"size":5}'
    for bad in (pretty, duplicate, b"[]", b""):
        with pytest.raises(RetainedUsUniverseError):
            read_retained_us_universe(bad, expected_sha256=sha256(bad).hexdigest())
    with pytest.raises(RetainedUsUniverseError):
        read_retained_us_universe(payload, expected_sha256="0" * 64)


def test_damaged_or_unexpected_entries_fail_closed(tmp_path, store):
    data = tmp_path / "data"
    now = CURRENT_AT + timedelta(minutes=5)
    identity = keep(store, retain(data, CURRENT_AT), now)["identity"]
    (store / ".publish-1-0123456789abcdef.tmp").write_bytes(b"partial")
    assert len(load_retained_us_universes(str(store))) == 1

    path = store / f"{identity}.json"
    original = path.read_bytes()
    path.chmod(0o644)
    path.write_bytes(original.replace(b'"size":5', b'"size":6'))
    refused("STORE_INVALID", load_retained_us_universes, str(store))
    before = snapshot(store)
    refused("STORE_INVALID", keep, store, retain(data, CURRENT_AT, size=7), now)
    assert snapshot(store) == before

    path.write_bytes(original)
    (store / "notes.txt").write_text("x")
    refused("STORE_INVALID", load_retained_us_universes, str(store))


def test_directory_must_be_absolute_private_and_unlocked(tmp_path, store):
    universe = retain(tmp_path / "data", CURRENT_AT)
    now = CURRENT_AT + timedelta(minutes=5)
    refused("CONFIG_INVALID", retain_us_universe, "relative/store", universe, now=now)
    refused("DIRECTORY_UNAVAILABLE", load_retained_us_universes, str(tmp_path / "missing"))
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    refused("DIRECTORY_UNSAFE", keep, shared, universe, now)
    refused("DIRECTORY_UNSAFE", load_retained_us_universes, str(shared))
    link = tmp_path / "link"
    link.symlink_to(store)
    refused("DIRECTORY_UNAVAILABLE", keep, link, universe, now)

    holder = os.open(str(store), os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)
        refused("STORE_BUSY", keep, store, universe, now)
    finally:
        os.close(holder)
    assert snapshot(store) == []
    assert keep(store, copy.deepcopy(universe), now)["status"] == "RETAINED"
