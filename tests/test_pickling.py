"""run_nondet closures are serialised with cloudpickle on the real network.

Direct mode skips that step unless `check_pickling` is on. A closure that
captures `self`, a storage handle, or a module-level object that cannot be
pickled passes every other direct test and then fails on-chain, so the
non-deterministic round is replayed here with the check enabled and any
"not picklable" warning promoted to a failure.
"""

import warnings

import pytest

from conftest import *  # noqa: F401,F403

EP = EP_HAWKISH


@pytest.mark.parametrize(
    "feeds",
    [
        lambda: (feed_doc(*EP[:3]),) * 3,
        lambda: (feed_doc(*EP[:3]), NOT_FOUND, OUTAGE),
        lambda: (retracted_doc(EP[0]),) * 3,
    ],
    ids=["verified", "quorum-failure", "rejected"],
)
def test_nondet_closures_are_picklable(direct_vm, direct_deploy, direct_alice, direct_bob, feeds):
    c = new_contract(direct_deploy, direct_vm, direct_alice)
    direct_vm.check_pickling = True
    set_feeds(direct_vm, *feeds())
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        res = propose(c, direct_vm, direct_bob, EP[0])
        assert res["verdict"] in (0, 1, 2)
        direct_vm.run_validator()


def test_challenge_closures_are_picklable(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    c = new_contract(direct_deploy, direct_vm, direct_alice)
    all_feeds(direct_vm, EP)
    propose(c, direct_vm, direct_bob, EP[0])
    direct_vm.check_pickling = True
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        challenge(c, direct_vm, direct_charlie, EP[0])
