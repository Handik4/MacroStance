"""Value accounting: every wei is in exactly one bucket."""

import pytest

from conftest import *  # noqa: F401,F403


@pytest.fixture()
def c(direct_vm, direct_deploy, direct_alice):
    return new_contract(direct_deploy, direct_vm, direct_alice)


def owed(c):
    o = c.get_protocol_overview()
    return sum(int(o[k]) for k in ("total_escrow", "total_claimable", "locked_protocol_reserve", "total_collateral"))


def test_buckets_sum_to_everything_ever_paid_in(direct_vm, c, direct_alice, direct_bob, direct_charlie):
    paid = 0
    # a verified proposal (escrow), a rejected one (reserve), a QF (claimable), collateral
    all_feeds(direct_vm, EP_HAWKISH)
    propose(c, direct_vm, direct_bob, EP_HAWKISH[0], 2 * BOND)
    paid += 2 * BOND
    assert owed(c) == paid
    deposit(c, direct_vm, direct_charlie, 5 * ATTO)
    paid += 5 * ATTO
    assert owed(c) == paid
    warp_by(direct_vm, DAY)
    c.execute_stance_transition(EP_HAWKISH[0])  # escrow -> claimable
    assert owed(c) == paid
    set_feeds(direct_vm, NOT_FOUND, NOT_FOUND, NOT_FOUND)
    propose(c, direct_vm, direct_bob, "2026-Q4-FOMC")  # forfeited
    paid += BOND
    assert owed(c) == paid
    set_feeds(direct_vm, OUTAGE, OUTAGE, OUTAGE)
    propose(c, direct_vm, direct_bob, "2026-Q4-CPI")  # refunded to claimable
    paid += BOND
    assert owed(c) == paid


def test_claim_and_withdraw_reduce_the_matching_bucket(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, 4 * ATTO)
    direct_vm.sender = direct_bob
    c.withdraw_collateral(ATTO)
    assert c.get_protocol_overview()["total_collateral"] == str(3 * ATTO)
    set_feeds(direct_vm, OUTAGE, OUTAGE, OUTAGE)
    propose(c, direct_vm, direct_bob, "2026-Q4-FOMC")
    assert c.get_protocol_overview()["total_claimable"] == str(BOND)
    direct_vm.sender = direct_bob
    c.claim_payout()
    assert c.get_protocol_overview()["total_claimable"] == "0"


def test_failed_proposal_with_bad_id_moves_no_value(direct_vm, c, direct_bob):
    before = owed(c)
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_ID"):
        propose(c, direct_vm, direct_bob, "bogus")
    assert owed(c) == before == 0


def test_vault_never_touches_bond_buckets(direct_vm, c, direct_alice, direct_bob):
    all_feeds(direct_vm, EP_HAWKISH)
    propose(c, direct_vm, direct_bob, EP_HAWKISH[0])
    deposit(c, direct_vm, direct_alice, ATTO)
    direct_vm.sender = direct_alice
    c.borrow_against_collateral(ATTO // 2)
    c.repay(ATTO)
    c.withdraw_collateral(ATTO)
    o = c.get_protocol_overview()
    assert (o["total_escrow"], o["total_claimable"], o["locked_protocol_reserve"]) == (str(BOND), "0", "0")
