"""Deciding which bot instances should be running.

One market per instance is how the measured results were produced, so
changing the traded universe means starting and stopping systemd units -
five commands typed by hand, at least one of which is a loop over service
names. Typed by hand it is also where the two quiet mistakes live:

* Stopping an instance that still holds a position abandons it. In paper
  mode the trade never closes and never reaches the trade journal, so the
  market's recorded result silently omits it. In live mode the position is
  real, its stop rests on the exchange, and nothing trails it any more.
* A stopped instance's entry stays on the shared portfolio board. A flat
  one is harmless (an entry with no positions claims no slot), but one
  that held a position claims a slot no living process will ever release
  until it goes stale.

So the decision is worth making in code that can be tested, separately
from the ``systemctl`` calls that carry it out. Nothing here touches
systemd or the filesystem beyond reading; :mod:`scripts.set_markets` is
the part with consequences.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

from ict_bot.execution.state import peek_open_positions

DEFAULT_BOARD = "state/portfolio.json"


@dataclass(frozen=True)
class Instance:
    """One systemd instance, as it exists on disk right now."""

    name: str                  # "trend-btc" - the %i of ict-bot@%i.service
    config: str                # config/config-trend-btc.yaml
    example: str | None        # the shipped template it can be created from
    state_file: str
    running: bool
    bot_id: str                # what it publishes to the portfolio board as
    board_file: str
    holds: list[str] = field(default_factory=list)  # e.g. ["long BTC/USDT"]

    @property
    def service(self) -> str:
        return f"ict-bot@{self.name}.service"


@dataclass(frozen=True)
class Plan:
    start: list[Instance] = field(default_factory=list)
    keep: list[Instance] = field(default_factory=list)
    stop: list[Instance] = field(default_factory=list)
    holding: list[Instance] = field(default_factory=list)  # wanted gone, but not flat
    unknown: list[str] = field(default_factory=list)       # asked for, no config ships

    @property
    def is_noop(self) -> bool:
        return not (self.start or self.stop)


def _settings(config: str) -> dict:
    """The few keys this module needs, read straight from the YAML.

    Not ``load_config``: that validates the whole file and raises on a
    config this tool may be about to stop anyway, and a stop should not
    depend on the stopped thing still parsing.
    """
    try:
        with open(config) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def discover(names: list[str], running: set[str], root: str = ".") -> dict[str, Instance]:
    """Look up each named instance's configs, state and running status."""
    found = {}
    for name in names:
        stem = f"config-{name}"
        config = os.path.join(root, "config", f"{stem}.yaml")
        example = config.replace(".yaml", ".example.yaml")
        raw = _settings(config)
        live = raw.get("live") or {}
        portfolio = raw.get("portfolio") or {}
        state_file = live.get("state_file") or os.path.join(root, "state", f"{stem}-state.json")
        holds = [f"{p.get('side', '?')} {p.get('symbol', '?')}"
                 for p in peek_open_positions(state_file)]
        found[name] = Instance(
            name=name,
            config=config,
            example=example if os.path.exists(example) else None,
            state_file=state_file,
            running=name in running,
            # Mirrors _default_bot_id in config.py, which uses the config
            # filename's stem unless the file names one itself.
            bot_id=str(live.get("bot_id") or stem),
            board_file=os.path.join(root, portfolio.get("board_file") or DEFAULT_BOARD),
            holds=holds,
        )
    return found


def plan(wanted: list[str], current: list[Instance], force: bool = False) -> Plan:
    """What to start and stop to end up running exactly ``wanted``.

    ``current`` is every instance that could be involved - the wanted ones
    and whatever is running now. An instance that is running, no longer
    wanted, and still holds a position is reported under ``holding`` and
    left alone unless ``force`` says otherwise: the position is the reason
    to look before typing, not a detail to log afterwards.
    """
    by_name = {i.name: i for i in current}
    result = Plan()
    for name in wanted:
        instance = by_name.get(name)
        if instance is None or (instance.example is None and not os.path.exists(instance.config)):
            result.unknown.append(name)
        elif instance.running:
            result.keep.append(instance)
        else:
            result.start.append(instance)

    for instance in current:
        if instance.name in wanted or not instance.running:
            continue
        if instance.holds and not force:
            result.holding.append(instance)
        else:
            result.stop.append(instance)
    return result
