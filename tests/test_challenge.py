"""Emergency circuit breaker: challenge a staged epoch inside the 24h window."""

import pytest

from conftest import *  # noqa: F401,F403

EP = EP_HAWKISH


@pytest.fixture()
def staged(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = new_contract(direct_deploy, direct_vm, direct_alice)
    all_feeds(direct_vm, EP)
    propose(c, direct_vm, direct_bob, EP[0])
    return c


def ov(c):
    return c.get_protocol_overview()


def conserved(c, bonds):
    o = ov(c)
    return int(o["total_escrow"]) + int(o["treasury"]) + int(o["total_claimable"]) == bonds


def test_retraction_cancels_and_slashes_reporter_to_challenger(direct_vm, staged, direct_bob, direct_charlie):
    r = retracted_doc(EP[0])
    set_feeds(direct_vm, r, r, r)
    res = challenge(staged, direct_vm, direct_charlie, EP[0])
    assert res["challenge_outcome"] == "CANCELLED" and res["status"] == "CANCELLED"
    ch = addr_hex(staged, direct_vm, direct_charlie)
    assert staged.claimable_of(ch) == str(2 * BOND)  # own bond back + reporter's bond
    assert staged.claimable_of(addr_hex(staged, direct_vm, direct_bob)) == "0"
    o = ov(staged)
    assert o["pending_epoch"] == "" and o["total_escrow"] == "0"
    assert res["resolved_by"] == ch.lower()
    assert conserved(staged, 2 * BOND)


def test_single_retracting_source_is_enough(direct_vm, staged, direct_charlie):
    set_feeds(direct_vm, feed_doc(*EP[:3]), feed_doc(*EP[:3]), retracted_doc(EP[0]))
    assert challenge(staged, direct_vm, direct_charlie, EP[0])["challenge_outcome"] == "CANCELLED"


def test_conflicting_stances_cancel(direct_vm, staged, direct_charlie):
    set_feeds(direct_vm, feed_doc(EP[0], 25, 320), feed_doc(EP[0], -25, 190), feed_doc(EP[0], 0, 250))
    assert challenge(staged, direct_vm, direct_charlie, EP[0])["challenge_outcome"] == "CANCELLED"


def test_release_revised_to_a_new_quorum_cancels_the_stale_stage(direct_vm, staged, direct_charlie):
    revised = feed_doc(EP[0], 0, 250)  # all three now agree on a DIFFERENT release
    set_feeds(direct_vm, revised, revised, revised)
    res = challenge(staged, direct_vm, direct_charlie, EP[0])
    assert res["challenge_outcome"] == "CANCELLED" and res["challenge_verdict"] == 1


def test_same_stance_but_revised_numbers_also_cancels(direct_vm, staged, direct_charlie):
    revised = feed_doc(EP[0], 50, 400)  # still HAWKISH, different release hash
    set_feeds(direct_vm, revised, revised, revised)
    assert challenge(staged, direct_vm, direct_charlie, EP[0])["challenge_outcome"] == "CANCELLED"


def test_challenge_with_404_lag_does_not_slash_reporter_or_revoke_valid_release(
    direct_vm, staged, direct_bob, direct_charlie
):
    """1 CONFIRMED + 2x 404 (CDN purge / propagation lag) is absence of evidence,
    not proof of invalidity: the release is UPHELD, the reporter's bond is safe,
    the hash is not revoked and the release executes normally."""
    before = staged.get_epoch(EP[0])
    set_feeds(direct_vm, NOT_FOUND, NOT_FOUND, feed_doc(*EP[:3]))
    res = challenge(staged, direct_vm, direct_charlie, EP[0])
    bob = addr_hex(staged, direct_vm, direct_bob)
    ch = addr_hex(staged, direct_vm, direct_charlie)
    assert res["challenge_outcome"] == "UPHELD" and res["status"] == "STAGED"
    assert not res["challenge_contradicted"]
    assert res["bond"] == str(BOND)  # reporter bond untouched
    assert ov(staged)["pending_epoch"] == EP[0] and ov(staged)["total_escrow"] == str(BOND)
    assert staged.claimable_of(ch) == str(BOND)  # challenger refunded, not punished
    assert staged.claimable_of(bob) == "0"
    assert res["revision"] == before["revision"]  # revision not advanced -> hash not revoked
    assert conserved(staged, 2 * BOND)
    # the valid release is still executable and the reporter gets the bond back
    set_feeds(direct_vm, *[feed_doc(*EP[:3])] * 3)
    warp_by(direct_vm, DAY)
    assert staged.execute_stance_transition(EP[0])["stance"] == HAWKISH
    assert staged.claimable_of(bob) == str(BOND)
    assert conserved(staged, 2 * BOND)


@pytest.mark.parametrize("pattern", ["lag", "outage_mix", "all_404"])
def test_no_proof_patterns_never_cancel(direct_vm, staged, direct_charlie, pattern):
    docs = {
        "lag": (NOT_FOUND, NOT_FOUND, feed_doc(*EP[:3])),
        "outage_mix": (NOT_FOUND, OUTAGE, feed_doc(*EP[:3])),
        "all_404": (NOT_FOUND, NOT_FOUND, NOT_FOUND),
    }[pattern]
    set_feeds(direct_vm, *docs)
    res = challenge(staged, direct_vm, direct_charlie, EP[0])
    assert res["challenge_outcome"] in ("UPHELD", "INCONCLUSIVE") and res["status"] == "STAGED"


def test_one_conflicting_source_is_not_proof(direct_vm, staged, direct_charlie):
    other = feed_doc(EP[0], 50, 400)
    set_feeds(direct_vm, other, NOT_FOUND, NOT_FOUND)
    res = challenge(staged, direct_vm, direct_charlie, EP[0])
    assert res["challenge_outcome"] == "UPHELD" and res["status"] == "STAGED"


def test_two_conflicting_sources_are_proof(direct_vm, staged, direct_charlie):
    other = feed_doc(EP[0], 50, 400)
    set_feeds(direct_vm, other, other, NOT_FOUND)
    assert challenge(staged, direct_vm, direct_charlie, EP[0])["challenge_outcome"] == "CANCELLED"


def test_retraction_plus_404s_is_proof(direct_vm, staged, direct_charlie):
    set_feeds(direct_vm, retracted_doc(EP[0]), NOT_FOUND, NOT_FOUND)
    assert challenge(staged, direct_vm, direct_charlie, EP[0])["challenge_outcome"] == "CANCELLED"


def test_frivolous_challenge_pays_challenger_bond_to_reporter(direct_vm, staged, direct_bob, direct_charlie):
    res = challenge(staged, direct_vm, direct_charlie, EP[0])  # feeds still confirm
    assert res["challenge_outcome"] == "UPHELD" and res["status"] == "STAGED"
    assert staged.claimable_of(addr_hex(staged, direct_vm, direct_bob)) == str(BOND)
    assert staged.claimable_of(addr_hex(staged, direct_vm, direct_charlie)) == "0"
    assert ov(staged)["pending_epoch"] == EP[0] and ov(staged)["total_escrow"] == str(BOND)
    assert conserved(staged, 2 * BOND)


def test_staged_epoch_still_executes_after_failed_challenge(direct_vm, staged, direct_bob, direct_charlie):
    challenge(staged, direct_vm, direct_charlie, EP[0])
    warp_by(direct_vm, DAY)
    assert staged.execute_stance_transition(EP[0])["stance"] == HAWKISH
    # reporter: own bond + challenger's forfeited bond
    assert staged.claimable_of(addr_hex(staged, direct_vm, direct_bob)) == str(2 * BOND)


def test_outage_during_challenge_is_inconclusive_and_refunds_challenger(direct_vm, staged, direct_charlie):
    set_feeds(direct_vm, feed_doc(*EP[:3]), NOT_FOUND, OUTAGE)
    res = challenge(staged, direct_vm, direct_charlie, EP[0])
    assert res["challenge_outcome"] == "INCONCLUSIVE" and res["status"] == "STAGED"
    assert staged.claimable_of(addr_hex(staged, direct_vm, direct_charlie)) == str(BOND)
    assert ov(staged)["total_escrow"] == str(BOND)


def test_total_outage_is_inconclusive(direct_vm, staged, direct_charlie):
    set_feeds(direct_vm, OUTAGE, OUTAGE, OUTAGE)
    assert challenge(staged, direct_vm, direct_charlie, EP[0])["challenge_outcome"] == "INCONCLUSIVE"


def test_cancelled_epoch_cannot_be_executed(direct_vm, staged, direct_charlie):
    r = retracted_doc(EP[0])
    set_feeds(direct_vm, r, r, r)
    challenge(staged, direct_vm, direct_charlie, EP[0])
    warp_by(direct_vm, DAY)
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_STATE"):
        staged.execute_stance_transition(EP[0])
    assert staged.get_active_params()["stance"] == NEUTRAL


def test_pending_slot_is_freed_by_cancellation(direct_vm, staged, direct_bob, direct_charlie):
    r = retracted_doc(EP[0])
    set_feeds(direct_vm, r, r, r)
    challenge(staged, direct_vm, direct_charlie, EP[0])
    all_feeds(direct_vm, EP_Q4)
    assert propose(staged, direct_vm, direct_bob, EP_Q4[0])["verdict"] == 1


def test_cancelled_epoch_can_be_re_proposed_after_a_corrected_release(direct_vm, staged, direct_bob, direct_charlie):
    r = retracted_doc(EP[0])
    set_feeds(direct_vm, r, r, r)
    challenge(staged, direct_vm, direct_charlie, EP[0])
    fixed = feed_doc(EP[0], 0, 250)
    set_feeds(direct_vm, fixed, fixed, fixed)
    res = propose(staged, direct_vm, direct_bob, EP[0])
    assert res["verdict"] == 1 and res["stance"] == NEUTRAL and res["attempts"] == 2


def test_window_stays_open_after_the_timelock_until_the_stage_expires(direct_vm, staged, direct_charlie):
    warp_by(direct_vm, DAY + 1)  # the 24 h timelock is over; still not executed
    assert challenge(staged, direct_vm, direct_charlie, EP[0])["challenge_outcome"] == "UPHELD"
    warp_by(direct_vm, 7 * DAY - 1)  # exactly the last second of the validity window
    assert challenge(staged, direct_vm, direct_charlie, EP[0])["challenge_outcome"] == "UPHELD"
    warp_by(direct_vm, 1)
    with direct_vm.expect_revert("ERR_CHALLENGE_WINDOW_CLOSED"):
        challenge(staged, direct_vm, direct_charlie, EP[0])


def test_challenge_after_execution_reverts(direct_vm, staged, direct_charlie):
    warp_by(direct_vm, DAY)
    staged.execute_stance_transition(EP[0])
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_STATE"):
        challenge(staged, direct_vm, direct_charlie, EP[0])


def test_challenge_unknown_epoch_reverts(direct_vm, staged, direct_charlie):
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_STATE"):
        challenge(staged, direct_vm, direct_charlie, "2026-Q4-FOMC")


@pytest.mark.parametrize("value", [0, BOND - 1])
def test_challenger_bond_below_minimum_reverts(direct_vm, staged, direct_charlie, value):
    with direct_vm.expect_revert("ERR_INSUFFICIENT_BOND"):
        challenge(staged, direct_vm, direct_charlie, EP[0], value)


def test_challenger_bond_above_cap_reverts(direct_vm, staged, direct_charlie):
    with direct_vm.expect_revert("ERR_INSUFFICIENT_BOND"):
        challenge(staged, direct_vm, direct_charlie, EP[0], 10 * ATTO + 1)


def test_challenge_of_failed_proposal_reverts(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    c = new_contract(direct_deploy, direct_vm, direct_alice)
    set_feeds(direct_vm, NOT_FOUND, NOT_FOUND, NOT_FOUND)
    propose(c, direct_vm, direct_bob, EP[0])
    with direct_vm.expect_revert("ERR_INVALID_EPOCH_STATE"):
        challenge(c, direct_vm, direct_charlie, EP[0])


def test_reporter_can_claim_nothing_after_being_slashed(direct_vm, staged, direct_bob, direct_charlie):
    r = retracted_doc(EP[0])
    set_feeds(direct_vm, r, r, r)
    challenge(staged, direct_vm, direct_charlie, EP[0])
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("ERR_NO_CLAIMABLE_BALANCE"):
        staged.claim_payout()
    direct_vm.sender = direct_charlie
    assert staged.claim_payout() == str(2 * BOND)
