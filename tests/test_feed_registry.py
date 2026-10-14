"""Invariant 1: governed 3-slot registry, host uniqueness, 7 day rotation timelock."""

import pytest

from conftest import *  # noqa: F401,F403
from conftest import _now

WEEK = 7 * DAY
NEW_A = "https://gitlab.com/Handik4/macrostance/-/raw/main/mock_feeds/a/"
NEW_B = "https://fastly.jsdelivr.net/gh/Handik4/MacroStance@main/deployments/mock_feeds/b/"
NEW_C = "https://cdn.statically.io/gh/Handik4/MacroStance/main/deployments/mock_feeds/c/"


@pytest.fixture()
def c(direct_vm, direct_deploy, direct_alice):
    return new_contract(direct_deploy, direct_vm, direct_alice)


def reg(c):
    return {r["slot"]: r for r in c.get_feed_registry()}


def test_genesis_registry_has_three_distinct_hosts(c):
    r = reg(c)
    assert [r[i]["active_url"] for i in range(3)] == [FEED_A, FEED_B, FEED_C]
    assert len({r[i]["active_host"] for i in range(3)}) == 3
    assert all(r[i]["staged_url"] == "" and r[i]["activates_at"] == 0 for i in range(3))


def test_no_public_method_accepts_a_url_for_data_fetching(c):
    """Callers never pass URLs: the only URL-taking writes are governor-gated."""
    import inspect

    # `propose_epoch_sync` / `challenge_staged_epoch` take only an epoch id.
    assert list(inspect.signature(c.propose_epoch_sync).parameters) == ["epoch_id"]
    assert list(inspect.signature(c.challenge_staged_epoch).parameters) == ["epoch_id"]


# ------------------------------------------------------------ URL validation
GOOD = [
    "https://example.org/feeds/",
    "https://raw.githubusercontent.com/o/r/main/x/",
    "https://cdn.jsdelivr.net/gh/o/r@main/x/",
    "https://a-b.c-d.example.io/p/q/r/",
    "https://EXAMPLE.org/Feeds/",
]
BAD = {
    "http://example.org/feeds/": "scheme",
    "ftp://example.org/feeds/": "scheme",
    "//example.org/feeds/": "scheme",
    "example.org/feeds/": "scheme",
    "https://": "length",
    "": "length",
    "https://example.org": "path",
    "https://example.org/feeds": "path",
    "https://example.org/a//b/": "path",
    "https://example.org/a/../b/": "path",
    "https://example.org/a/./b/": "path",
    "https://example.org:8443/feeds/": "authority",
    "https://user:pw@example.org/feeds/": "authority",
    "https://user@example.org/feeds/": "authority",
    "https://example.org/feeds/?x=1/": "forbidden character",
    "https://example.org/feeds/#frag/": "forbidden character",
    "https://example.org/fee%2e%2e/ds/": "forbidden character",
    "https://example.org/fee\\ds/": "forbidden character",
    "https://example.org/fe eds/": "characters",
    "https://example.org/feeds/\n": "characters",
    "https://localhost/feeds/": "host",
    "https://foo.localhost/feeds/": "host",
    "https://printer.local/feeds/": "host",
    "https://svc.internal/feeds/": "host",
    "https://127.0.0.1/feeds/": "host",
    "https://10.0.0.5/feeds/": "host",
    "https://169.254.169.254/latest/": "host",
    "https://[::1]/feeds/": "authority",
    "https://nodots/feeds/": "host",
    "https://-bad.example.org/feeds/": "host",
    "https://exa_mple.org/feeds/": "host",
    "https://example.123/feeds/": "host",
}


@pytest.mark.parametrize("url", GOOD)
def test_acceptable_prefixes(c, url):
    assert c.feed_prefix_error(url) == ""


@pytest.mark.parametrize("url", list(BAD))
def test_unsafe_prefixes_are_rejected(c, url):
    assert c.feed_prefix_error(url) != "", url


def test_overlong_prefix_is_rejected(c):
    assert c.feed_prefix_error("https://example.org/" + "a" * 200 + "/") == "length"


def test_constructor_rejects_unsafe_and_duplicate_feeds(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("ERR_UNSAFE_FEED_URL"):
        direct_deploy(CONTRACT, "http://raw.githubusercontent.com/x/", FEED_B, FEED_C)


def test_constructor_rejects_same_host_in_two_slots(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("ERR_DUPLICATE_FEED_HOST"):
        direct_deploy(CONTRACT, FEED_A, FEED_A.replace("/a/", "/b/"), FEED_C)


# ------------------------------------------------------------------ rotation
def stage(c, vm, who, slot, url):
    vm.sender = who
    return c.stage_feed_rotation(slot, url)


def test_stage_sets_seven_day_timelock(direct_vm, c, direct_alice):
    r = stage(c, direct_vm, direct_alice, 0, NEW_A)
    assert r["activates_at"] == _now(direct_vm) + WEEK
    s = reg(c)[0]
    assert s["staged_url"] == NEW_A and s["active_url"] == FEED_A  # active untouched


def test_staged_feed_is_not_used_until_activation(direct_vm, c, direct_alice, direct_bob):
    stage(c, direct_vm, direct_alice, 0, NEW_A)
    set_feeds(direct_vm, feed_doc(*EP_HAWKISH[:3]), feed_doc(*EP_HAWKISH[:3]), feed_doc(*EP_HAWKISH[:3]))
    assert propose(c, direct_vm, direct_bob, EP_HAWKISH[0])["source_count"] == 3  # old host answered


def test_activation_before_timelock_reverts(direct_vm, c, direct_alice):
    stage(c, direct_vm, direct_alice, 1, NEW_B)
    warp_by(direct_vm, WEEK - 1)
    with direct_vm.expect_revert("ERR_TIMELOCK_ACTIVE"):
        c.activate_feed_rotation(1)


def test_activation_at_exact_timelock_succeeds(direct_vm, c, direct_alice):
    stage(c, direct_vm, direct_alice, 1, NEW_B)
    warp_by(direct_vm, WEEK)
    assert c.activate_feed_rotation(1)["active_url"] == NEW_B
    s = reg(c)[1]
    assert s["active_url"] == NEW_B and s["staged_url"] == "" and s["activates_at"] == 0


def test_activation_is_permissionless(direct_vm, c, direct_alice, direct_charlie):
    stage(c, direct_vm, direct_alice, 2, NEW_C)
    warp_by(direct_vm, WEEK)
    direct_vm.sender = direct_charlie
    assert c.activate_feed_rotation(2)["active_url"] == NEW_C


def test_activate_without_staged_reverts(direct_vm, c):
    with direct_vm.expect_revert("ERR_NO_STAGED_FEED"):
        c.activate_feed_rotation(0)


@pytest.mark.parametrize("fn", ["stage", "cancel"])
def test_rotation_is_governor_only(direct_vm, c, direct_alice, direct_bob, fn):
    stage(c, direct_vm, direct_alice, 0, NEW_A)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("ERR_UNAUTHORIZED"):
        if fn == "stage":
            c.stage_feed_rotation(1, NEW_B)
        else:
            c.cancel_feed_rotation(0)


def test_cancel_clears_staged_feed(direct_vm, c, direct_alice):
    stage(c, direct_vm, direct_alice, 0, NEW_A)
    c.cancel_feed_rotation(0)
    assert reg(c)[0]["staged_url"] == ""
    warp_by(direct_vm, WEEK)
    with direct_vm.expect_revert("ERR_NO_STAGED_FEED"):
        c.activate_feed_rotation(0)


def test_cancel_without_staged_reverts(direct_vm, c, direct_alice):
    with direct_vm.expect_revert("ERR_NO_STAGED_FEED"):
        c.cancel_feed_rotation(0)


@pytest.mark.parametrize("slot", [3, 4, 255])
def test_invalid_slot_reverts(direct_vm, c, direct_alice, slot):
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("ERR_INVALID_SLOT"):
        c.stage_feed_rotation(slot, NEW_A)
    with direct_vm.expect_revert("ERR_INVALID_SLOT"):
        c.activate_feed_rotation(slot)


def test_restaging_resets_the_timer(direct_vm, c, direct_alice):
    stage(c, direct_vm, direct_alice, 0, NEW_A)
    warp_by(direct_vm, WEEK - 10)
    r = stage(c, direct_vm, direct_alice, 0, NEW_A.replace("gitlab.com", "bitbucket.org"))
    assert r["activates_at"] == _now(direct_vm) + WEEK
    warp_by(direct_vm, 20)
    with direct_vm.expect_revert("ERR_TIMELOCK_ACTIVE"):
        c.activate_feed_rotation(0)


# ----------------------------------------------------- host uniqueness (core)
def test_cannot_stage_a_host_that_is_active_in_another_slot(direct_vm, c, direct_alice):
    with direct_vm.expect_revert("ERR_DUPLICATE_FEED_HOST"):
        stage(c, direct_vm, direct_alice, 0, "https://cdn.jsdelivr.net/gh/evil/x@main/a/")


def test_cannot_stage_a_host_that_is_staged_in_another_slot(direct_vm, c, direct_alice):
    stage(c, direct_vm, direct_alice, 0, NEW_A)
    with direct_vm.expect_revert("ERR_DUPLICATE_FEED_HOST"):
        stage(c, direct_vm, direct_alice, 1, "https://gitlab.com/other/path/")


def test_apex_domain_collision_is_rejected(direct_vm, c, direct_alice):
    # a different subdomain of an apex that another slot already uses
    with direct_vm.expect_revert("ERR_DUPLICATE_FEED_HOST"):
        stage(c, direct_vm, direct_alice, 1, "https://gist.githubusercontent.com/o/r/")


def test_case_differences_do_not_bypass_host_uniqueness(direct_vm, c, direct_alice):
    with direct_vm.expect_revert("ERR_DUPLICATE_FEED_HOST"):
        stage(c, direct_vm, direct_alice, 1, "https://RAW.GITHUBUSERCONTENT.COM/o/r/")


def test_slot_may_be_repointed_within_its_own_host(direct_vm, c, direct_alice):
    r = stage(c, direct_vm, direct_alice, 0, "https://raw.githubusercontent.com/Handik4/other/main/a/")
    assert r["slot"] == 0


def test_two_staged_slots_cannot_share_a_host_even_if_active_hosts_differ(direct_vm, c, direct_alice):
    stage(c, direct_vm, direct_alice, 0, NEW_A)
    stage(c, direct_vm, direct_alice, 1, NEW_B)
    with direct_vm.expect_revert("ERR_DUPLICATE_FEED_HOST"):
        stage(c, direct_vm, direct_alice, 2, "https://gitlab.com/third/x/")


def test_rotation_swapping_hosts_between_slots_is_staged_then_blocked_correctly(direct_vm, c, direct_alice):
    """Slot A wants B's host while B is still active: rejected at stage time."""
    with direct_vm.expect_revert("ERR_DUPLICATE_FEED_HOST"):
        stage(c, direct_vm, direct_alice, 0, FEED_B)


def test_stage_validates_url_shape(direct_vm, c, direct_alice):
    with direct_vm.expect_revert("ERR_UNSAFE_FEED_URL"):
        stage(c, direct_vm, direct_alice, 0, "http://gitlab.com/x/")


def test_governor_transfer(direct_vm, c, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    c.transfer_governor(addr_hex(c, direct_vm, direct_bob))
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("ERR_UNAUTHORIZED"):
        c.stage_feed_rotation(0, NEW_A)
    direct_vm.sender = direct_bob
    assert c.stage_feed_rotation(0, NEW_A)["slot"] == 0


def test_epoch_uses_activated_feed(direct_vm, c, direct_alice, direct_bob):
    stage(c, direct_vm, direct_alice, 0, "https://raw.githubusercontent.com/Handik4/other/main/a/")
    warp_by(direct_vm, WEEK)
    c.activate_feed_rotation(0)
    direct_vm.clear_mocks()
    doc = feed_doc(*EP_HAWKISH[:3])
    direct_vm.mock_web(r"https://raw\.githubusercontent\.com/Handik4/other/main/a/2026-Q3-FOMC\.json", doc)
    direct_vm.mock_web(rf"https://{HOSTS['b']}/.*", doc)
    direct_vm.mock_web(rf"https://{HOSTS['c']}/.*", NOT_FOUND)
    assert propose(c, direct_vm, direct_bob, EP_HAWKISH[0])["verdict"] == 1


def test_urls_are_built_from_registry_prefix_and_epoch_id(direct_vm, c, direct_bob):
    """The exact URL fetched is prefix + epoch_id + '.json' on every slot."""
    direct_vm.clear_mocks()
    for prefix in (FEED_A, FEED_B, FEED_C):
        direct_vm.mock_web(prefix.replace(".", r"\.") + r"2026-Q4-FOMC\.json", feed_doc("2026-Q4-FOMC", -25, 210))
    assert propose(c, direct_vm, direct_bob, "2026-Q4-FOMC")["source_count"] == 3
