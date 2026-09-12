"""Artificial software fixtures only; no authentic positions or performance."""
import json
from datetime import timedelta
from decimal import Decimal

import pytest

from app.paper import shadow_fills, shadow_positions
from tests.test_shadow_fills import prepared


def setup_position(tmp_path, monkeypatch, **kwargs):
    *args, now = prepared(tmp_path, monkeypatch, **kwargs)
    fill_path = shadow_fills.append_fill_event(tmp_path, *args)
    monkeypatch.setattr(shadow_positions, "_now", lambda: now)
    return args, now, fill_path


def test_position_preserves_fill_and_frozen_exits_without_performance(tmp_path, monkeypatch):
    args, _, fill_path = setup_position(tmp_path, monkeypatch)
    path = shadow_positions.append_position_open_event(tmp_path, *args)
    event = shadow_positions.audit_position_open_event(tmp_path, *args)
    assert event == json.loads(path.read_bytes())
    assert event["entry"] == json.loads(fill_path.read_bytes())["fill"]
    assert event["initial_stop"] == str(args[0].candidates[0].stop)
    assert event["initial_targets"] == [str(v) for v in args[0].candidates[0].targets]
    assert event["label"] == "EXPERIMENTAL / PAPER ONLY"
    assert event["scoring"] == "NOT SCORED"
    assert event["portfolio_status"] == "SHARED CAPITAL NOT ALLOCATED"
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        shadow_positions.append_position_open_event(tmp_path, *args)
    assert path.read_bytes() == original


def test_no_fill_cannot_open_position(tmp_path, monkeypatch):
    args, _, _ = setup_position(tmp_path, monkeypatch, participation=Decimal("0.0009"))
    with pytest.raises(ValueError, match="requires an authenticated simulated fill"):
        shadow_positions.append_position_open_event(tmp_path, *args)
    assert not (tmp_path / "position-open-events").exists()


@pytest.mark.parametrize("change", ["entry", "stop", "extra", "duplicate", "past", "future"])
def test_audit_rejects_tampering_and_bad_clocks(tmp_path, monkeypatch, change):
    args, now, _ = setup_position(tmp_path, monkeypatch)
    path = shadow_positions.append_position_open_event(tmp_path, *args)
    event = json.loads(path.read_bytes())
    if change == "entry":
        event["entry"]["quantity"] += 1
    elif change == "stop":
        event["initial_stop"] = "1"
    elif change == "extra":
        event["nav"] = "100"
    elif change in ("past", "future"):
        event["recorded_at"] = (now + timedelta(seconds=-1 if change == "past" else 1)).isoformat()
    if change == "duplicate":
        path.write_text('{"scoring":"SCORED",' + json.dumps(event)[1:])
    else:
        path.write_text(json.dumps(event))
    with pytest.raises(ValueError):
        shadow_positions.audit_position_open_event(tmp_path, *args)


def test_position_reaudits_upstream_fill(tmp_path, monkeypatch):
    args, _, fill_path = setup_position(tmp_path, monkeypatch)
    shadow_positions.append_position_open_event(tmp_path, *args)
    fill = json.loads(fill_path.read_bytes())
    fill["fill"]["fill_price"] = "1"
    fill_path.write_text(json.dumps(fill))
    with pytest.raises(ValueError, match="fill event does not bind"):
        shadow_positions.audit_position_open_event(tmp_path, *args)


def test_position_requires_durable_fill(tmp_path, monkeypatch):
    args, _, fill_path = setup_position(tmp_path, monkeypatch)
    fill_path.unlink()
    with pytest.raises(FileNotFoundError):
        shadow_positions.append_position_open_event(tmp_path, *args)


def test_publication_rejects_backdating_and_rollback(tmp_path, monkeypatch):
    args, now, _ = setup_position(tmp_path, monkeypatch)
    monkeypatch.setattr(shadow_positions, "_now", lambda: now - timedelta(seconds=1))
    with pytest.raises(ValueError, match="precedes fill"):
        shadow_positions.append_position_open_event(tmp_path, *args)
    clocks = iter((now, now - timedelta(seconds=1)))
    monkeypatch.setattr(shadow_positions, "_now", lambda: next(clocks))
    with pytest.raises(ValueError, match="clock rollback"):
        shadow_positions.append_position_open_event(tmp_path, *args)
    assert not list((tmp_path / "position-open-events").glob("*.json"))
