#!/usr/bin/env python3
"""Make the running trend instances match a list of markets.

    sudo .venv/bin/python scripts/set_markets.py btc eth sol xrp doge

Starts what is missing (copying the shipped example config on first use),
stops what is no longer wanted, and removes each stopped instance from the
shared portfolio board. Prints the plan and asks before doing any of it.

It refuses to stop an instance that still holds a position, because that
strands the position: in paper mode the trade never closes and never
reaches the trade journal, so that market's recorded result quietly omits
it; in live mode the position is real and nothing trails its stop any
more. Wait for the position to exit, or pass --force if you have decided
to abandon it.

Only ``trend-*`` instances are considered. An ICT instance, or anything
else you started by hand, is left alone.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ict_bot.execution.portfolio import PortfolioBoard  # noqa: E402
from ict_bot.instances import Instance, discover, plan  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIX = "trend-"


def running_instances() -> set[str]:
    """Every ict-bot@<name> instance systemd currently has active."""
    try:
        out = subprocess.run(
            ["systemctl", "list-units", "--all", "--no-legend", "--plain", "ict-bot@*.service"],
            capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"Could not ask systemd what is running: {exc}")
    return parse_units(out)


def parse_units(listing: str) -> set[str]:
    """Instance names from `systemctl list-units`, active ones only.

    Columns are UNIT LOAD ACTIVE SUB DESCRIPTION. ``--all`` also lists
    loaded-but-inactive units, and a failed unit is prefixed with a bullet
    even under ``--plain``, so neither the presence of a line nor its first
    field is the answer on its own.
    """
    names = set()
    for line in listing.splitlines():
        fields = line.replace("\u25cf", " ").replace("*", " ").split()
        if len(fields) < 3:
            continue
        unit, _load, active = fields[0], fields[1], fields[2]
        if not (unit.startswith("ict-bot@") and unit.endswith(".service")):
            continue
        if active == "active":
            names.add(unit[len("ict-bot@"):-len(".service")])
    return names


def systemctl(*args: str, check: bool = True) -> None:
    subprocess.run(["systemctl", *args], check=check)


def describe(instances: list[Instance]) -> str:
    return ", ".join(i.name for i in instances) or "-"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("markets", nargs="+",
                        help="market suffixes, e.g. btc eth sol xrp doge")
    parser.add_argument("--force", action="store_true",
                        help="stop instances even if they still hold a position")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    parser.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args()

    wanted = [m if m.startswith(PREFIX) else PREFIX + m for m in args.markets]
    running = {n for n in running_instances() if n.startswith(PREFIX)}
    known = discover(sorted(set(wanted) | running), running, root=ROOT)
    todo = plan(wanted, list(known.values()), force=args.force)

    if todo.unknown:
        return fail(f"No config ships for: {', '.join(todo.unknown)}.\n"
                    f"Expected config/config-<name>.example.yaml - see config/ for what exists.")

    print(f"already running : {describe(todo.keep)}")
    print(f"start           : {describe(todo.start)}")
    print(f"stop            : {describe(todo.stop)}")
    for instance in todo.holding:
        print(f"NOT stopping {instance.name}: still holds {', '.join(instance.holds)}")
    if todo.holding:
        print("Stopping it would abandon that position. Wait for it to exit, or use --force.")

    if todo.is_noop:
        print("Nothing to do.")
        return 1 if todo.holding else 0
    if args.dry_run:
        return 0
    if not args.yes and input("Apply? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing changed.")
        return 0

    for instance in todo.start:
        if not os.path.exists(instance.config):
            shutil.copy(instance.example, instance.config)
            print(f"created {os.path.relpath(instance.config, ROOT)}")
        systemctl("enable", "--now", instance.service)

    for instance in todo.stop:
        systemctl("disable", "--now", instance.service)
        # Only once the unit is really down: releasing a slot a live
        # process still republishes just brings the entry back.
        PortfolioBoard(instance.board_file).release(instance.bot_id)

    print()
    # Inactive units make this exit non-zero, which here is the expected
    # outcome of a stop, not a failure.
    systemctl("status", "ict-bot*", "--no-pager", check=False)
    return 1 if todo.holding else 0


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
