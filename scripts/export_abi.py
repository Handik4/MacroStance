"""Export (or check) deployments/macro_stance_abi.json from the contract.

The ABI is reflected by the linter's `schema` layer, which loads the contract
under the exact GenVM SDK its dependency header pins -- the same reflection the
runner does -- so it cannot drift from the deployed surface the way a
hand-transcribed ABI can.

    GENVM_VERSION=v0.6.0-rc3 .venv/bin/python scripts/export_abi.py          # write
    GENVM_VERSION=v0.6.0-rc3 .venv/bin/python scripts/export_abi.py --check  # CI

`--check` also verifies that every method the frontend calls by name
(`frontend/src/lib/contract.ts`) exists in the ABI with a matching
mutability, and that no write is called as a read or vice versa.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contracts" / "macro_stance.py"
OUT = ROOT / "deployments" / "macro_stance_abi.json"
FRONTEND = ROOT / "frontend" / "src" / "lib" / "contract.ts"

TYPE_MAP = {"int": "uint256", "string": "string", "bool": "bool", "dict": "json", "list": "json", "null": "none"}


def reflect() -> dict:
    exe = Path(sys.executable).parent / "genvm-lint"
    proc = subprocess.run([str(exe), "schema", str(CONTRACT), "--json"], capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout, proc.stderr, file=sys.stderr)
        sys.exit(2)
    data = json.loads(proc.stdout)
    if not data.get("ok"):
        print(proc.stdout, file=sys.stderr)
        sys.exit(2)
    return data["schema"]


def build(schema: dict) -> dict:
    abi = []
    for name, m in sorted(schema["methods"].items()):
        view = bool(m.get("readonly"))
        abi.append(
            {
                "type": "function",
                "name": name,
                "stateMutability": "view" if view else ("payable" if m.get("payable") else "nonpayable"),
                "inputs": [{"name": n, "type": TYPE_MAP.get(t, t)} for n, t in m["params"]],
                "outputs": [{"name": "result", "type": TYPE_MAP.get(m["ret"], m["ret"])}],
            }
        )
    return {
        "contract": "MacroStance",
        "source": "contracts/macro_stance.py",
        "constructor": [{"name": n, "type": TYPE_MAP.get(t, t)} for n, t in schema["ctor"]["params"]],
        "abi": abi,
    }


def frontend_calls() -> dict[str, str]:
    """method name -> 'read' | 'write' as used by the frontend client."""
    src = FRONTEND.read_text()
    calls: dict[str, str] = {}
    for m in re.finditer(r'this\.read<[^>]*>\(\s*"([a-z_]+)"', src):
        calls[m.group(1)] = "read"
    for m in re.finditer(r'this\.write\(\s*"([a-z_]+)"', src):
        calls[m.group(1)] = "write"
    return calls


def main() -> int:
    built = build(reflect())
    text = json.dumps(built, indent=2, sort_keys=False) + "\n"
    if "--check" not in sys.argv:
        OUT.write_text(text)
        print(f"wrote {OUT.relative_to(ROOT)} ({len(built['abi'])} methods)")
        return 0
    problems = []
    if not OUT.exists() or OUT.read_text() != text:
        problems.append("deployments/macro_stance_abi.json is stale; rerun without --check")
    by_name = {e["name"]: e for e in built["abi"]}
    for name, kind in frontend_calls().items():
        e = by_name.get(name)
        if e is None:
            problems.append(f"frontend calls unknown method {name}")
        elif kind == "read" and e["stateMutability"] != "view":
            problems.append(f"frontend reads {name} but it is {e['stateMutability']}")
        elif kind == "write" and e["stateMutability"] == "view":
            problems.append(f"frontend writes {name} but it is a view")
    for p in problems:
        print("MISMATCH:", p, file=sys.stderr)
    if problems:
        return 1
    print(f"ok: ABI current, {len(frontend_calls())} frontend call sites match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
