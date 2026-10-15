"""Recheck of 0b2cdb2: release-timestamp rollback guard, challenge window
through the validity window, and the current-quarter epoch bound."""

import pytest

from conftest import *  # noqa: F401,F403
from conftest import _now

WEEK = 7 * DAY
EID = "2026-Q3-FOMC"
T1 = default_ts(EID)            # the older release
T2 = T1 + 7200                  # the revision, published later
T3 = T2 + 7200


def doc(rate, cpi, ts):
    return feed_doc(EID, rate, cpi, ts=ts)


OLD = doc(-25, 190, T1)         # DOVISH  -> max LTV 80%
REVISED = doc(25, 320, T2)      # HAWKISH -> max LTV 65%
NEWER = doc(0, 250, T3)         # NEUTRAL


def three(vm, d):
    set_feeds(vm, d, d, d)


def ov(c):
    return c.get_protocol_overview()


@pytest.fixture()
def c(direct_vm, direct_deploy, direct_alice):
    return new_contract(direct_deploy, direct_vm, direct_alice)


def execute(c, vm, who, eid=EID):
    warp_by(vm, DAY)
    vm.sender = who
    return c.execute_stance_transition(eid)


# ===================================================================== #1
def test_older_release_cannot_roll_back_an_already_revised_epoch(direct_vm, c, direct_bob):
    """The reported scenario: LTV 80% -> revised to 65% -> an older release
    must not be able to put it back to 80%."""
    three(direct_vm, OLD)
    propose(c, direct_vm, direct_bob, EID)
    assert execute(c, direct_vm, direct_bob)["max_ltv_bps"] == 8000

    three(direct_vm, REVISED)  # the revision of the SAME epoch, strictly newer
    res = propose(c, direct_vm, direct_bob, EID)
    assert res["status"] == "STAGED" and res["release_ts"] == T2
    assert execute(c, direct_vm, direct_bob)["max_ltv_bps"] == 6500
    assert ov(c)["active_release_ts"] == T2

    three(direct_vm, OLD)  # a stale mirror serves the superseded release again
    with direct_vm.expect_revert("ERR_STALE_REVISION_ROLLBACK"):
        propose(c, direct_vm, direct_bob, EID)
    o = ov(c)
    assert (o["active_stance"], o["max_ltv_bps"], o["pending_epoch"]) == (HAWKISH, 6500, "")


def test_a_timestamp_equal_to_the_executed_one_is_not_newer(direct_vm, c, direct_bob):
    three(direct_vm, REVISED)
    propose(c, direct_vm, direct_bob, EID)
    execute(c, direct_vm, direct_bob)
    three(direct_vm, REVISED)  # identical release, identical timestamp
    with direct_vm.expect_revert("ERR_STALE_REVISION_ROLLBACK"):
        propose(c, direct_vm, direct_bob, EID)
    # same numbers, one second later, is a genuinely newer publication
    three(direct_vm, doc(25, 320, T2 + 1))
    assert propose(c, direct_vm, direct_bob, EID)["status"] == "STAGED"


def test_an_executed_epoch_accepts_a_strictly_newer_revision_repeatedly(direct_vm, c, direct_bob):
    three(direct_vm, OLD)
    propose(c, direct_vm, direct_bob, EID)
    execute(c, direct_vm, direct_bob)
    seen = []
    for d, want in ((REVISED, HAWKISH), (NEWER, NEUTRAL)):
        three(direct_vm, d)
        propose(c, direct_vm, direct_bob, EID)
        seen.append(execute(c, direct_vm, direct_bob)["stance"])
        assert seen[-1] == want
    assert ov(c)["active_release_ts"] == T3 and ov(c)["active_version"] == 3


def test_an_older_release_under_a_different_tag_of_the_same_quarter_is_refused(direct_vm, c, direct_bob):
    """Rollback without reusing the epoch id: same quarter, other tag."""
    three(direct_vm, REVISED)
    propose(c, direct_vm, direct_bob, EID)
    execute(c, direct_vm, direct_bob)
    sibling = "2026-Q3-FOMCR"
    set_feeds(direct_vm, *(feed_doc(sibling, -25, 190, ts=T1),) * 3)  # older than the live release
    with direct_vm.expect_revert("ERR_STALE_REVISION_ROLLBACK"):
        propose(c, direct_vm, direct_bob, sibling)
    set_feeds(direct_vm, *(feed_doc(sibling, 0, 250, ts=T3),) * 3)  # newer is fine
    assert propose(c, direct_vm, direct_bob, sibling)["status"] == "STAGED"


def test_an_older_release_for_an_earlier_quarter_is_refused_by_timestamp_too(direct_vm, c, direct_bob):
    """Quarter monotonicity already blocks this; the timestamp guard is the
    second, independent line (here: a same-quarter id with an old timestamp)."""
    three(direct_vm, NEWER)
    propose(c, direct_vm, direct_bob, EID)
    execute(c, direct_vm, direct_bob)
    set_feeds(direct_vm, *(feed_doc("2026-Q3-AAAA", 25, 320, ts=T1),) * 3)
    with direct_vm.expect_revert("ERR_STALE_REVISION_ROLLBACK"):
        propose(c, direct_vm, direct_bob, "2026-Q3-AAAA")


def test_executing_an_older_release_reverts(direct_vm, c, direct_bob):
    three(direct_vm, OLD)
    propose(c, direct_vm, direct_bob, EID)
    c.active_release_ts = T2  # a newer release became live in the meantime
    warp_by(direct_vm, DAY)
    with direct_vm.expect_revert("ERR_STALE_REVISION_ROLLBACK"):
        c.execute_stance_transition(EID)
    assert ov(c)["active_stance"] == NEUTRAL


def test_execution_is_also_refused_against_the_per_epoch_record(direct_vm, c, direct_bob):
    three(direct_vm, OLD)
    propose(c, direct_vm, direct_bob, EID)
    c.executed_epoch_releases[EID] = T3  # this epoch was executed at a newer release
    warp_by(direct_vm, DAY)
    with direct_vm.expect_revert("ERR_STALE_REVISION_ROLLBACK"):
        c.execute_stance_transition(EID)


def test_a_cancelled_revision_leaves_the_live_stance_and_marker_untouched(direct_vm, c, direct_bob, direct_charlie):
    three(direct_vm, OLD)
    propose(c, direct_vm, direct_bob, EID)
    execute(c, direct_vm, direct_bob)
    three(direct_vm, REVISED)
    propose(c, direct_vm, direct_bob, EID)
    three(direct_vm, retracted_doc(EID))
    assert challenge(c, direct_vm, direct_charlie, EID)["challenge_outcome"] == "CANCELLED"
    o = ov(c)
    assert (o["active_stance"], o["active_release_ts"], o["pending_epoch"]) == (DOVISH, T1, "")
    # a corrected, still-newer publication can be applied afterwards
    three(direct_vm, doc(25, 320, T3))
    propose(c, direct_vm, direct_bob, EID)
    assert execute(c, direct_vm, direct_bob)["stance"] == HAWKISH


def test_a_release_dated_in_the_future_is_refused(direct_vm, c, direct_bob):
    now = _now(direct_vm)
    three(direct_vm, doc(25, 320, now + DAY + 1))
    with direct_vm.expect_revert("ERR_FUTURE_RELEASE"):
        propose(c, direct_vm, direct_bob, EID)
    three(direct_vm, doc(25, 320, now + DAY))  # exactly the slack: accepted
    assert propose(c, direct_vm, direct_bob, EID)["status"] == "STAGED"


def test_a_future_dated_release_cannot_lock_out_later_releases(direct_vm, c, direct_bob):
    """Why the future guard exists: the executed timestamp is a high-water mark."""
    three(direct_vm, doc(25, 320, 4_000_000_000))  # year 2096, inside the static bounds
    with direct_vm.expect_revert("ERR_FUTURE_RELEASE"):
        propose(c, direct_vm, direct_bob, EID)
    three(direct_vm, OLD)
    assert propose(c, direct_vm, direct_bob, EID)["status"] == "STAGED"


def test_a_failed_quorum_does_not_need_or_move_a_release_timestamp(direct_vm, c, direct_bob):
    set_feeds(direct_vm, OLD, NOT_FOUND, OUTAGE)
    res = propose(c, direct_vm, direct_bob, EID)
    assert (res["verdict"], res["release_ts"]) == (2, 0)
    assert ov(c)["active_release_ts"] == 0


# ===================================================================== #2
def stage(c, vm, who, d=REVISED):
    three(vm, d)
    return propose(c, vm, who, EID)


def test_staged_proposal_at_day_2_can_be_challenged_and_aborted_on_retraction(
    direct_vm, c, direct_bob, direct_charlie
):
    stage(c, direct_vm, direct_bob)
    warp_by(direct_vm, 2 * DAY)  # well past the 24 h timelock, never executed
    three(direct_vm, retracted_doc(EID))

    res = challenge(c, direct_vm, direct_charlie, EID)
    assert res["challenge_outcome"] == "CANCELLED" and res["status"] == "CANCELLED"
    assert c.claimable_of(addr_hex(c, direct_vm, direct_charlie)) == str(2 * BOND)  # own + slashed
    assert c.claimable_of(addr_hex(c, direct_vm, direct_bob)) == "0"

    with direct_vm.expect_revert("ERR_INVALID_EPOCH_STATE"):  # the transition is aborted
        c.execute_stance_transition(EID)
    o = ov(c)
    assert (o["active_stance"], o["pending_epoch"], o["total_escrow"]) == (NEUTRAL, "", "0")


def test_a_conflict_on_day_5_aborts_the_transition(direct_vm, c, direct_bob, direct_charlie):
    stage(c, direct_vm, direct_bob)
    warp_by(direct_vm, 5 * DAY)
    set_feeds(direct_vm, doc(25, 320, T2), doc(-25, 190, T2), doc(0, 250, T2))  # three stances
    assert challenge(c, direct_vm, direct_charlie, EID)["challenge_outcome"] == "CANCELLED"
    assert ov(c)["active_stance"] == NEUTRAL


@pytest.mark.parametrize("day_offset", [DAY, DAY + 1, 2 * DAY, 5 * DAY, WEEK + DAY - 1, WEEK + DAY])
def test_retraction_challenge_succeeds_anywhere_inside_the_validity_window(
    direct_vm, c, direct_bob, direct_charlie, day_offset
):
    stage(c, direct_vm, direct_bob)
    warp_by(direct_vm, day_offset)
    three(direct_vm, retracted_doc(EID))
    assert challenge(c, direct_vm, direct_charlie, EID)["challenge_outcome"] == "CANCELLED"


def test_challenge_after_the_validity_window_is_closed(direct_vm, c, direct_bob, direct_charlie):
    stage(c, direct_vm, direct_bob)
    warp_by(direct_vm, WEEK + DAY + 1)
    three(direct_vm, retracted_doc(EID))
    with direct_vm.expect_revert("ERR_CHALLENGE_WINDOW_CLOSED"):
        challenge(c, direct_vm, direct_charlie, EID)
    # and the stale stage still cannot be applied: it expires instead
    assert c.execute_stance_transition(EID)["status"] == "EXPIRED"
    assert ov(c)["active_stance"] == NEUTRAL


def test_an_unchallenged_stage_still_executes_on_day_2(direct_vm, c, direct_bob):
    stage(c, direct_vm, direct_bob)
    warp_by(direct_vm, 2 * DAY)
    direct_vm.sender = direct_bob
    assert c.execute_stance_transition(EID)["status"] == "EXECUTED"


def test_a_failed_late_challenge_pays_the_reporter_and_execution_continues(
    direct_vm, c, direct_bob, direct_charlie
):
    stage(c, direct_vm, direct_bob)
    warp_by(direct_vm, 3 * DAY)
    res = challenge(c, direct_vm, direct_charlie, EID)  # feeds still confirm
    assert res["challenge_outcome"] == "UPHELD"
    direct_vm.sender = direct_bob
    assert c.execute_stance_transition(EID)["status"] == "EXECUTED"
    assert c.claimable_of(addr_hex(c, direct_vm, direct_bob)) == str(2 * BOND)


def test_a_late_cancellation_revokes_the_release_like_an_early_one(direct_vm, c, direct_bob, direct_charlie):
    stage(c, direct_vm, direct_bob)
    warp_by(direct_vm, 4 * DAY)
    three(direct_vm, retracted_doc(EID))
    challenge(c, direct_vm, direct_charlie, EID)
    three(direct_vm, REVISED)  # a cache brings the retracted release back
    with direct_vm.expect_revert("ERR_STALE_RELEASE"):
        propose(c, direct_vm, direct_bob, EID)


# ===================================================================== #3
@pytest.mark.parametrize("epoch_id", ["2027-Q4-FOMC", "2027-Q1-FOMC", "2028-Q1-FOMC", "2200-Q4-FOMC"])
def test_a_future_quarter_reverts_as_future_epoch(direct_vm, c, direct_bob, epoch_id):
    all_feeds(direct_vm, (epoch_id, 25, 320, HAWKISH))  # valid data, and a valid timestamp
    with direct_vm.expect_revert("ERR_FUTURE_EPOCH"):
        propose(c, direct_vm, direct_bob, epoch_id)
    assert c.get_epoch_count() == 0 and ov(c)["total_escrow"] == "0"


def test_2027_q4_in_2026_cannot_poison_the_monotonic_ordering(direct_vm, c, direct_bob):
    all_feeds(direct_vm, ("2027-Q4-FOMC", 25, 320, HAWKISH))
    with direct_vm.expect_revert("ERR_FUTURE_EPOCH"):
        propose(c, direct_vm, direct_bob, "2027-Q4-FOMC")
    # the present quarter and the past are untouched
    for ep in (EP_Q4,):
        all_feeds(direct_vm, ep)
        assert propose(c, direct_vm, direct_bob, ep[0])["status"] == "STAGED"
    assert ov(c)["last_executed_seq"] == 0


@pytest.mark.parametrize(
    "ep",
    [EP_Q4, EP_HAWKISH, EP_NEUTRAL, EP_DOVISH, ("2025-Q4-FOMC", 25, 320, HAWKISH), ("2000-Q1-AB", 0, 250, NEUTRAL)],
    ids=lambda e: e[0],
)
def test_the_current_quarter_and_every_earlier_one_is_accepted(direct_vm, c, direct_bob, ep):
    # clock: 2026-10-01, i.e. 2026-Q4
    all_feeds(direct_vm, ep)
    assert propose(c, direct_vm, direct_bob, ep[0])["status"] == "STAGED"


@pytest.mark.parametrize(
    "now,epoch_id,ok",
    [
        ("2026-02-01T00:00:00Z", "2026-Q1-FOMC", True),
        ("2026-02-01T00:00:00Z", "2026-Q2-FOMC", False),
        ("2026-03-31T23:59:59Z", "2026-Q2-FOMC", False),   # last second of Q1
        ("2026-04-01T00:00:00Z", "2026-Q2-FOMC", True),    # first second of Q2
        ("2026-06-30T23:59:59Z", "2026-Q3-FOMC", False),
        ("2026-07-01T00:00:00Z", "2026-Q3-FOMC", True),
        ("2026-09-30T23:59:59Z", "2026-Q4-FOMC", False),
        ("2026-10-01T00:00:00Z", "2026-Q4-FOMC", True),
        ("2026-12-31T23:59:59Z", "2027-Q1-FOMC", False),   # year boundary
        ("2027-01-01T00:00:00Z", "2027-Q1-FOMC", True),
        ("2027-01-01T00:00:00Z", "2027-Q2-FOMC", False),
    ],
)
def test_quarter_boundaries_follow_the_block_clock(direct_vm, direct_deploy, direct_alice, direct_bob, now, epoch_id, ok):
    direct_vm.warp(now)
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT, FEED_A, FEED_B, FEED_C)
    # a release inside the quarter it names, so only the epoch bound is in play
    y, q = int(epoch_id[:4]), int(epoch_id[6])
    from datetime import datetime, timezone

    qstart = int(datetime(y, (q - 1) * 3 + 1, 1, tzinfo=timezone.utc).timestamp())
    all_feeds(direct_vm, (epoch_id, 25, 320, HAWKISH), ts=qstart + 60)
    if ok:
        assert propose(c, direct_vm, direct_bob, epoch_id)["status"] == "STAGED"
    else:
        with direct_vm.expect_revert("ERR_FUTURE_EPOCH"):
            propose(c, direct_vm, direct_bob, epoch_id)


def test_future_epoch_is_rejected_before_any_network_or_bond_handling(direct_vm, c, direct_bob):
    direct_vm.clear_mocks()
    with direct_vm.expect_revert("ERR_FUTURE_EPOCH"):
        propose(c, direct_vm, direct_bob, "2027-Q4-FOMC", value=0)


def test_overview_reports_the_current_quarter(c):
    o = ov(c)
    assert (o["current_year"], o["current_quarter"]) == (2026, 4)
    assert o["max_epoch_seq"] == 2026 * 4 + 3
