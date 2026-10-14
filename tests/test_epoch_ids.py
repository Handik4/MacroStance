"""Epoch id grammar: `YYYY-Qn-TAG` is also what makes ids URL-safe."""

import pytest

from conftest import *  # noqa: F401,F403


@pytest.fixture()
def c(direct_vm, direct_deploy, direct_alice):
    return new_contract(direct_deploy, direct_vm, direct_alice)


VALID = [
    ("2026-Q4-FOMC", 2026 * 4 + 3),
    ("2026-Q1-FOMC", 2026 * 4 + 0),
    ("2000-Q1-AB", 2000 * 4),
    ("2200-Q4-ABCDEFGHIJKL", 2200 * 4 + 3),
    ("2026-Q2-CPI", 2026 * 4 + 1),
    ("2026-Q3-X1", 2026 * 4 + 2),
    ("2026-Q3-0000", 2026 * 4 + 2),
]


@pytest.mark.parametrize("epoch_id,seq", VALID)
def test_valid_ids_parse_to_monotonic_sequence(c, epoch_id, seq):
    assert c.parse_epoch_id(epoch_id) == seq


INVALID = [
    "", "2026", "2026-Q4", "2026-Q4-", "2026-Q5-FOMC", "2026-Q0-FOMC", "2026-q4-FOMC",
    "26-Q4-FOMC", "20260-Q4-FOMC", "1999-Q4-FOMC", "2201-Q1-FOMC", "2026-Q4-F",
    "2026-Q4-fomc", "2026-Q4-FOMC-X", "2026-Q4-ABCDEFGHIJKLM", "2026_Q4_FOMC",
    "2026-Q4-FO MC", "2026-Q4-FO/MC", "2026-Q4-../..", "../2026-Q4-FOMC",
    "2026-Q4-FOMC.json", "2026-Q4-FOMC?x=1", "2026-Q4-FOMC#a", "2026-Q4-FOM%43",
    "2026-Q4-FOMC\n", " 2026-Q4-FOMC", "20X6-Q4-FOMC", "2026-QX-FOMC",
    "2026-Q4-ФО", "a" * 40,
]


@pytest.mark.parametrize("epoch_id", INVALID)
def test_invalid_ids_are_rejected(c, epoch_id):
    assert c.parse_epoch_id(epoch_id) == -1


@pytest.mark.parametrize("epoch_id", ["", "../x", "2026-Q4-fomc", "2026-Q5-FOMC"])
def test_propose_rejects_invalid_id_before_touching_the_network(direct_vm, c, direct_alice, epoch_id):
    direct_vm.clear_mocks()  # no mocks at all: any fetch would be an outage, not a revert
    direct_vm.sender = direct_alice
    direct_vm.value = BOND
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_ID"):
        c.propose_epoch_sync(epoch_id)
    direct_vm.value = 0


def test_ordering_is_by_quarter_not_lexicographic(c):
    assert c.parse_epoch_id("2026-Q4-AAAA") > c.parse_epoch_id("2026-Q3-ZZZZ")
    assert c.parse_epoch_id("2027-Q1-AAAA") > c.parse_epoch_id("2026-Q4-ZZZZ")
