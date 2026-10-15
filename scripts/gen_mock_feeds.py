"""Generate the deterministic mock macro feeds under deployments/mock_feeds/.

Three directories (a, b, c) are served by three independent hosts:

    a  raw.githubusercontent.com   FOMC / monetary release mirror
    b  cdn.jsdelivr.net/gh         FRED macro feed mirror
    c  rawcdn.githack.com          BLS CPI / rate feed mirror

Every file is `<epoch_id>.json`. Scenarios deliberately differ per slot so the
repository doubles as a live demonstration of each quorum outcome:

    2026-Q1-FOMC .. 2026-Q4-FOMC   clean 3/3 releases, one per stance tier
    2026-Q4-LAG                    A+B confirmed, C not yet published  -> VERIFIED 2/3
    2026-Q4-SPLIT                  three conflicting stances           -> REJECTED
    2026-Q4-RETRACT                every source retracted              -> REJECTED
    2026-Q4-ONEDOWN                only A published (B, C 404)         -> REJECTED
    2026-Q4-REVISE                 clean today; flip to RETRACTED in a commit
                                   to demonstrate the challenge path

    .venv/bin/python scripts/gen_mock_feeds.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "deployments" / "mock_feeds"


def confirmed(epoch, rate, cpi, source, ts):
    return {
        "epoch_id": epoch,
        "status": "CONFIRMED",
        "rate_change_bps": rate,
        "cpi_yoy_bps": cpi,
        "release_ts": ts,
        "source": source,
    }


def retracted(epoch, source):
    return {"epoch_id": epoch, "status": "RETRACTED", "source": source}


SOURCES = {"a": "FOMC-MIRROR", "b": "FRED-MIRROR", "c": "BLS-MIRROR"}

# Publication times (unix seconds, UTC). `release_ts` is the release's identity
# for ordering: the contract never stages or executes a release that is not
# strictly newer than the live one. Every fixture is in the past relative to the
# demo date, and a revision of an epoch must carry a LATER release_ts.
TS = {
    "2026-Q1-FOMC": 1769625600,    # 2026-01-28T18:40:00Z
    "2026-Q2-FOMC": 1777489200,    # 2026-04-29T19:00:00Z
    "2026-Q3-FOMC": 1789587600,    # 2026-09-16T18:20:00Z
    "2026-Q4-FOMC": 1792000000,    # 2026-10-14T18:26:40Z
    "2026-Q4-REVISE": 1792001000,
    "2026-Q4-LAG": 1792002000,
    "2026-Q4-SPLIT": 1792003000,
    "2026-Q4-ONEDOWN": 1792004000,
}

CLEAN = {
    "2026-Q1-FOMC": (-25, 190),  # DOVISH
    "2026-Q2-FOMC": (0, 250),    # NEUTRAL
    "2026-Q3-FOMC": (25, 320),   # HAWKISH
    "2026-Q4-FOMC": (-25, 210),  # DOVISH (cut)
    "2026-Q4-REVISE": (25, 310), # HAWKISH
}

files: dict[str, dict[str, dict]] = {"a": {}, "b": {}, "c": {}}
for epoch, (rate, cpi) in CLEAN.items():
    for slot, src in SOURCES.items():
        files[slot][epoch] = confirmed(epoch, rate, cpi, src, TS[epoch])

# LAG: C has not published yet (file absent -> HTTP 404).
for slot in ("a", "b"):
    files[slot]["2026-Q4-LAG"] = confirmed("2026-Q4-LAG", 25, 330, SOURCES[slot], TS["2026-Q4-LAG"])

# SPLIT: conflicting stances on every source.
files["a"]["2026-Q4-SPLIT"] = confirmed("2026-Q4-SPLIT", 50, 350, SOURCES["a"], TS["2026-Q4-SPLIT"])   # HAWKISH
files["b"]["2026-Q4-SPLIT"] = confirmed("2026-Q4-SPLIT", -50, 150, SOURCES["b"], TS["2026-Q4-SPLIT"])  # DOVISH
files["c"]["2026-Q4-SPLIT"] = confirmed("2026-Q4-SPLIT", 0, 250, SOURCES["c"], TS["2026-Q4-SPLIT"])    # NEUTRAL

for slot, src in SOURCES.items():
    files[slot]["2026-Q4-RETRACT"] = retracted("2026-Q4-RETRACT", src)

files["a"]["2026-Q4-ONEDOWN"] = confirmed("2026-Q4-ONEDOWN", 25, 330, SOURCES["a"], TS["2026-Q4-ONEDOWN"])

for slot, per_epoch in files.items():
    d = ROOT / slot
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("*.json"):
        old.unlink()
    for epoch, doc in per_epoch.items():
        (d / f"{epoch}.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
print("wrote", sum(len(v) for v in files.values()), "feed files")
