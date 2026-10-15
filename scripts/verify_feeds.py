"""Verify, over live HTTP, that every committed fixture is served byte-for-byte
(as JSON) by every registry host, and that every CONFIRMED document carries a
valid integer `release_ts`.

The registry prefixes are read from deploy/deploy_macro_stance.ts so this checks
exactly what a deployment would register.

    python scripts/verify_feeds.py            # all fixtures, all three slots
    python scripts/verify_feeds.py --retries 6

Exit status: 0 when every document is served and valid, 1 otherwise.
A 404 is reported separately because some fixtures are deliberately absent from
a slot (scenario fixtures such as LAG and ONEDOWN).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy" / "deploy_macro_stance.ts"
FEEDS_DIR = ROOT / "deployments" / "mock_feeds"
TS_MIN, TS_MAX = 946684800, 4102444800


def registry_prefixes() -> dict[str, str]:
    src = DEPLOY.read_text()
    sha = re.search(r'FIXTURE_COMMIT = "([0-9a-f]{40})"', src)
    if not sha:
        sys.exit("FIXTURE_COMMIT not found in the deploy script")
    urls = re.findall(r"`(https://[^`]+)`", src[src.index("const FEEDS"):])
    if len(urls) != 3:
        sys.exit(f"expected 3 feed prefixes, found {len(urls)}")
    return {s: u.replace("${FIXTURE_COMMIT}", sha.group(1)) for s, u in zip("abc", urls)}


def fetch(url: str, retries: int) -> tuple[int, str]:
    code, body = 0, ""
    for attempt in range(retries):
        r = subprocess.run(
            ["curl", "-sS", "-m", "40", "-w", "\n%{http_code}", url], capture_output=True, text=True
        )
        body, _, c = r.stdout.rpartition("\n")
        code = int(c) if c.isdigit() else 0
        if code in (200, 404):
            return code, body
        time.sleep(2 + 3 * attempt)
    return code, body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--retries", type=int, default=4)
    args = ap.parse_args()
    prefixes = registry_prefixes()
    ok = missing = bad = 0
    for slot, prefix in prefixes.items():
        print(f"slot {slot.upper()}  {prefix}")
        for f in sorted((FEEDS_DIR / slot).glob("*.json")):
            code, body = fetch(prefix + f.name, args.retries)
            local = json.loads(f.read_text())
            problem = ""
            if code != 200:
                problem = f"HTTP {code}"
            else:
                try:
                    remote = json.loads(body)
                except ValueError:
                    remote, problem = None, "not JSON"
                if remote is not None:
                    if remote != local:
                        problem = "differs from the committed fixture"
                    elif remote.get("status") == "CONFIRMED":
                        ts = remote.get("release_ts")
                        if isinstance(ts, bool) or not isinstance(ts, int) or not TS_MIN <= ts <= TS_MAX:
                            problem = f"invalid release_ts {ts!r}"
            if problem:
                bad += 1
                print(f"  BAD  {f.name}: {problem}")
            else:
                ok += 1
        # fixtures that exist in another slot but not this one must really 404
        for other in "abc":
            if other == slot:
                continue
            for f in sorted((FEEDS_DIR / other).glob("*.json")):
                if not (FEEDS_DIR / slot / f.name).exists():
                    code, _ = fetch(prefix + f.name, 1)
                    if code == 404:
                        missing += 1
                    else:
                        bad += 1
                        print(f"  BAD  {f.name}: expected 404, got HTTP {code}")
    print(f"\n{ok} documents served and valid, {missing} correctly absent (404), {bad} problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
