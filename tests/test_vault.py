"""Invariant 5: lending sandbox, integer BPS math, no dust, no rounding exploit."""

import pytest

from conftest import *  # noqa: F401,F403
from conftest import _now

RAY = 10**27
BPS = 10000


def ceil_div(a, b):
    return -((-a) // b)


@pytest.fixture()
def c(direct_vm, direct_deploy, direct_alice):
    return new_contract(direct_deploy, direct_vm, direct_alice)


def health(c, vm, who):
    return c.get_account_health(addr_hex(c, vm, who))


def borrow(c, vm, who, amt):
    vm.sender = who
    return c.borrow_against_collateral(amt)


def repay(c, vm, who, amt):
    vm.sender = who
    return c.repay(amt)


def withdraw(c, vm, who, amt):
    vm.sender = who
    return c.withdraw_collateral(amt)


def set_stance(c, vm, who, ep):
    stage_and_execute(c, vm, who, ep)


# ----------------------------------------------------------------- deposits
def test_deposit_accumulates(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    assert deposit(c, direct_vm, direct_bob, 2 * ATTO)["collateral"] == str(3 * ATTO)
    assert c.get_protocol_overview()["total_collateral"] == str(3 * ATTO)


def test_zero_deposit_reverts(direct_vm, c, direct_bob):
    with direct_vm.expect_revert("ERR_INVALID_AMOUNT"):
        deposit(c, direct_vm, direct_bob, 0)


def test_one_wei_deposit_is_exact(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, 1)
    assert health(c, direct_vm, direct_bob)["collateral"] == "1"


def test_unknown_account_is_all_zero(direct_vm, c, direct_charlie):
    h = health(c, direct_vm, direct_charlie)
    assert (h["collateral"], h["debt"], h["status"], h["available_to_borrow"]) == ("0", "0", "NO_DEBT", "0")


def test_health_lookup_is_case_insensitive(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    h = addr_hex(c, direct_vm, direct_bob)
    assert c.get_account_health(h)["collateral"] == c.get_account_health(h.lower())["collateral"] == str(ATTO)


# ------------------------------------------------------------------ borrows
def test_borrow_without_collateral_reverts(direct_vm, c, direct_bob):
    with direct_vm.expect_revert("ERR_INSUFFICIENT_COLLATERAL"):
        borrow(c, direct_vm, direct_bob, 1)


def test_zero_borrow_reverts(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    with direct_vm.expect_revert("ERR_INVALID_AMOUNT"):
        borrow(c, direct_vm, direct_bob, 0)


@pytest.mark.parametrize("ep", [EP_DOVISH, EP_NEUTRAL, EP_HAWKISH])
def test_ltv_boundary_is_exact_for_every_tier(direct_vm, c, direct_alice, direct_bob, ep):
    """`available_to_borrow` is the exact boundary: it works, one wei more does
    not, and it trails the ideal floor(collateral * ltv) by at most the two
    round-up steps (scaled debt, then debt) once the index has left 1.0."""
    set_stance(c, direct_vm, direct_alice, ep)
    deposit(c, direct_vm, direct_bob, ATTO)
    ideal = ATTO * TABLE[ep[3]][0] // BPS
    avail = int(health(c, direct_vm, direct_bob)["available_to_borrow"])
    assert 0 <= ideal - avail <= 2
    with direct_vm.expect_revert("ERR_LTV_EXCEEDED"):
        borrow(c, direct_vm, direct_bob, avail + 1)
    assert int(borrow(c, direct_vm, direct_bob, avail)["debt"]) in range(avail, ideal + 1)
    assert health(c, direct_vm, direct_bob)["status"] == "HEALTHY"
    with direct_vm.expect_revert("ERR_LTV_EXCEEDED"):
        borrow(c, direct_vm, direct_bob, 3)


def test_ltv_boundary_is_exactly_ideal_at_unit_index(direct_vm, c, direct_bob):
    """Before any time has passed the index is exactly 1.0 and there is no loss."""
    deposit(c, direct_vm, direct_bob, ATTO)
    cap = ATTO * 7500 // BPS
    with direct_vm.expect_revert("ERR_LTV_EXCEEDED"):
        borrow(c, direct_vm, direct_bob, cap + 1)
    assert borrow(c, direct_vm, direct_bob, cap)["debt"] == str(cap)


@pytest.mark.parametrize("collateral", [1, 3, 7, 13, 9999, 10001, 123456789, 10**18 + 1, 3 * 10**18 + 7])
def test_available_to_borrow_is_exactly_borrowable_and_one_more_is_not(direct_vm, c, direct_bob, collateral):
    deposit(c, direct_vm, direct_bob, collateral)
    a = int(health(c, direct_vm, direct_bob)["available_to_borrow"])
    assert a == collateral * 7500 // BPS
    if a > 0:
        borrow(c, direct_vm, direct_bob, a)
    with direct_vm.expect_revert():
        borrow(c, direct_vm, direct_bob, 1)


def test_tiny_collateral_cannot_borrow_anything(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, 1)  # 1 * 7500 // 10000 == 0
    assert health(c, direct_vm, direct_bob)["available_to_borrow"] == "0"
    with direct_vm.expect_revert("ERR_LTV_EXCEEDED"):
        borrow(c, direct_vm, direct_bob, 1)


def test_repeated_one_wei_borrows_never_undercount_debt(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, 10**6)
    for _ in range(50):
        borrow(c, direct_vm, direct_bob, 1)
    assert int(health(c, direct_vm, direct_bob)["debt"]) >= 50


def test_accounts_are_isolated(direct_vm, c, direct_bob, direct_charlie):
    deposit(c, direct_vm, direct_bob, ATTO)
    deposit(c, direct_vm, direct_charlie, 2 * ATTO)
    borrow(c, direct_vm, direct_bob, ATTO // 2)
    assert health(c, direct_vm, direct_charlie)["debt"] == "0"
    assert health(c, direct_vm, direct_charlie)["collateral"] == str(2 * ATTO)


# ------------------------------------------------------------------- repay
def test_repay_without_debt_reverts(direct_vm, c, direct_bob):
    with direct_vm.expect_revert("ERR_NO_DEBT"):
        repay(c, direct_vm, direct_bob, 1)


def test_zero_repay_reverts(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    borrow(c, direct_vm, direct_bob, 1000)
    with direct_vm.expect_revert("ERR_INVALID_AMOUNT"):
        repay(c, direct_vm, direct_bob, 0)


def test_partial_repay_reduces_debt_exactly_at_unit_index(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    borrow(c, direct_vm, direct_bob, 1000)
    r = repay(c, direct_vm, direct_bob, 400)
    assert (r["repaid"], r["debt"]) == ("400", "600")


def test_full_repay_leaves_zero_dust_after_interest(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    borrow(c, direct_vm, direct_bob, 10**17)
    warp_by(direct_vm, 37 * DAY + 11)
    debt = int(health(c, direct_vm, direct_bob)["debt"])
    assert debt > 10**17
    r = repay(c, direct_vm, direct_bob, debt)
    assert r["debt"] == "0" and r["repaid"] == str(debt)
    h = health(c, direct_vm, direct_bob)
    assert h["status"] == "NO_DEBT" and h["debt"] == "0"
    assert c.get_protocol_overview()["total_scaled_debt"] == "0"


def test_overpayment_is_clamped_to_the_debt(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    borrow(c, direct_vm, direct_bob, 5000)
    r = repay(c, direct_vm, direct_bob, 10**30)
    assert (r["repaid"], r["debt"]) == ("5000", "0")


def test_repay_cannot_reduce_debt_by_more_than_paid(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    borrow(c, direct_vm, direct_bob, 10**17)
    warp_by(direct_vm, 100 * DAY + 3)
    before = int(health(c, direct_vm, direct_bob)["debt"])
    for amt in (1, 7, 12345, 10**15 + 3):
        r = repay(c, direct_vm, direct_bob, amt) if amt > 1 else None
        if r:
            assert int(r["repaid"]) <= amt
    assert int(health(c, direct_vm, direct_bob)["debt"]) <= before


def test_dust_repay_below_one_scaled_unit_is_refused(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    borrow(c, direct_vm, direct_bob, 10**17)
    warp_by(direct_vm, 100 * DAY)
    # force an accrual so the stored index is > RAY
    deposit(c, direct_vm, direct_bob, 1)
    with direct_vm.expect_revert("ERR_INVALID_AMOUNT"):
        repay(c, direct_vm, direct_bob, 1)


# ---------------------------------------------------------------- withdraw
def test_withdraw_everything_without_debt(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    r = withdraw(c, direct_vm, direct_bob, ATTO)
    assert r["collateral"] == "0" and c.get_protocol_overview()["total_collateral"] == "0"


def test_withdraw_more_than_deposited_reverts(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    with direct_vm.expect_revert("ERR_INSUFFICIENT_COLLATERAL"):
        withdraw(c, direct_vm, direct_bob, ATTO + 1)


def test_zero_withdraw_reverts(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    with direct_vm.expect_revert("ERR_INVALID_AMOUNT"):
        withdraw(c, direct_vm, direct_bob, 0)


def test_withdraw_is_bounded_by_ltv(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, 10**18)
    borrow(c, direct_vm, direct_bob, 5 * 10**17)  # needs >= 5e17 / 0.75 collateral
    need = ceil_div(5 * 10**17 * BPS, 7500)
    free = 10**18 - need
    with direct_vm.expect_revert("ERR_LTV_EXCEEDED"):
        withdraw(c, direct_vm, direct_bob, free + 1)
    assert withdraw(c, direct_vm, direct_bob, free)["collateral"] == str(need)
    assert health(c, direct_vm, direct_bob)["status"] == "HEALTHY"


def test_withdraw_after_full_repay_releases_all(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    borrow(c, direct_vm, direct_bob, 7 * 10**17)
    repay(c, direct_vm, direct_bob, 10**30)
    assert withdraw(c, direct_vm, direct_bob, ATTO)["collateral"] == "0"


# --------------------------------------------------------- interest accrual
@pytest.mark.parametrize("ep", [EP_DOVISH, EP_NEUTRAL, EP_HAWKISH])
def test_one_year_of_interest_at_the_tier_rate(direct_vm, c, direct_alice, direct_bob, ep):
    set_stance(c, direct_vm, direct_alice, ep)
    deposit(c, direct_vm, direct_bob, 10 * ATTO)
    idx_b = int(c.get_protocol_overview()["borrow_index"])
    borrow(c, direct_vm, direct_bob, ATTO)
    warp_by(direct_vm, YEAR)
    rate = TABLE[ep[3]][1]
    # exact integer mirror of the contract's accounting
    scaled = ceil_div(ATTO * RAY, idx_b)
    idx_end = idx_b + ceil_div(idx_b * rate * YEAR, BPS * YEAR)
    mirror = ceil_div(scaled * idx_end, RAY)
    got = int(health(c, direct_vm, direct_bob)["debt"])
    assert got == mirror
    ideal = ATTO + ATTO * rate // BPS
    assert ideal <= got <= ideal + 2  # protocol-favouring rounding is bounded


@pytest.mark.parametrize("ep", [EP_DOVISH, EP_NEUTRAL, EP_HAWKISH])
def test_reserve_factor_share_of_interest(direct_vm, c, direct_alice, direct_bob, ep):
    set_stance(c, direct_vm, direct_alice, ep)
    deposit(c, direct_vm, direct_bob, 10 * ATTO)
    borrow(c, direct_vm, direct_bob, ATTO)
    warp_by(direct_vm, YEAR)
    deposit(c, direct_vm, direct_bob, 1)  # any write triggers the global accrual
    _, rate, rf = TABLE[ep[3]]
    interest = ATTO * rate // BPS
    assert c.get_protocol_overview()["reserves_accrued"] == str(interest * rf // BPS)


def test_no_debt_no_reserves(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    warp_by(direct_vm, YEAR)
    deposit(c, direct_vm, direct_bob, 1)
    assert c.get_protocol_overview()["reserves_accrued"] == "0"


def test_index_matches_the_documented_integer_formula(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    idx = RAY
    for dt in (1, 59, 3600, 86400, 7 * 86400 + 13):
        warp_by(direct_vm, dt)
        deposit(c, direct_vm, direct_bob, 1)
        idx += ceil_div(idx * 550 * dt, BPS * YEAR)
        assert c.get_protocol_overview()["borrow_index"] == str(idx)


def test_splitting_accruals_never_undercharges(direct_vm, c, direct_bob):
    """Many tiny accruals must produce at least the single-step index."""
    deposit(c, direct_vm, direct_bob, ATTO)
    for _ in range(40):
        warp_by(direct_vm, 5)
        deposit(c, direct_vm, direct_bob, 1)
    stepped = int(c.get_protocol_overview()["borrow_index"])
    single = RAY + ceil_div(RAY * 550 * 200, BPS * YEAR)
    assert stepped >= single


def test_interest_accrues_at_old_rate_up_to_the_transition(direct_vm, c, direct_alice, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    borrow(c, direct_vm, direct_bob, 10**17)
    all_feeds(direct_vm, EP_HAWKISH)
    propose(c, direct_vm, direct_alice, EP_HAWKISH[0])
    t_stage = _now(direct_vm)
    warp_by(direct_vm, DAY + 5000)
    c.execute_stance_transition(EP_HAWKISH[0])
    dt = DAY + 5000
    expect = RAY + ceil_div(RAY * 550 * dt, BPS * YEAR)  # NEUTRAL rate, not HAWKISH
    assert c.get_protocol_overview()["borrow_index"] == str(expect)
    assert t_stage > 0


def test_new_rate_applies_after_the_transition(direct_vm, c, direct_alice, direct_bob):
    deposit(c, direct_vm, direct_bob, 10 * ATTO)
    borrow(c, direct_vm, direct_bob, ATTO)
    stage_and_execute(c, direct_vm, direct_alice, EP_HAWKISH)
    idx0 = int(c.get_protocol_overview()["borrow_index"])
    warp_by(direct_vm, YEAR)
    h = health(c, direct_vm, direct_bob)
    grown = idx0 + ceil_div(idx0 * 900 * YEAR, BPS * YEAR)
    scaled = int(c.get_protocol_overview()["total_scaled_debt"])
    assert h["debt"] == str(ceil_div(scaled * grown, RAY))


# -------------------------------------------- stance change vs open positions
def test_tightening_leaves_existing_positions_over_ltv_but_not_liquidated(direct_vm, c, direct_alice, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    borrow(c, direct_vm, direct_bob, ATTO * 7000 // BPS)  # fine at 75%, too much at 65%
    before = health(c, direct_vm, direct_bob)
    assert before["status"] == "HEALTHY"
    stage_and_execute(c, direct_vm, direct_alice, EP_HAWKISH)
    after = health(c, direct_vm, direct_bob)
    assert after["status"] == "OVER_LTV" and after["max_ltv_bps"] == 6500
    assert after["collateral"] == before["collateral"]  # nothing is seized
    assert after["available_to_borrow"] == "0"


def test_over_ltv_blocks_new_borrows_and_withdrawals_until_repaid(direct_vm, c, direct_alice, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    borrow(c, direct_vm, direct_bob, ATTO * 7000 // BPS)
    stage_and_execute(c, direct_vm, direct_alice, EP_HAWKISH)
    with direct_vm.expect_revert("ERR_LTV_EXCEEDED"):
        borrow(c, direct_vm, direct_bob, 1)
    with direct_vm.expect_revert("ERR_LTV_EXCEEDED"):
        withdraw(c, direct_vm, direct_bob, 1)
    debt = int(health(c, direct_vm, direct_bob)["debt"])
    target = ATTO * 6500 // BPS
    repay(c, direct_vm, direct_bob, debt - target)
    assert health(c, direct_vm, direct_bob)["status"] == "HEALTHY"


def test_loosening_raises_borrow_capacity(direct_vm, c, direct_alice, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    assert health(c, direct_vm, direct_bob)["borrow_capacity"] == str(ATTO * 7500 // BPS)
    stage_and_execute(c, direct_vm, direct_alice, EP_DOVISH)
    assert health(c, direct_vm, direct_bob)["borrow_capacity"] == str(ATTO * 8000 // BPS)


def test_health_factor_and_ltv_fields(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    borrow(c, direct_vm, direct_bob, ATTO // 2)
    h = health(c, direct_vm, direct_bob)
    assert h["ltv_bps"] == 5000
    assert h["health_factor_bps"] == ATTO * 7500 // (ATTO // 2)  # 15000 == 1.5x


def test_view_projection_includes_unaccrued_interest(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, 10 * ATTO)
    borrow(c, direct_vm, direct_bob, ATTO)
    d0 = int(health(c, direct_vm, direct_bob)["debt"])
    warp_by(direct_vm, 30 * DAY)
    assert int(health(c, direct_vm, direct_bob)["debt"]) > d0  # no write happened


def test_borrow_after_interest_counts_accrued_debt_toward_ltv(direct_vm, c, direct_bob):
    deposit(c, direct_vm, direct_bob, ATTO)
    borrow(c, direct_vm, direct_bob, ATTO * 7500 // BPS - 1000)
    warp_by(direct_vm, 90 * DAY)
    with direct_vm.expect_revert("ERR_LTV_EXCEEDED"):
        borrow(c, direct_vm, direct_bob, 2000)
