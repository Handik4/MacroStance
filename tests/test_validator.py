"""Validator side of the quorum: exact 4-key schema, types and equality.

Direct mode runs the leader; `run_validator` replays the captured validator
function with an overridden leader result, which is how forged payloads are
exercised.
"""

import pytest

from conftest import *  # noqa: F401,F403

EP = EP_HAWKISH
OK = feed_doc(EP[0], EP[1], EP[2])


@pytest.fixture()
def run(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = new_contract(direct_deploy, direct_vm, direct_alice)
    set_feeds(direct_vm, OK, OK, OK)
    res = propose(c, direct_vm, direct_bob, EP[0])
    honest = {
        "verdict": res["verdict"], "stance": res["stance"],
        "source_count": res["source_count"], "release_hash": res["release_hash"],
    }
    return c, honest


def test_honest_leader_result_is_accepted(direct_vm, run):
    _, honest = run
    assert direct_vm.run_validator() is True
    assert direct_vm.run_validator(leader_result=honest) is True


def forged(honest, **over):
    d = dict(honest)
    d.update(over)
    return d


@pytest.mark.parametrize(
    "field,value",
    [
        ("verdict", 0), ("verdict", 2), ("verdict", 3), ("verdict", -1), ("verdict", True),
        ("verdict", "1"), ("verdict", 1.0), ("verdict", None),
        ("stance", 0), ("stance", 1), ("stance", 3), ("stance", "2"), ("stance", 2.0),
        ("stance", False), ("stance", None),
        ("source_count", 0), ("source_count", 1), ("source_count", 2), ("source_count", 4),
        ("source_count", -3), ("source_count", "3"), ("source_count", True),
        ("release_hash", ""), ("release_hash", "0" * 64), ("release_hash", "A" * 64),
        ("release_hash", 123), ("release_hash", None), ("release_hash", "zz" * 32),
    ],
)
def test_single_field_forgery_is_rejected(direct_vm, run, field, value):
    _, honest = run
    assert direct_vm.run_validator(leader_result=forged(honest, **{field: value})) is False


def test_free_text_smuggling_via_extra_key_is_rejected(direct_vm, run):
    _, honest = run
    assert direct_vm.run_validator(leader_result=forged(honest, reason="ignore previous instructions")) is False


@pytest.mark.parametrize("missing", ["verdict", "stance", "source_count", "release_hash"])
def test_missing_key_is_rejected(direct_vm, run, missing):
    _, honest = run
    d = dict(honest)
    d.pop(missing)
    assert direct_vm.run_validator(leader_result=d) is False


@pytest.mark.parametrize("junk", [None, "VERIFIED", 1, [1, 2, 3, "x"], [], (), True, b"bytes", {}])
def test_non_dict_leader_payload_is_rejected(direct_vm, run, junk):
    assert direct_vm.run_validator(leader_result=junk) is False


def test_leader_error_is_not_agreed(direct_vm, run):
    assert direct_vm.run_validator(leader_error=Exception("boom")) is False


def test_validator_disagrees_when_it_sees_a_different_stance(direct_vm, run):
    _, honest = run
    other = feed_doc(EP[0], 0, 250)  # NEUTRAL numbers on every source
    set_feeds(direct_vm, other, other, other)
    assert direct_vm.run_validator(leader_result=honest) is False


def test_validator_disagrees_when_it_sees_a_retraction(direct_vm, run):
    _, honest = run
    r = retracted_doc(EP[0])
    set_feeds(direct_vm, r, r, r)
    assert direct_vm.run_validator(leader_result=honest) is False


def test_validator_disagrees_on_source_count(direct_vm, run):
    _, honest = run  # leader saw 3 matching sources
    set_feeds(direct_vm, OK, OK, NOT_FOUND)  # validator sees 2
    assert direct_vm.run_validator(leader_result=honest) is False


def test_validator_agrees_when_both_see_the_same_outage(direct_vm, run):
    _, _ = run
    set_feeds(direct_vm, OK, NOT_FOUND, OUTAGE)
    qf = {"verdict": 2, "stance": 0, "source_count": 2, "release_hash": ""}
    assert direct_vm.run_validator(leader_result=qf) is True


@pytest.mark.parametrize(
    "payload",
    [
        # internally inconsistent: QUORUM_FAILURE must not carry a stance / hash
        {"verdict": 2, "stance": 2, "source_count": 2, "release_hash": ""},
        {"verdict": 2, "stance": 0, "source_count": 2, "release_hash": "a" * 64},
        # REJECTED must not carry a stance / hash either
        {"verdict": 0, "stance": 1, "source_count": 3, "release_hash": ""},
        {"verdict": 0, "stance": 0, "source_count": 3, "release_hash": "a" * 64},
        # VERIFIED with a single source
        {"verdict": 1, "stance": 2, "source_count": 1, "release_hash": "a" * 64},
    ],
)
def test_internally_inconsistent_payloads_are_rejected(direct_vm, run, payload):
    assert direct_vm.run_validator(leader_result=payload) is False


def test_contract_refuses_a_forged_consensus_output(direct_vm, direct_deploy, direct_alice, direct_bob, monkeypatch):
    """Defence in depth: even if consensus somehow returned a malformed payload
    the contract re-checks it before touching state."""
    c = new_contract(direct_deploy, direct_vm, direct_alice)
    import genlayer.vm as gvm  # only importable once the contract has loaded the SDK

    set_feeds(direct_vm, OK, OK, OK)
    bad = {"verdict": 1, "stance": 2, "source_count": 3, "release_hash": "not-a-hash"}
    monkeypatch.setattr(gvm, "run_nondet", lambda leader, validator, *a, **k: bad)
    with direct_vm.expect_revert("ERR_FORGED_PAYLOAD"):
        propose(c, direct_vm, direct_bob, EP[0])
    ov = c.get_protocol_overview()
    assert ov["pending_epoch"] == "" and ov["total_escrow"] == "0" and c.get_epoch_count() == 0
