"""Invariant 3: fault-tolerant 2-of-3 quorum and verdict semantics.

Two layers:
  * `resolve_verdict` over EVERY combination of per-source states, checked
    against an independent oracle written from the spec's wording ("could the
    down source still mathematically complete 2-of-3?") rather than copied from
    the contract's branch order;
  * the full `propose_epoch_sync` path over every HTTP-level combination, which
    also pins where the bond goes.
"""

import itertools
import json

import pytest

from conftest import *  # noqa: F401,F403

H0 = "a" * 64
H1 = "b" * 64
H2 = "c" * 64
# Symbolic per-source states used for exhaustive enumeration.
TS0, TS1, TS2 = 1_790_000_000, 1_790_000_500, 1_790_001_000
SYMS = {
    "C0": ("CONFIRMED", 0, H0, TS0),   # stance 0, hash H0
    "C0b": ("CONFIRMED", 0, H1, TS1),  # same stance, DIFFERENT release hash
    "C2": ("CONFIRMED", 2, H2, TS2),   # conflicting stance
    "R": ("RETRACTED", 0, "", 0),
    "N": ("NOT_FOUND", 0, "", 0),
    "O": ("OUTAGE", 0, "", 0),
}


def oracle(states):
    """verdict from first principles. 0 REJECTED, 1 VERIFIED, 2 QUORUM_FAILURE."""
    kinds = [s[0] for s in states]
    if "RETRACTED" in kinds:
        return 0
    confirmed = [(s[1], s[2]) for s in states if s[0] == "CONFIRMED"]  # (stance, hash)
    outages = kinds.count("OUTAGE")
    # a matching pair already exists?
    pair = any(a == b for a, b in itertools.combinations(confirmed, 2))
    stances = {s for s, _ in confirmed}
    if pair:
        return 0 if len(stances) > 1 else 1
    if len(stances) > 1:
        return 0  # explicit conflicting confirmed stances
    # No pair, no conflict. Could the outage(s) still complete a 2-of-3?
    can_complete = outages >= 2 or (outages >= 1 and len(confirmed) >= 1)
    if can_complete and outages >= 1:
        return 2
    return 0


COMBOS = list(itertools.product(SYMS.keys(), repeat=3))


@pytest.fixture()
def c(direct_vm, direct_deploy, direct_alice):
    return new_contract(direct_deploy, direct_vm, direct_alice)


@pytest.mark.parametrize("combo", COMBOS, ids=lambda t: "-".join(t))
def test_resolution_matches_independent_oracle(c, combo):
    states = [SYMS[k] for k in combo]
    got = c.resolve_verdict(json.dumps([list(s) for s in states]))
    assert got["verdict"] == oracle(states), combo
    if got["verdict"] == 1:
        assert got["source_count"] >= 2 and len(got["release_hash"]) == 64
        # the verified release's timestamp is the matching group's own
        assert got["release_ts"] in (TS0, TS1, TS2)
    else:
        assert got["stance"] == 0 and got["release_hash"] == "" and got["release_ts"] == 0


@pytest.mark.parametrize(
    "states,verdict",
    [
        # The spec's headline case: never slash when the down source could complete quorum.
        ([SYMS["C0"], SYMS["N"], SYMS["O"]], 2),
        ([SYMS["C0"], SYMS["O"], SYMS["N"]], 2),
        ([SYMS["O"], SYMS["C0"], SYMS["N"]], 2),
        ([SYMS["C0"], SYMS["O"], SYMS["O"]], 2),
        ([SYMS["O"], SYMS["O"], SYMS["O"]], 2),
        ([SYMS["N"], SYMS["O"], SYMS["O"]], 2),
        ([SYMS["C0"], SYMS["C0b"], SYMS["O"]], 2),
        # >= 2 x 404 can never reach 2 confirmations -> fake / unreleased epoch.
        ([SYMS["N"], SYMS["N"], SYMS["O"]], 0),
        ([SYMS["N"], SYMS["N"], SYMS["C0"]], 0),
        ([SYMS["N"], SYMS["N"], SYMS["N"]], 0),
        # Explicit retraction beats an otherwise perfect quorum.
        ([SYMS["C0"], SYMS["C0"], SYMS["R"]], 0),
        # Third source confirms a conflicting stance -> not VERIFIED.
        ([SYMS["C0"], SYMS["C0"], SYMS["C2"]], 0),
        ([SYMS["C0"], SYMS["C2"], SYMS["O"]], 0),
        # Clean quorum.
        ([SYMS["C0"], SYMS["C0"], SYMS["C0"]], 1),
        ([SYMS["C0"], SYMS["C0"], SYMS["N"]], 1),
        ([SYMS["C0"], SYMS["C0"], SYMS["O"]], 1),
        ([SYMS["C0"], SYMS["C0"], SYMS["C0b"]], 1),
        # All three answered cleanly, no match.
        ([SYMS["C0"], SYMS["C0b"], SYMS["N"]], 0),
    ],
)
def test_spec_cases(c, states, verdict):
    got = c.resolve_verdict(json.dumps([list(s) for s in states]))
    assert got["verdict"] == verdict


def test_verified_source_count_is_size_of_matching_group(c):
    full = c.resolve_verdict(json.dumps([list(SYMS["C0"])] * 3))
    pair = c.resolve_verdict(json.dumps([list(SYMS["C0"]), list(SYMS["C0"]), list(SYMS["N"])]))
    assert (full["source_count"], pair["source_count"]) == (3, 2)


def test_verified_carries_the_matching_hash_and_stance(c):
    got = c.resolve_verdict(json.dumps([list(SYMS["C2"]), list(SYMS["C2"]), list(SYMS["O"])]))
    assert (got["verdict"], got["stance"], got["release_hash"], got["release_ts"]) == (1, 2, H2, TS2)


def test_resolution_is_order_independent(c):
    base = [SYMS["C0"], SYMS["N"], SYMS["O"]]
    seen = {
        json.dumps(c.resolve_verdict(json.dumps([list(s) for s in perm])), sort_keys=True)
        for perm in itertools.permutations(base)
    }
    assert len(seen) == 1


@pytest.mark.parametrize(
    "bad",
    [
        "not json", "{}", "[]", json.dumps([["CONFIRMED", 0, H0, TS0]] * 2),
        json.dumps([["CONFIRMED", 0, H0, TS0]] * 4),
        json.dumps([["BOGUS", 0, "", 0]] * 3),
        json.dumps([["CONFIRMED", 7, H0, TS0]] * 3),
        json.dumps([["CONFIRMED", True, H0, TS0]] * 3),
        json.dumps([["CONFIRMED", 0]] * 3),
        json.dumps([["CONFIRMED", 0, H0]] * 3),  # the old 3-field shape
        json.dumps([["CONFIRMED", 0, H0, -1]] * 3),
        json.dumps([["CONFIRMED", 0, H0, "x"]] * 3),
    ],
)
def test_resolve_verdict_rejects_malformed_input(direct_vm, c, bad):
    with direct_vm.expect_revert("ERR_FORGED_PAYLOAD"):
        c.resolve_verdict(bad)


# ---------------------------------------------------------------- end to end
EP = EP_HAWKISH
OK = feed_doc(EP[0], EP[1], EP[2])
RET = retracted_doc(EP[0])
NF = NOT_FOUND
OUT = OUTAGE
HTTP = {"OK": OK, "RET": RET, "NF": NF, "OUT": OUT}


def http_expected(combo):
    states = []
    for k in combo:
        states.append({"OK": SYMS["C0"], "RET": SYMS["R"], "NF": SYMS["N"], "OUT": SYMS["O"]}[k])
    return oracle(states)


HTTP_COMBOS = list(itertools.product(HTTP.keys(), repeat=3))


@pytest.mark.parametrize("combo", HTTP_COMBOS, ids=lambda t: "-".join(t))
def test_full_flow_verdict_and_bond_routing(direct_vm, direct_deploy, direct_alice, direct_bob, combo):
    c = new_contract(direct_deploy, direct_vm, direct_alice)
    set_feeds(direct_vm, *(HTTP[k] for k in combo))
    res = propose(c, direct_vm, direct_bob, EP[0])
    verdict = http_expected(combo)
    assert res["verdict"] == verdict
    ov = c.get_protocol_overview()
    bob = addr_hex(c, direct_vm, direct_bob)
    if verdict == 1:  # VERIFIED: bond escrowed, nothing paid or forfeited
        assert res["status"] == "STAGED"
        assert (ov["total_escrow"], ov["treasury"], c.claimable_of(bob)) == (str(BOND), "0", "0")
        assert ov["pending_epoch"] == EP[0] and res["stance"] == EP[3]
    elif verdict == 2:  # QUORUM_FAILURE: bond comes straight back, retryable
        assert res["status"] == "QUORUM_FAILED"
        assert (ov["total_escrow"], ov["treasury"], c.claimable_of(bob)) == ("0", "0", str(BOND))
        assert ov["pending_epoch"] == ""
    else:  # REJECTED_RELEASE: bond forfeited to the treasury
        assert res["status"] == "REJECTED"
        assert (ov["total_escrow"], ov["treasury"], c.claimable_of(bob)) == ("0", str(BOND), "0")
    # Whatever happened, the bond is conserved.
    total = int(ov["total_escrow"]) + int(ov["treasury"]) + int(ov["total_claimable"])
    assert total == BOND


def test_spec_headline_confirmed_404_outage_is_quorum_failure(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = new_contract(direct_deploy, direct_vm, direct_alice)
    set_feeds(direct_vm, OK, NF, OUT)
    res = propose(c, direct_vm, direct_bob, EP[0])
    assert res["verdict_name"] == "QUORUM_FAILURE"
    assert c.claimable_of(addr_hex(c, direct_vm, direct_bob)) == str(BOND)
    assert c.get_protocol_overview()["treasury"] == "0"


def test_unmocked_host_counts_as_outage_not_vote(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = new_contract(direct_deploy, direct_vm, direct_alice)
    direct_vm.clear_mocks()
    direct_vm.mock_web(rf"https://{HOSTS['a']}/.*", OK)
    direct_vm.mock_web(rf"https://{HOSTS['b']}/.*", OK)
    # host c has no mock -> the fetch raises -> OUTAGE; A+B still reach quorum
    res = propose(c, direct_vm, direct_bob, EP[0])
    assert res["verdict"] == 1 and res["source_count"] == 2


def test_quorum_failure_epoch_can_be_retried_and_then_verified(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = new_contract(direct_deploy, direct_vm, direct_alice)
    set_feeds(direct_vm, OK, NF, OUT)
    assert propose(c, direct_vm, direct_bob, EP[0])["verdict"] == 2
    set_feeds(direct_vm, OK, OK, OK)  # lag cleared, outage over
    res = propose(c, direct_vm, direct_bob, EP[0])
    assert res["verdict"] == 1 and res["attempts"] == 2
    assert c.get_epoch_count() == 1  # same epoch slot, not a new one


def test_rejected_epoch_can_be_retried_once_feeds_publish(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = new_contract(direct_deploy, direct_vm, direct_alice)
    set_feeds(direct_vm, NF, NF, NF)
    assert propose(c, direct_vm, direct_bob, EP[0])["verdict"] == 0
    set_feeds(direct_vm, OK, OK, NF)
    assert propose(c, direct_vm, direct_bob, EP[0])["verdict"] == 1
