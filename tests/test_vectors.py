"""Golden parity vectors shared with the TypeScript port.

The vectors are generated FROM THE CONTRACT. The frontend's vitest suite
replays them against its own implementation, so drift in either language
fails a test. Regenerate with `UPDATE_VECTORS=1 pytest tests/test_vectors.py`.
"""

import itertools
import json
import os
from pathlib import Path

import pytest

from conftest import *  # noqa: F401,F403

OUT = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "__fixtures__" / "parity_vectors.json"
RATES = [-100, -50, -26, -25, -24, -11, -10, -1, 0, 1, 10, 11, 24, 25, 26, 50, 100]
CPIS = [-50, 0, 100, 199, 200, 201, 250, 299, 300, 301, 350, 500, 1000]
H = ["a" * 64, "b" * 64, "c" * 64]
SYMS = {
    "C0": ["CONFIRMED", 0, H[0]], "C0b": ["CONFIRMED", 0, H[1]], "C2": ["CONFIRMED", 2, H[2]],
    "R": ["RETRACTED", 0, ""], "N": ["NOT_FOUND", 0, ""], "O": ["OUTAGE", 0, ""],
}
EID = "2026-Q4-FOMC"


def doc(**o):
    d = {"epoch_id": EID, "status": "CONFIRMED", "rate_change_bps": 25, "cpi_yoy_bps": 320}
    d.update(o)
    return json.dumps(d)


CLASSIFY = [
    (200, doc(), EID), (200, doc(rate_change_bps=-25, cpi_yoy_bps=190), EID),
    (200, doc(rate_change_bps=0, cpi_yoy_bps=250), EID),
    (200, json.dumps({"epoch_id": EID, "status": "RETRACTED"}), EID),
    (404, "", EID), (500, doc(), EID), (503, "x", EID), (429, doc(), EID), (301, doc(), EID),
    (200, "not json", EID), (200, "[]", EID), (200, "", EID),
    (200, doc(rate_change_bps="25"), EID), (200, doc(rate_change_bps=True), EID),
    (200, doc(cpi_yoy_bps=25.5), EID), (200, doc(rate_change_bps=2001), EID),
    (200, doc(cpi_yoy_bps=-1001), EID), (200, doc(status="PENDING"), EID),
    (200, doc(epoch_id="2026-Q3-FOMC"), EID), (200, doc(extra="ignored"), EID),
    (200, doc(pad="x" * 5000), EID), (200, doc(rate_change_bps=2000, cpi_yoy_bps=10000), EID),
    # Lexeme-level cases where a naive JSON.parse port would diverge from Python:
    (200, '{"epoch_id": "%s", "status": "CONFIRMED", "rate_change_bps": 25.0, "cpi_yoy_bps": 320}' % EID, EID),
    (200, '{"epoch_id": "%s", "status": "CONFIRMED", "rate_change_bps": 25, "cpi_yoy_bps": 3.2e2}' % EID, EID),
    (200, '{"epoch_id": "%s", "status": "CONFIRMED", "rate_change_bps": 2.5E1, "cpi_yoy_bps": 320}' % EID, EID),
    (200, '{"epoch_id": "%s", "status": "CONFIRMED", "rate_change_bps": -0, "cpi_yoy_bps": 250}' % EID, EID),
    (200, '{"epoch_id": "%s", "status": "CONFIRMED", "rate_change_bps": 0, "rate_change_bps": 25, "cpi_yoy_bps": 320}' % EID, EID),
    (200, '  \n{"epoch_id": "%s", "status": "CONFIRMED", "rate_change_bps": 25, "cpi_yoy_bps": 320}\n ' % EID, EID),
    (200, '\ufeff{"epoch_id": "%s", "status": "CONFIRMED", "rate_change_bps": 25, "cpi_yoy_bps": 320}' % EID, EID),
    (200, '{"epoch_id": "%s", "status": "CONFIRMED", "rate_change_bps": NaN, "cpi_yoy_bps": 320}' % EID, EID),
    (200, '{"epoch\\u005fid": "%s", "status": "CONFIRMED", "rate_change_bps": 25, "cpi_yoy_bps": 320}' % EID, EID),
    (200, '{"epoch_id": "%s", "status": "CONFIRMED", "rate_change_bps": 25, "cpi_yoy_bps": 320} trailing' % EID, EID),
    (200, '{"epoch_id": "%s", "status": "CONFIRMED", "rate_change_bps": 25, "cpi_yoy_bps": 1' + "0" * 400 + '}' % EID if False else '{"epoch_id": "%s", "status": "CONFIRMED", "rate_change_bps": 25, "cpi_yoy_bps": 1%s}' % (EID, "0" * 400), EID),
    (200, '{"epoch_id": "%s", "status": "CONFIRMED", "rate_change_bps": 25, "cpi_yoy_bps": 320, "note": "caf\u00e9"}' % EID, EID),
]


def build(c):
    stance = [
        {"rate_change_bps": r, "cpi_yoy_bps": p, "stance": c.derive_stance(r, p)}
        for r, p in itertools.product(RATES, CPIS)
    ]
    table = [c.get_risk_table(i) for i in range(3)]
    classify = []
    for status, body, eid in CLASSIFY:
        out = c.classify_feed_response(status, body, eid)
        classify.append({"status": status, "body": body, "epoch_id": eid, **out})
    resolve = []
    for combo in itertools.product(SYMS, repeat=3):
        states = [SYMS[k] for k in combo]
        out = c.resolve_verdict(json.dumps(states))
        resolve.append({"states": states, **out})
    epoch_ids = [
        {"epoch_id": e, "seq": c.parse_epoch_id(e)}
        for e in ["2026-Q4-FOMC", "2026-Q1-CPI", "2000-Q1-AB", "2200-Q4-ABCDEFGHIJKL", "2026-Q5-FOMC",
                  "2026-q4-FOMC", "2026-Q4-F", "../2026-Q4-FOMC", "", "2026-Q4-FOMC.json", "1999-Q4-FOMC"]
    ]
    return {"stance": stance, "risk_table": table, "classify": classify, "resolve": resolve, "epoch_ids": epoch_ids}


def test_vectors_match_contract(direct_vm, direct_deploy, direct_alice):
    c = new_contract(direct_deploy, direct_vm, direct_alice)
    built = build(c)
    if os.environ.get("UPDATE_VECTORS") == "1" or not OUT.exists():
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(built, indent=1, sort_keys=True) + "\n")
    assert json.loads(OUT.read_text()) == json.loads(json.dumps(built, sort_keys=True)), (
        "frontend parity vectors are stale; rerun with UPDATE_VECTORS=1"
    )
    assert len(built["stance"]) == len(RATES) * len(CPIS)
    assert len(built["resolve"]) == 216
