"""Invariant 2: deterministic stance buckets and exact parameter binding."""

import itertools

import pytest

from conftest import *  # noqa: F401,F403


@pytest.fixture()
def c(direct_vm, direct_deploy, direct_alice):
    return new_contract(direct_deploy, direct_vm, direct_alice)


def py_stance(rate, cpi):
    """Reference implementation, written straight from the spec text."""
    if rate >= 25 or cpi > 300:
        return 2
    if rate <= -25 or cpi < 200:
        return 0
    return 1


# (rate_change_bps, cpi_yoy_bps, expected)
BOUNDARIES = [
    # Rate-driven edges at neutral CPI (250)
    (-100, 250, 0), (-26, 250, 0), (-25, 250, 0), (-24, 250, 1), (-10, 250, 1),
    (-1, 250, 1), (0, 250, 1), (1, 250, 1), (10, 250, 1), (11, 250, 1),
    (24, 250, 1), (25, 250, 2), (26, 250, 2), (50, 250, 2), (100, 250, 2),
    # CPI-driven edges at unchanged rate
    (0, -100, 0), (0, 0, 0), (0, 199, 0), (0, 200, 1), (0, 201, 1),
    (0, 299, 1), (0, 300, 1), (0, 301, 2), (0, 400, 2), (0, 1000, 2),
    # Priority: hawkish beats dovish (stagflation: cut + hot CPI, hike + cold CPI)
    (-25, 301, 2), (-100, 500, 2), (25, 199, 2), (50, 100, 2),
    # Dovish when only one dovish trigger is present with a mid-band rate move
    (-25, 300, 0), (-25, 200, 0), (-24, 199, 0), (24, 199, 0),
]


@pytest.mark.parametrize("rate,cpi,expected", BOUNDARIES)
def test_contract_matches_spec_on_boundaries(c, rate, cpi, expected):
    assert py_stance(rate, cpi) == expected  # the table itself is spec-correct
    assert c.derive_stance(rate, cpi) == expected


@pytest.mark.parametrize(
    "rate,cpi", list(itertools.product([-60, -25, -24, 0, 24, 25, 60], [150, 199, 200, 300, 301, 450]))
)
def test_contract_matches_reference_on_grid(c, rate, cpi):
    assert c.derive_stance(rate, cpi) == py_stance(rate, cpi)


@pytest.mark.parametrize("stance", [DOVISH, NEUTRAL, HAWKISH])
def test_risk_table_is_exact(c, stance):
    t = c.get_risk_table(stance)
    ltv, rate, rf = TABLE[stance]
    assert (t["max_ltv_bps"], t["borrow_rate_bps"], t["reserve_factor_bps"]) == (ltv, rate, rf)
    assert t["stance_name"] == ["DOVISH", "NEUTRAL", "HAWKISH"][stance]


@pytest.mark.parametrize("bad", [-1, 3, 99])
def test_risk_table_rejects_unknown_stance(direct_vm, c, bad):
    with direct_vm.expect_revert("ERR_INVALID_STANCE"):
        c.get_risk_table(bad)


def test_tiers_are_strictly_ordered_by_risk(c):
    d, n, h = (c.get_risk_table(i) for i in range(3))
    assert d["max_ltv_bps"] > n["max_ltv_bps"] > h["max_ltv_bps"]
    assert d["borrow_rate_bps"] < n["borrow_rate_bps"] < h["borrow_rate_bps"]
    assert d["reserve_factor_bps"] < n["reserve_factor_bps"] < h["reserve_factor_bps"]


def test_genesis_stance_is_neutral(c):
    p = c.get_active_params()
    assert p["stance"] == NEUTRAL and p["stance_name"] == "NEUTRAL"
    assert (p["max_ltv_bps"], p["borrow_rate_bps"], p["reserve_factor_bps"]) == TABLE[NEUTRAL]


@pytest.mark.parametrize("ep", [EP_DOVISH, EP_NEUTRAL, EP_HAWKISH])
def test_activation_binds_exact_tier_not_raw_numbers(direct_vm, c, direct_alice, ep):
    res = stage_and_execute(c, direct_vm, direct_alice, ep)
    assert res["stance"] == ep[3]
    assert (res["max_ltv_bps"], res["borrow_rate_bps"], res["reserve_factor_bps"]) == TABLE[ep[3]]
    p = c.get_active_params()
    assert (p["max_ltv_bps"], p["borrow_rate_bps"], p["reserve_factor_bps"]) == TABLE[ep[3]]


@pytest.mark.parametrize("cpi", [301, 350, 400, 999, 5000])
def test_raw_value_inside_a_tier_changes_nothing(direct_vm, c, direct_alice, cpi):
    """Every CPI above 300 is HAWKISH -> the identical, fixed parameter row."""
    stage_and_execute(c, direct_vm, direct_alice, ("2026-Q3-FOMC", 0, cpi, HAWKISH))
    p = c.get_active_params()
    assert (p["stance"], p["max_ltv_bps"], p["borrow_rate_bps"], p["reserve_factor_bps"]) == (HAWKISH, *TABLE[HAWKISH])
