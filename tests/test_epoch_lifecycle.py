"""Invariant 4: bonds, staging, 24h activation timelock, step-change guard."""

import pytest

from conftest import *  # noqa: F401,F403
from conftest import _now

EP = EP_HAWKISH


@pytest.fixture()
def c(direct_vm, direct_deploy, direct_alice):
    return new_contract(direct_deploy, direct_vm, direct_alice)


@pytest.fixture()
def staged(direct_vm, c, direct_alice, direct_bob):
    all_feeds(direct_vm, EP)
    propose(c, direct_vm, direct_bob, EP[0])
    return c


def ov(c):
    return c.get_protocol_overview()


# ------------------------------------------------------------------- bonds
@pytest.mark.parametrize("value", [0, 1, BOND - 1])
def test_bond_below_minimum_reverts(direct_vm, c, direct_bob, value):
    all_feeds(direct_vm, EP)
    with direct_vm.expect_revert("ERR_INSUFFICIENT_BOND"):
        propose(c, direct_vm, direct_bob, EP[0], value)


def test_exact_minimum_bond_is_accepted(direct_vm, c, direct_bob):
    all_feeds(direct_vm, EP)
    assert propose(c, direct_vm, direct_bob, EP[0], BOND)["bond"] == str(BOND)


def test_bond_above_cap_reverts(direct_vm, c, direct_bob):
    all_feeds(direct_vm, EP)
    with direct_vm.expect_revert("ERR_INSUFFICIENT_BOND"):
        propose(c, direct_vm, direct_bob, EP[0], 10 * ATTO + 1)


def test_larger_bond_is_escrowed_and_refunded_in_full(direct_vm, c, direct_bob):
    all_feeds(direct_vm, EP)
    big = 3 * ATTO
    assert propose(c, direct_vm, direct_bob, EP[0], big)["bond"] == str(big)
    warp_by(direct_vm, DAY)
    direct_vm.sender = direct_bob
    assert c.execute_stance_transition(EP[0])["reporter_refund"] == str(big)


# --------------------------------------------------------------- staging
def test_verified_epoch_is_staged_with_24h_activation(staged, direct_vm):
    e = staged.get_epoch(EP[0])
    assert e["status"] == "STAGED" and e["activates_at"] - e["staged_at"] == DAY
    assert ov(staged)["pending_epoch"] == EP[0]


def test_active_parameters_do_not_change_during_timelock(staged, direct_vm):
    warp_by(direct_vm, DAY - 1)
    assert staged.get_active_params()["stance"] == NEUTRAL


def test_execute_before_timelock_reverts(staged, direct_vm, direct_bob):
    warp_by(direct_vm, DAY - 1)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("ERR_TIMELOCK_ACTIVE"):
        staged.execute_stance_transition(EP[0])


def test_execute_at_exact_timelock_succeeds(staged, direct_vm, direct_bob):
    warp_by(direct_vm, DAY)
    direct_vm.sender = direct_bob
    assert staged.execute_stance_transition(EP[0])["stance"] == HAWKISH


def test_execute_applies_stance_and_clears_pending(staged, direct_vm, direct_bob):
    warp_by(direct_vm, DAY)
    staged.execute_stance_transition(EP[0])
    o = ov(staged)
    assert o["active_stance"] == HAWKISH and o["active_epoch_id"] == EP[0]
    assert o["pending_epoch"] == "" and o["last_executed_seq"] == 2026 * 4 + 2
    assert staged.get_epoch(EP[0])["status"] == "EXECUTED"


def test_execute_is_permissionless_and_credits_the_reporter(staged, direct_vm, direct_bob, direct_charlie):
    warp_by(direct_vm, DAY)
    direct_vm.sender = direct_charlie
    staged.execute_stance_transition(EP[0])
    assert staged.claimable_of(addr_hex(staged, direct_vm, direct_bob)) == str(BOND)
    assert staged.claimable_of(addr_hex(staged, direct_vm, direct_charlie)) == "0"


def test_execute_twice_reverts(staged, direct_vm, direct_bob):
    warp_by(direct_vm, DAY)
    staged.execute_stance_transition(EP[0])
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_STATE"):
        staged.execute_stance_transition(EP[0])


def test_execute_unknown_epoch_reverts(direct_vm, c):
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_STATE"):
        c.execute_stance_transition("2026-Q3-FOMC")


def test_execute_of_unstaged_failure_states_reverts(direct_vm, c, direct_bob):
    set_feeds(direct_vm, NOT_FOUND, NOT_FOUND, NOT_FOUND)
    propose(c, direct_vm, direct_bob, EP[0])
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_STATE"):
        c.execute_stance_transition(EP[0])


def test_escrow_is_released_on_execute(staged, direct_vm):
    assert ov(staged)["total_escrow"] == str(BOND)
    warp_by(direct_vm, DAY)
    staged.execute_stance_transition(EP[0])
    assert ov(staged)["total_escrow"] == "0" and ov(staged)["total_claimable"] == str(BOND)


# ----------------------------------------------- single pending / monotonic
def test_second_proposal_blocked_while_one_is_pending(direct_vm, staged, direct_bob):
    all_feeds(direct_vm, EP_Q4)
    with direct_vm.expect_revert("ERR_TRANSITION_PENDING"):
        propose(staged, direct_vm, direct_bob, EP_Q4[0])


def test_proposal_allowed_after_execution(direct_vm, staged, direct_bob):
    warp_by(direct_vm, DAY)
    staged.execute_stance_transition(EP[0])
    all_feeds(direct_vm, EP_Q4)
    assert propose(staged, direct_vm, direct_bob, EP_Q4[0])["verdict"] == 1


def test_stale_quarter_cannot_roll_parameters_back(direct_vm, staged, direct_bob):
    warp_by(direct_vm, DAY)
    staged.execute_stance_transition(EP[0])  # Q3 executed
    all_feeds(direct_vm, EP_DOVISH)  # Q1 data is perfectly valid...
    with direct_vm.expect_revert("ERR_STALE_EPOCH"):
        propose(staged, direct_vm, direct_bob, EP_DOVISH[0])  # ...but stale


def test_same_quarter_different_tag_is_allowed(direct_vm, staged, direct_bob):
    warp_by(direct_vm, DAY)
    staged.execute_stance_transition(EP[0])
    other = ("2026-Q3-CPI", 0, 250, NEUTRAL)
    # a later release in the same quarter: it must carry a LATER timestamp
    all_feeds(direct_vm, other, ts=default_ts(EP[0]) + 3600)
    assert propose(staged, direct_vm, direct_bob, other[0])["verdict"] == 1


def test_executed_epoch_cannot_be_re_proposed(direct_vm, staged, direct_bob):
    warp_by(direct_vm, DAY)
    staged.execute_stance_transition(EP[0])
    all_feeds(direct_vm, EP)  # the very same release, same timestamp
    with direct_vm.expect_revert("ERR_STALE_REVISION_ROLLBACK"):
        propose(staged, direct_vm, direct_bob, EP[0])


def test_staged_epoch_cannot_be_re_proposed(direct_vm, staged, direct_bob):
    with direct_vm.expect_revert("ERR_TRANSITION_PENDING"):
        propose(staged, direct_vm, direct_bob, EP[0])


def test_full_year_sequence_applies_each_tier_in_turn(direct_vm, c, direct_alice):
    seen = []
    for ep in (EP_DOVISH, EP_NEUTRAL, EP_HAWKISH, EP_Q4):
        r = stage_and_execute(c, direct_vm, direct_alice, ep)
        seen.append(r["stance"])
    assert seen == [DOVISH, NEUTRAL, HAWKISH, DOVISH]
    assert c.get_epoch_count() == 4


# ---------------------------------------------------------------- rewards
def test_keeper_reward_is_paid_from_forfeited_bonds(direct_vm, c, direct_bob, direct_charlie):
    set_feeds(direct_vm, NOT_FOUND, NOT_FOUND, NOT_FOUND)
    propose(c, direct_vm, direct_charlie, "2026-Q1-FOMC")  # bond forfeited -> treasury
    assert ov(c)["treasury"] == str(BOND)
    all_feeds(direct_vm, EP)
    propose(c, direct_vm, direct_bob, EP[0])
    warp_by(direct_vm, DAY)
    r = c.execute_stance_transition(EP[0])
    assert r["keeper_reward"] == str(KEEPER_REWARD)
    assert c.claimable_of(addr_hex(c, direct_vm, direct_bob)) == str(BOND + KEEPER_REWARD)
    assert ov(c)["treasury"] == str(BOND - KEEPER_REWARD)


def test_no_reward_when_treasury_is_empty(staged, direct_vm):
    warp_by(direct_vm, DAY)
    assert staged.execute_stance_transition(EP[0])["keeper_reward"] == "0"


def test_reward_is_capped_by_treasury_balance(direct_vm, c, direct_alice, direct_bob, direct_charlie):
    set_feeds(direct_vm, NOT_FOUND, NOT_FOUND, NOT_FOUND)
    propose(c, direct_vm, direct_charlie, "2026-Q1-FOMC")
    direct_vm.sender = direct_alice
    c.withdraw_treasury(addr_hex(c, direct_vm, direct_alice), BOND - 5)  # leave 5 wei
    all_feeds(direct_vm, EP)
    propose(c, direct_vm, direct_bob, EP[0])
    warp_by(direct_vm, DAY)
    assert c.execute_stance_transition(EP[0])["keeper_reward"] == "5"
    assert ov(c)["treasury"] == "0"


# ------------------------------------------------------ claims & treasury
def test_claim_payout_pays_once(direct_vm, staged, direct_bob):
    warp_by(direct_vm, DAY)
    staged.execute_stance_transition(EP[0])
    direct_vm.sender = direct_bob
    assert staged.claim_payout() == str(BOND)
    assert ov(staged)["total_claimable"] == "0"
    with direct_vm.expect_revert("ERR_NO_CLAIMABLE_BALANCE"):
        staged.claim_payout()


def test_claim_with_nothing_owed_reverts(direct_vm, c, direct_charlie):
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("ERR_NO_CLAIMABLE_BALANCE"):
        c.claim_payout()


def test_claimable_lookup_is_case_insensitive(direct_vm, staged, direct_bob):
    warp_by(direct_vm, DAY)
    staged.execute_stance_transition(EP[0])
    h = addr_hex(staged, direct_vm, direct_bob)
    assert staged.claimable_of(h) == staged.claimable_of(h.lower()) == staged.claimable_of("0x" + h[2:].upper()) == str(BOND)


def test_quorum_failure_refund_is_claimable(direct_vm, c, direct_bob):
    set_feeds(direct_vm, feed_doc(*EP[:3]), NOT_FOUND, OUTAGE)
    propose(c, direct_vm, direct_bob, EP[0])
    direct_vm.sender = direct_bob
    assert c.claim_payout() == str(BOND)


def test_treasury_withdrawal_is_governor_only_and_bounded(direct_vm, c, direct_alice, direct_bob):
    set_feeds(direct_vm, NOT_FOUND, NOT_FOUND, NOT_FOUND)
    propose(c, direct_vm, direct_bob, EP[0])
    to = addr_hex(c, direct_vm, direct_alice)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("ERR_UNAUTHORIZED"):
        c.withdraw_treasury(to, 1)
    direct_vm.sender = direct_alice
    for bad in (0, BOND + 1):
        with direct_vm.expect_revert("ERR_INVALID_AMOUNT"):
            c.withdraw_treasury(to, bad)
    assert c.withdraw_treasury(to, BOND) == str(BOND)
    assert ov(c)["treasury"] == "0"


# ------------------------------------------------------------- views
def test_epoch_index_enumerates_in_insertion_order(direct_vm, c, direct_alice):
    for ep in (EP_DOVISH, EP_NEUTRAL, EP_HAWKISH):
        stage_and_execute(c, direct_vm, direct_alice, ep)
    assert c.get_epoch_count() == 3
    assert [c.get_epoch_by_index(i)["epoch_id"] for i in range(3)] == [e[0] for e in (EP_DOVISH, EP_NEUTRAL, EP_HAWKISH)]


def test_index_out_of_range_reverts(direct_vm, c):
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_STATE"):
        c.get_epoch_by_index(0)


def test_unknown_epoch_view_reverts(direct_vm, c):
    assert c.has_epoch("2026-Q3-FOMC") is False
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_STATE"):
        c.get_epoch("2026-Q3-FOMC")


def test_failed_attempts_count_and_do_not_duplicate_index(direct_vm, c, direct_bob):
    set_feeds(direct_vm, OUTAGE, OUTAGE, OUTAGE)
    for n in (1, 2, 3):
        assert propose(c, direct_vm, direct_bob, EP[0])["attempts"] == n
    assert c.get_epoch_count() == 1


def test_overview_reports_constants(c):
    o = ov(c)
    assert (o["reporter_bond"], o["challenger_bond"]) == (str(BOND), str(BOND))
    assert (o["activation_timelock"], o["feed_rotation_timelock"]) == (DAY, 7 * DAY)
