"""Deciding which instances to start and stop.

Changing the traded universe is systemd work, and two mistakes in it are
silent: stopping an instance that still holds a position abandons the
trade (it never closes, so the journal never records it), and a stopped
instance keeps claiming its slot on the shared portfolio board.
"""
from __future__ import annotations

import json

from ict_bot.instances import discover, plan


def _write(tmp_path, name: str, *, example=True, config=False, positions=()):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "state").mkdir(exist_ok=True)
    body = "market:\n  symbol: \"BTC/USDT\"\n"
    if example:
        (tmp_path / "config" / f"config-{name}.example.yaml").write_text(body)
    if config:
        (tmp_path / "config" / f"config-{name}.yaml").write_text(body)
    if positions:
        state = {"version": 1, "positions": [{"symbol": s, "side": "long"} for s in positions]}
        (tmp_path / "state" / f"config-{name}-state.json").write_text(json.dumps(state))


def test_a_wanted_instance_that_is_not_running_gets_started(tmp_path):
    _write(tmp_path, "trend-doge")
    found = discover(["trend-doge"], running=set(), root=str(tmp_path))

    todo = plan(["trend-doge"], list(found.values()))

    assert [i.name for i in todo.start] == ["trend-doge"]
    assert not todo.stop and not todo.unknown


def test_a_wanted_instance_already_running_is_left_alone(tmp_path):
    _write(tmp_path, "trend-btc", config=True)
    found = discover(["trend-btc"], running={"trend-btc"}, root=str(tmp_path))

    todo = plan(["trend-btc"], list(found.values()))

    assert [i.name for i in todo.keep] == ["trend-btc"]
    assert todo.is_noop


def test_a_running_instance_that_is_no_longer_wanted_gets_stopped(tmp_path):
    _write(tmp_path, "trend-btc", config=True)
    _write(tmp_path, "trend-ltc", config=True)
    found = discover(["trend-btc", "trend-ltc"], running={"trend-btc", "trend-ltc"}, root=str(tmp_path))

    todo = plan(["trend-btc"], list(found.values()))

    assert [i.name for i in todo.stop] == ["trend-ltc"]


def test_an_instance_holding_a_position_is_not_stopped(tmp_path):
    _write(tmp_path, "trend-ltc", config=True, positions=["LTC/USDT"])
    found = discover(["trend-ltc"], running={"trend-ltc"}, root=str(tmp_path))

    todo = plan([], list(found.values()))

    assert not todo.stop
    assert [i.name for i in todo.holding] == ["trend-ltc"]
    assert found["trend-ltc"].holds == ["long LTC/USDT"]


def test_force_stops_it_anyway(tmp_path):
    _write(tmp_path, "trend-ltc", config=True, positions=["LTC/USDT"])
    found = discover(["trend-ltc"], running={"trend-ltc"}, root=str(tmp_path))

    todo = plan([], list(found.values()), force=True)

    assert [i.name for i in todo.stop] == ["trend-ltc"]
    assert not todo.holding


def test_a_market_with_no_config_is_reported_not_guessed(tmp_path):
    found = discover(["trend-nope"], running=set(), root=str(tmp_path))

    todo = plan(["trend-nope"], list(found.values()))

    assert todo.unknown == ["trend-nope"]
    assert not todo.start


def test_a_stopped_instance_is_not_restarted_just_because_it_is_unwanted(tmp_path):
    _write(tmp_path, "trend-ada", config=True)
    found = discover(["trend-ada"], running=set(), root=str(tmp_path))

    todo = plan([], list(found.values()))

    assert todo.is_noop and not todo.holding


def test_the_board_id_and_file_come_from_the_config_when_it_sets_them(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config-trend-btc.yaml").write_text(
        "live:\n  bot_id: \"my-btc\"\nportfolio:\n  board_file: \"state/shared.json\"\n")
    instance = discover(["trend-btc"], running=set(), root=str(tmp_path))["trend-btc"]

    assert instance.bot_id == "my-btc"
    assert instance.board_file.endswith("state/shared.json")


def test_an_unparsable_config_does_not_stop_it_being_managed(tmp_path):
    # A stop must not depend on the stopped thing still parsing.
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config-trend-btc.yaml").write_text("{ this is: not: valid")
    instance = discover(["trend-btc"], running={"trend-btc"}, root=str(tmp_path))["trend-btc"]

    assert instance.bot_id == "config-trend-btc"
    assert plan([], [instance]).stop == [instance]


def _script():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "set_markets.py"
    spec = importlib.util.spec_from_file_location("set_markets", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_active_units_count_as_running():
    # --all also lists loaded-but-dead units, and a failed one carries a
    # bullet even under --plain; neither is "running".
    listing = (
        "ict-bot@trend-btc.service loaded active running ICT trading bot - instance trend-btc\n"
        "ict-bot@trend-ltc.service loaded inactive dead ICT trading bot - instance trend-ltc\n"
        "● ict-bot@trend-ada.service loaded failed failed ICT trading bot - instance trend-ada\n"
        "ict-bot@ethusdt.service loaded active running ICT trading bot - instance ethusdt\n"
        "\n"
    )

    assert _script().parse_units(listing) == {"trend-btc", "ethusdt"}
