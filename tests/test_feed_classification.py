"""Per-source classification: only a clean 200 + valid schema is a vote."""

import json

import pytest

from conftest import *  # noqa: F401,F403

EID = "2026-Q4-FOMC"


@pytest.fixture()
def c(direct_vm, direct_deploy, direct_alice):
    return new_contract(direct_deploy, direct_vm, direct_alice)


def doc(**over):
    d = {"epoch_id": EID, "status": "CONFIRMED", "rate_change_bps": 25, "cpi_yoy_bps": 320}
    d.update(over)
    return json.dumps(d)


def kind(c, status, body, epoch_id=EID):
    return c.classify_feed_response(status, body, epoch_id)["kind"]


def test_valid_confirmed_document(c):
    r = c.classify_feed_response(200, doc(), EID)
    assert r["kind"] == "CONFIRMED" and r["stance"] == HAWKISH and len(r["release_hash"]) == 64


def test_retracted_needs_no_numeric_fields(c):
    body = json.dumps({"epoch_id": EID, "status": "RETRACTED"})
    assert kind(c, 200, body) == "RETRACTED"


def test_retracted_for_a_different_epoch_is_not_a_retraction(c):
    body = json.dumps({"epoch_id": "2026-Q3-FOMC", "status": "RETRACTED"})
    assert kind(c, 200, body) == "OUTAGE"


def test_404_is_not_found(c):
    assert kind(c, 404, "") == "NOT_FOUND"


@pytest.mark.parametrize("status", [100, 201, 204, 301, 302, 304, 400, 401, 403, 408, 410, 418, 429, 500, 502, 503, 504])
def test_every_other_status_is_an_outage(c, status):
    assert kind(c, status, doc()) == "OUTAGE"


@pytest.mark.parametrize(
    "body",
    [
        "", "   ", "not json", "[]", "null", "42", '"CONFIRMED"', "{", doc()[:-1],
        json.dumps({"epoch_id": EID}),
        json.dumps({"epoch_id": EID, "status": "PENDING", "rate_change_bps": 0, "cpi_yoy_bps": 250}),
        json.dumps({"epoch_id": EID, "status": "confirmed", "rate_change_bps": 0, "cpi_yoy_bps": 250}),
        json.dumps({"status": "CONFIRMED", "rate_change_bps": 0, "cpi_yoy_bps": 250}),
        json.dumps({"epoch_id": "2026-Q3-FOMC", "status": "CONFIRMED", "rate_change_bps": 0, "cpi_yoy_bps": 250}),
        json.dumps({"epoch_id": EID, "status": "CONFIRMED", "cpi_yoy_bps": 250}),
        json.dumps({"epoch_id": EID, "status": "CONFIRMED", "rate_change_bps": 0}),
    ],
)
def test_schema_errors_are_outages(c, body):
    assert kind(c, 200, body) == "OUTAGE"


@pytest.mark.parametrize(
    "field,value",
    [
        ("rate_change_bps", "25"), ("rate_change_bps", 25.0), ("rate_change_bps", 25.5),
        ("rate_change_bps", True), ("rate_change_bps", None), ("rate_change_bps", [25]),
        ("cpi_yoy_bps", "320"), ("cpi_yoy_bps", 320.0), ("cpi_yoy_bps", False),
        ("cpi_yoy_bps", None), ("cpi_yoy_bps", {"v": 1}),
    ],
)
def test_non_integer_numerics_are_outages(c, field, value):
    assert kind(c, 200, doc(**{field: value})) == "OUTAGE"


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("rate_change_bps", -2000, "CONFIRMED"), ("rate_change_bps", 2000, "CONFIRMED"),
        ("rate_change_bps", -2001, "OUTAGE"), ("rate_change_bps", 2001, "OUTAGE"),
        ("cpi_yoy_bps", -1000, "CONFIRMED"), ("cpi_yoy_bps", 10000, "CONFIRMED"),
        ("cpi_yoy_bps", -1001, "OUTAGE"), ("cpi_yoy_bps", 10001, "OUTAGE"),
    ],
)
def test_numeric_bounds(c, field, value, expected):
    assert kind(c, 200, doc(**{field: value})) == expected


def test_oversized_body_is_an_outage(c):
    assert kind(c, 200, doc(pad="x" * 5000)) == "OUTAGE"


def test_extra_fields_are_ignored(c):
    assert kind(c, 200, doc(source="FOMC", published_at="2026-12-16", note="hello")) == "CONFIRMED"


def test_release_hash_binds_the_numbers_not_just_the_stance(c):
    a = c.classify_feed_response(200, doc(cpi_yoy_bps=320), EID)
    b = c.classify_feed_response(200, doc(cpi_yoy_bps=380), EID)
    assert a["stance"] == b["stance"] == HAWKISH
    assert a["release_hash"] != b["release_hash"]


def test_release_hash_is_deterministic_and_epoch_bound(c):
    a1 = c.classify_feed_response(200, doc(), EID)
    a2 = c.classify_feed_response(200, doc(), EID)
    other = c.classify_feed_response(200, doc(epoch_id="2026-Q3-FOMC"), "2026-Q3-FOMC")
    assert a1 == a2 and a1["release_hash"] != other["release_hash"]


def test_release_hash_matches_documented_canonical_form(c):
    import hashlib

    expect = hashlib.sha256(f"{EID}|25|320|2".encode()).hexdigest()
    assert c.classify_feed_response(200, doc(), EID)["release_hash"] == expect
