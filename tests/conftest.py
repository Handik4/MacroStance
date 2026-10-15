"""Shared helpers for MacroStance direct-mode tests.

Fixtures (direct_vm, direct_deploy, direct_alice, ...) come from the
genlayer-test pytest plugin. Direct mode executes the leader function only;
validator behaviour is exercised through `direct_vm.run_validator`.
"""

import json

import pytest

CONTRACT = "contracts/macro_stance.py"

ATTO = 10**18
BOND = ATTO // 10  # REPORTER_BOND == CHALLENGER_BOND == 0.1 GEN
KEEPER_REWARD = ATTO // 100
DAY = 24 * 3600
YEAR = 365 * DAY
T0 = "2026-10-01T00:00:00Z"

# Mirrors the deployed registry: every prefix is pinned to the fixture commit.
FIXTURE_COMMIT = "e2cd0c0216747473a90d5cd738e7675f97a9b6cd"
FEED_A = f"https://raw.githubusercontent.com/Handik4/MacroStance/{FIXTURE_COMMIT}/deployments/mock_feeds/a/"
FEED_B = f"https://cdn.jsdelivr.net/gh/Handik4/MacroStance@{FIXTURE_COMMIT}/deployments/mock_feeds/b/"
FEED_C = f"https://rawcdn.githack.com/Handik4/MacroStance/{FIXTURE_COMMIT}/deployments/mock_feeds/c/"
HOSTS = {
    "a": r"raw\.githubusercontent\.com",
    "b": r"cdn\.jsdelivr\.net",
    "c": r"rawcdn\.githack\.com",
}

DOVISH, NEUTRAL, HAWKISH = 0, 1, 2
TABLE = {
    DOVISH: (8000, 350, 1000),
    NEUTRAL: (7500, 550, 1500),
    HAWKISH: (6500, 900, 2500),
}

# Epoch fixtures: (epoch_id, rate_change_bps, cpi_yoy_bps, expected stance)
EP_DOVISH = ("2026-Q1-FOMC", -25, 190, DOVISH)
EP_NEUTRAL = ("2026-Q2-FOMC", 0, 250, NEUTRAL)
EP_HAWKISH = ("2026-Q3-FOMC", 25, 320, HAWKISH)
EP_Q4 = ("2026-Q4-FOMC", -25, 210, DOVISH)


def iso(ts: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_ts(epoch_id: str) -> int:
    """Deterministic publication time for a fixture epoch.

    Strictly increasing with the quarter, and always within 17 h of the
    quarter's first instant so it is never "in the future" for a test clock
    that sits at the start of the quarter (the contract allows 24 h of slack).
    Different tags in one quarter get different offsets; tests that need a
    specific order pass `ts=` explicitly.
    """
    from datetime import datetime, timezone

    year, quarter, tag = int(epoch_id[:4]), int(epoch_id[6]), epoch_id[8:]
    start = datetime(year, (quarter - 1) * 3 + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp()) + 3600 + (sum(map(ord, tag)) % 1000) * 60


def feed_doc(epoch_id, rate, cpi, status="CONFIRMED", ts=None) -> dict:
    return {"status": 200, "body": json.dumps(
        {
            "epoch_id": epoch_id,
            "status": status,
            "rate_change_bps": rate,
            "cpi_yoy_bps": cpi,
            "release_ts": default_ts(epoch_id) if ts is None else ts,
        }
    )}


def retracted_doc(epoch_id) -> dict:
    return {"status": 200, "body": json.dumps({"epoch_id": epoch_id, "status": "RETRACTED"})}


NOT_FOUND = {"status": 404, "body": "not found"}
OUTAGE = {"status": 503, "body": "unavailable"}


def set_feeds(vm, a, b, c):
    """Replace every feed mock. Each argument is a mock response dict."""
    vm.clear_mocks()
    for slot, resp in (("a", a), ("b", b), ("c", c)):
        vm.mock_web(rf"https://{HOSTS[slot]}/.*", resp)


def all_feeds(vm, ep, ts=None, **kw):
    doc = feed_doc(ep[0], ep[1], ep[2], ts=ts)
    set_feeds(vm, doc, doc, doc)


def new_contract(direct_deploy, direct_vm, sender):
    direct_vm.warp(T0)
    direct_vm.sender = sender
    return direct_deploy(CONTRACT, FEED_A, FEED_B, FEED_C)


def propose(c, vm, who, epoch_id, value=BOND):
    vm.sender = who
    vm.value = value
    try:
        return c.propose_epoch_sync(epoch_id)
    finally:
        vm.value = 0


def challenge(c, vm, who, epoch_id, value=BOND):
    vm.sender = who
    vm.value = value
    try:
        return c.challenge_staged_epoch(epoch_id)
    finally:
        vm.value = 0


def deposit(c, vm, who, amount):
    vm.sender = who
    vm.value = amount
    try:
        return c.deposit_collateral()
    finally:
        vm.value = 0


def stage_and_execute(c, vm, who, ep):
    """Drive one epoch all the way to an active stance."""
    all_feeds(vm, ep)
    propose(c, vm, who, ep[0])
    vm.warp(iso(_now(vm) + DAY + 1))
    vm.sender = who
    return c.execute_stance_transition(ep[0])


def _now(vm) -> int:
    from datetime import datetime, timezone

    return int(datetime.fromisoformat(vm._datetime.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())


def warp_by(vm, seconds: int):
    vm.warp(iso(_now(vm) + seconds))


def addr_hex(c, vm, who) -> str:
    prev = vm.sender
    vm.sender = who
    h = c.whoami()
    vm.sender = prev
    return h
