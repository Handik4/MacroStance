"""Audit hardening: release lineage, proposal expiry, epoch-year bound, borrow ceiling.

Findings #1, #2, #3 and #7 of the audit of dda2887.
"""

import pytest

from conftest import *  # noqa: F401,F403
from conftest import _now

WEEK = 7 * DAY
EP = EP_HAWKISH
EID = EP[0]
H1 = feed_doc(EID, 25, 320)       # HAWKISH  -- the release that gets revised
H2 = feed_doc(EID, 0, 250)        # NEUTRAL  -- the revision
H3 = feed_doc(EID, -25, 190)      # DOVISH   -- a later revision


def ov(c):
    return c.get_protocol_overview()


@pytest.fixture()
def c(direct_vm, direct_deploy, direct_alice):
    return new_contract(direct_deploy, direct_vm, direct_alice)


def three(vm, doc):
    set_feeds(vm, doc, doc, doc)


# ============================================================ finding #1
def test_older_revision_cannot_roll_back_revised_stance(direct_vm, c, direct_bob, direct_charlie):
    three(direct_vm, H1)
    first = propose(c, direct_vm, direct_bob, EID)
    assert first["status"] == "STAGED" and first["stance"] == HAWKISH

    # The release is revised; a challenger proves it and the stage is cancelled.
    three(direct_vm, H2)
    cancelled = challenge(c, direct_vm, direct_charlie, EID)
    assert cancelled["challenge_outcome"] == "CANCELLED"
    assert cancelled["revision"] == 1

    # A stale mirror flips back to the original numbers: it must not stage.
    three(direct_vm, H1)
    with direct_vm.expect_revert("ERR_STALE_RELEASE"):
        propose(c, direct_vm, direct_bob, EID)
    o = ov(c)
    assert (o["pending_epoch"], o["total_escrow"]) == ("", "0")
    assert c.get_epoch(EID)["status"] == "CANCELLED"

    # The revision itself is accepted, executes, and the old one stays dead.
    three(direct_vm, H2)
    res = propose(c, direct_vm, direct_bob, EID)
    assert res["verdict"] == 1 and res["stance"] == NEUTRAL and res["revision"] == 1
    warp_by(direct_vm, DAY)
    direct_vm.sender = direct_bob
    assert c.execute_stance_transition(EID)["stance"] == NEUTRAL
    o = ov(c)
    assert (o["active_stance"], o["active_version"], o["active_epoch_revision"]) == (NEUTRAL, 1, 1)

    three(direct_vm, H1)
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_STATE"):  # EXECUTED is terminal
        propose(c, direct_vm, direct_bob, EID)
    assert ov(c)["active_stance"] == NEUTRAL


def test_revoked_release_stays_revoked_across_repeated_revisions(direct_vm, c, direct_bob, direct_charlie):
    three(direct_vm, H1)
    propose(c, direct_vm, direct_bob, EID)
    three(direct_vm, H2)
    challenge(c, direct_vm, direct_charlie, EID)  # H1 revoked, revision 1
    propose(c, direct_vm, direct_bob, EID)  # H2 staged
    three(direct_vm, H3)
    res = challenge(c, direct_vm, direct_charlie, EID)  # H2 revoked, revision 2
    assert res["revision"] == 2 and res["status"] == "CANCELLED"
    for stale in (H1, H2):
        three(direct_vm, stale)
        with direct_vm.expect_revert("ERR_STALE_RELEASE"):
            propose(c, direct_vm, direct_bob, EID)
    three(direct_vm, H3)
    assert propose(c, direct_vm, direct_bob, EID)["stance"] == DOVISH


def test_retracted_release_cannot_be_replayed(direct_vm, c, direct_bob, direct_charlie):
    three(direct_vm, H1)
    propose(c, direct_vm, direct_bob, EID)
    three(direct_vm, retracted_doc(EID))
    assert challenge(c, direct_vm, direct_charlie, EID)["challenge_outcome"] == "CANCELLED"
    three(direct_vm, H1)  # the retraction is "undone" by a cache
    with direct_vm.expect_revert("ERR_STALE_RELEASE"):
        propose(c, direct_vm, direct_bob, EID)


def test_stale_release_reverts_so_the_reporter_keeps_the_bond(direct_vm, c, direct_bob, direct_charlie):
    three(direct_vm, H1)
    propose(c, direct_vm, direct_bob, EID)
    three(direct_vm, H2)
    challenge(c, direct_vm, direct_charlie, EID)
    before = ov(c)
    three(direct_vm, H1)
    with direct_vm.expect_revert("ERR_STALE_RELEASE"):
        propose(c, direct_vm, direct_bob, EID)
    after = ov(c)
    for k in ("treasury", "total_escrow", "total_claimable"):
        assert before[k] == after[k]


def test_failed_challenge_does_not_revoke(direct_vm, c, direct_bob, direct_charlie):
    three(direct_vm, H1)
    propose(c, direct_vm, direct_bob, EID)
    res = challenge(c, direct_vm, direct_charlie, EID)  # feeds still confirm
    assert res["challenge_outcome"] == "UPHELD" and res["revision"] == 0
    warp_by(direct_vm, DAY)
    assert c.execute_stance_transition(EID)["stance"] == HAWKISH


def test_revocation_is_scoped_to_the_epoch(direct_vm, c, direct_bob, direct_charlie):
    three(direct_vm, H1)
    propose(c, direct_vm, direct_bob, EID)
    three(direct_vm, H2)
    challenge(c, direct_vm, direct_charlie, EID)
    other = ("2026-Q4-CPI", 25, 320, HAWKISH)  # same numbers, different epoch
    all_feeds(direct_vm, other)
    assert propose(c, direct_vm, direct_bob, other[0])["verdict"] == 1


def test_active_version_counts_executed_transitions(direct_vm, c, direct_alice):
    seen = []
    for ep in (EP_DOVISH, EP_NEUTRAL, EP_HAWKISH):
        stage_and_execute(c, direct_vm, direct_alice, ep)
        seen.append(ov(c)["active_version"])
    assert seen == [1, 2, 3]


def test_staged_release_records_the_version_it_was_staged_against(direct_vm, c, direct_alice, direct_bob):
    stage_and_execute(c, direct_vm, direct_alice, EP_DOVISH)
    all_feeds(direct_vm, EP_NEUTRAL)
    res = propose(c, direct_vm, direct_bob, EP_NEUTRAL[0])
    assert res["base_version"] == 1


def test_execute_refuses_a_stage_against_a_superseded_version(direct_vm, c, direct_bob):
    three(direct_vm, H1)
    propose(c, direct_vm, direct_bob, EID)
    c.active_version = 9  # simulate a newer stance having become active
    warp_by(direct_vm, DAY)
    with direct_vm.expect_revert("ERR_STALE_RELEASE"):
        c.execute_stance_transition(EID)
    assert ov(c)["active_stance"] == NEUTRAL


def test_execute_refuses_a_revoked_hash(direct_vm, c, direct_bob):
    three(direct_vm, H1)
    res = propose(c, direct_vm, direct_bob, EID)
    c.revoked_hashes[f"{EID}|{res['release_hash']}"] = True
    warp_by(direct_vm, DAY)
    with direct_vm.expect_revert("ERR_STALE_RELEASE"):
        c.execute_stance_transition(EID)


# ============================================================ finding #2
def test_unexecuted_proposal_expires_after_validity_window(direct_vm, c, direct_bob, direct_charlie):
    three(direct_vm, H1)
    propose(c, direct_vm, direct_bob, EID)
    warp_by(direct_vm, DAY + WEEK + 1)  # one second past the window

    direct_vm.sender = direct_charlie  # anyone may trigger the expiry
    res = c.execute_stance_transition(EID)
    assert res["status"] == "EXPIRED" and res["reporter_refund"] == str(BOND)

    o = ov(c)
    assert o["active_stance"] == NEUTRAL and o["active_version"] == 0  # untouched
    assert (o["pending_epoch"], o["total_escrow"], o["total_claimable"]) == ("", "0", str(BOND))
    assert c.claimable_of(addr_hex(c, direct_vm, direct_bob)) == str(BOND)
    assert c.get_epoch(EID)["status"] == "EXPIRED"

    with direct_vm.expect_revert("ERR_INVALID_EPOCH_STATE"):  # forbidden thereafter
        c.execute_stance_transition(EID)


def test_execution_is_allowed_through_the_last_second_of_the_window(direct_vm, c, direct_bob):
    three(direct_vm, H1)
    propose(c, direct_vm, direct_bob, EID)
    warp_by(direct_vm, DAY + WEEK)  # exactly staged + 24h + window
    direct_vm.sender = direct_bob
    res = c.execute_stance_transition(EID)
    assert res["status"] == "EXECUTED" and res["stance"] == HAWKISH


def test_execution_before_the_timelock_is_still_refused(direct_vm, c, direct_bob):
    three(direct_vm, H1)
    propose(c, direct_vm, direct_bob, EID)
    warp_by(direct_vm, DAY - 1)
    with direct_vm.expect_revert("ERR_TIMELOCK_ACTIVE"):
        c.execute_stance_transition(EID)


def test_expiry_releases_bond_without_a_keeper_reward(direct_vm, c, direct_bob, direct_charlie):
    set_feeds(direct_vm, NOT_FOUND, NOT_FOUND, NOT_FOUND)
    propose(c, direct_vm, direct_charlie, "2026-Q1-FOMC")  # fund the treasury
    three(direct_vm, H1)
    propose(c, direct_vm, direct_bob, EID)
    warp_by(direct_vm, DAY + WEEK + 5)
    res = c.execute_stance_transition(EID)
    assert res["keeper_reward"] == "0"
    assert ov(c)["treasury"] == str(BOND)  # the reward was NOT paid out
    assert c.claimable_of(addr_hex(c, direct_vm, direct_bob)) == str(BOND)


def test_expired_epoch_can_be_re_proposed_against_fresh_feeds(direct_vm, c, direct_bob):
    three(direct_vm, H1)
    propose(c, direct_vm, direct_bob, EID)
    warp_by(direct_vm, DAY + WEEK + 1)
    c.execute_stance_transition(EID)
    res = propose(c, direct_vm, direct_bob, EID)
    assert res["verdict"] == 1 and res["status"] == "STAGED" and res["attempts"] == 2


def test_a_stuck_stage_never_blocks_new_proposals_forever(direct_vm, c, direct_bob, direct_charlie):
    three(direct_vm, H1)
    propose(c, direct_vm, direct_bob, EID)
    warp_by(direct_vm, DAY + WEEK + 1)  # nobody executed it
    all_feeds(direct_vm, EP_Q4)
    res = propose(c, direct_vm, direct_charlie, EP_Q4[0])  # auto-expires the stuck one
    assert res["status"] == "STAGED"
    assert c.get_epoch(EID)["status"] == "EXPIRED"
    assert c.claimable_of(addr_hex(c, direct_vm, direct_bob)) == str(BOND)
    assert ov(c)["pending_epoch"] == EP_Q4[0]


def test_a_live_stage_still_blocks_new_proposals(direct_vm, c, direct_bob, direct_charlie):
    three(direct_vm, H1)
    propose(c, direct_vm, direct_bob, EID)
    warp_by(direct_vm, DAY + WEEK)  # still inside the window
    all_feeds(direct_vm, EP_Q4)
    with direct_vm.expect_revert("ERR_TRANSITION_PENDING"):
        propose(c, direct_vm, direct_charlie, EP_Q4[0])


def test_challenge_window_is_still_only_the_first_24_hours(direct_vm, c, direct_bob, direct_charlie):
    three(direct_vm, H1)
    propose(c, direct_vm, direct_bob, EID)
    warp_by(direct_vm, DAY + 1)
    with direct_vm.expect_revert("ERR_CHALLENGE_WINDOW_CLOSED"):
        challenge(c, direct_vm, direct_charlie, EID)


def test_overview_and_epoch_views_expose_the_window(direct_vm, c, direct_bob):
    assert ov(c)["proposal_validity_window"] == WEEK
    three(direct_vm, H1)
    e = propose(c, direct_vm, direct_bob, EID)
    assert e["expires_at"] == e["activates_at"] + WEEK


# ============================================================ finding #3
@pytest.mark.parametrize("year", [2028, 2030, 2100, 2200])
def test_distant_future_epoch_year_rejected(direct_vm, c, direct_bob, year):
    eid = f"{year}-Q1-FOMC"
    all_feeds(direct_vm, (eid, 25, 320, HAWKISH))  # perfectly valid data
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_ID"):
        propose(c, direct_vm, direct_bob, eid)
    assert c.get_epoch_count() == 0 and ov(c)["total_escrow"] == "0"


@pytest.mark.parametrize("year", [2026, 2027])
def test_current_and_next_year_are_accepted(direct_vm, c, direct_bob, year):
    eid = f"{year}-Q4-FOMC"
    all_feeds(direct_vm, (eid, 25, 320, HAWKISH))
    assert propose(c, direct_vm, direct_bob, eid)["verdict"] == 1


def test_the_bound_follows_the_block_clock(direct_vm, c, direct_bob):
    all_feeds(direct_vm, ("2028-Q1-FOMC", 25, 320, HAWKISH))
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_ID"):
        propose(c, direct_vm, direct_bob, "2028-Q1-FOMC")
    direct_vm.warp("2027-01-01T00:00:00Z")  # now 2028 is "next year"
    assert propose(c, direct_vm, direct_bob, "2028-Q1-FOMC")["verdict"] == 1


def test_overview_reports_the_current_maximum_year(c):
    assert ov(c)["max_epoch_year"] == 2027


def test_year_boundary_is_checked_before_any_network_or_bond_handling(direct_vm, c, direct_bob):
    direct_vm.clear_mocks()
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_ID"):
        propose(c, direct_vm, direct_bob, "2200-Q4-FOMC", value=0)


# ============================================================ finding #7
def age_index(c, vm, who, years, rate_bps=550):
    """Drive the global index up with one accrual per simulated year."""
    deposit(c, vm, who, 1)
    for _ in range(years):
        warp_by(vm, YEAR)
        deposit(c, vm, who, 1)


def test_borrow_ceiling_is_exact_when_the_index_has_grown_large(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, 10 * ATTO)
    age_index(c, direct_vm, direct_bob, 60)  # 1.055^60 ~ 25x
    idx = int(ov(c)["borrow_index"])
    assert idx > 10 * RAY_  # far beyond the old 4-step walk's reach
    avail = int(c.get_account_health(addr_hex(c, direct_vm, direct_bob))["available_to_borrow"])
    ideal = (10 * ATTO + 60 + 1) * 7500 // 10000
    assert 0 < avail <= ideal and ideal - avail <= idx // RAY_ + 2
    direct_vm.sender = direct_bob
    c.borrow_against_collateral(avail)  # borrowable
    h = c.get_account_health(addr_hex(c, direct_vm, direct_bob))
    assert h["status"] == "HEALTHY" and h["available_to_borrow"] == "0"


def test_one_wei_more_than_available_is_refused_at_large_index(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, 10 * ATTO)
    age_index(c, direct_vm, direct_bob, 45)
    avail = int(c.get_account_health(addr_hex(c, direct_vm, direct_bob))["available_to_borrow"])
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("ERR_LTV_EXCEEDED"):
        c.borrow_against_collateral(avail + 1)
    c.borrow_against_collateral(avail)


@pytest.mark.parametrize("years", [0, 5, 20, 40, 80])
def test_available_matches_brute_force_across_index_magnitudes(direct_vm, c, direct_bob, years):
    deposit(c, direct_vm, direct_bob, 3 * ATTO + 17)
    age_index(c, direct_vm, direct_bob, years)
    o = ov(c)
    idx, collateral = int(o["borrow_index"]), 3 * ATTO + 17 + years + 1
    d_max = collateral * 7500 // 10000
    # brute-force the largest `a` the contract's own borrow rule accepts
    lo, hi = 0, d_max
    while lo < hi:
        mid = (lo + hi + 1) // 2
        scaled = -(-mid * RAY_ // idx)
        debt = -(-scaled * idx // RAY_)
        if debt <= d_max:
            lo = mid
        else:
            hi = mid - 1
    got = int(c.get_account_health(addr_hex(c, direct_vm, direct_bob))["available_to_borrow"])
    assert got == lo


def test_index_stays_within_u256_after_a_century(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    age_index(c, direct_vm, direct_bob, 100)
    assert int(ov(c)["borrow_index"]) < 2**256


RAY_ = 10**27
