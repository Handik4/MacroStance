"""The committed mock feeds must produce exactly the documented outcomes."""

import json
from pathlib import Path

import pytest

from conftest import *  # noqa: F401,F403

ROOT = Path(__file__).resolve().parent.parent / "deployments" / "mock_feeds"
EXPECT = {
    "2026-Q1-FOMC": (1, DOVISH, 3),
    "2026-Q2-FOMC": (1, NEUTRAL, 3),
    "2026-Q3-FOMC": (1, HAWKISH, 3),
    "2026-Q4-FOMC": (1, DOVISH, 3),
    "2026-Q4-REVISE": (1, HAWKISH, 3),
    "2026-Q4-LAG": (1, HAWKISH, 2),
    "2026-Q4-SPLIT": (0, 0, 3),
    "2026-Q4-RETRACT": (0, 0, 3),
    "2026-Q4-ONEDOWN": (0, 0, 3),  # 1 confirmation + two clean 404s
}


@pytest.fixture()
def c(direct_vm, direct_deploy, direct_alice):
    return new_contract(direct_deploy, direct_vm, direct_alice)


def response(slot, epoch):
    f = ROOT / slot / f"{epoch}.json"
    if not f.exists():
        return {"status": 404, "body": ""}
    return {"status": 200, "body": f.read_text()}


@pytest.mark.parametrize("epoch", sorted(EXPECT))
def test_committed_feed_files_resolve_as_documented(direct_vm, c, direct_bob, epoch):
    # The fixtures are dated up to 2026-10-14; a release may not be dated in
    # the future of the block clock, so the demo clock is the day after.
    direct_vm.warp("2026-10-15T00:00:00Z")
    set_feeds(direct_vm, *(response(s, epoch) for s in "abc"))
    res = propose(c, direct_vm, direct_bob, epoch)
    verdict, stance, count = EXPECT[epoch]
    assert res["verdict"] == verdict
    if verdict == 1:
        assert res["stance"] == stance
    # VERIFIED reports the matching group size; otherwise clean responses
    assert res["source_count"] == count


def test_every_feed_file_is_valid_json_with_matching_epoch_id():
    files = sorted(ROOT.glob("*/*.json"))
    assert len(files) >= 20
    for f in files:
        doc = json.loads(f.read_text())
        assert doc["epoch_id"] == f.stem and doc["status"] in ("CONFIRMED", "RETRACTED")


def test_three_slot_directories_exist():
    assert {p.name for p in ROOT.iterdir() if p.is_dir()} == {"a", "b", "c"}
