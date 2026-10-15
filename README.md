# MacroStance — Autonomous Macroeconomic Policy Oracle & DeFi Risk Controller

A GenLayer Intelligent Contract that reads a central-bank release from **three
independently hosted feeds**, resolves a **2-of-3 quorum** into one of three
discrete monetary stances (`DOVISH`, `NEUTRAL`, `HAWKISH`), and — after a
**24-hour timelock** that any bonded challenger can break with proof of
retraction — rebinds an on-chain lending vault's risk parameters (max LTV,
borrow rate, reserve factor) to that stance's fixed row.

Lending protocols lag macro shocks by days of governance. An EVM contract cannot
read an FOMC release or a CPI print; GenVM can fetch the web, and its validators
must agree on what they saw. MacroStance turns that into a state machine with a
single discrete output and a human-scale window to object.

| | |
|---|---|
| Network | GenLayer **Studio Next**, chain `61997` |
| Contract | [`0x6F5836e013b56738e1c27BDbFA36fB001369D867`](https://explorer-studio-next.genlayer.com/address/0x6F5836e013b56738e1c27BDbFA36fB001369D867) |
| Source SHA-256 (local == on-chain) | `43d824659dbc7cf11d7863eeb29ac8101c4930118ae89ca65ab1ce81625af0e8` |
| Deploy tx | `0x7473f0d0fb4cfb3e1507ed91473fe9ae348cdb904837efea32d9471148ee6260` |
| Runner pin | `py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng` |

---

## 1. Architecture

```
                      ┌────────────────────── on-chain registry (governor, 7-day timelock) ──────────────────────┐
                      │  slot A  raw.githubusercontent.com   FOMC / monetary release mirror                     │
                      │  slot B  cdn.jsdelivr.net            FRED macro feed mirror                             │
                      │  slot C  rawcdn.githack.com          BLS CPI / rate feed mirror                         │
                      └───────────────┬──────────────────────────┬──────────────────────────┬───────────────────┘
                                      │ GET <prefix><epoch>.json │                          │
   keeper ── propose_epoch_sync ──►   ▼                          ▼                          ▼
   (bond ≥ 0.1 GEN, epoch id only)  CONFIRMED / RETRACTED / 404 / OUTAGE   per source, classified identically
                                      └──────────────────────────┬───────────────────────────┘
                                                                 ▼
                             leader: {verdict, stance, source_count, release_hash, release_ts}   (exactly 5 keys)
                             validators: re-fetch independently, require exact equality on all five
                                                                 │
              ┌──────────────────────────────┬───────────────────┴──────────────┐
              ▼                              ▼                                  ▼
       0 REJECTED_RELEASE            2 QUORUM_FAILURE                    1 VERIFIED_STANCE
       bond → treasury               bond → reporter (retry)             stage 24 h, bond escrowed
                                                                                │
                              challenge_staged_epoch (bonded) ◄── until executed or expired ┤
                              retraction / conflict ⇒ CANCELLED,                │ after 24 h, anyone
                              reporter bond → challenger                        ▼
                                                                  execute_stance_transition
                                                                  active stance ← tier row; bond + keeper reward → reporter
                                                                                │
                                                                                ▼
                                          vault: max_ltv_bps / borrow_rate_bps / reserve_factor_bps  (integer BPS, ceil-in-protocol's-favour)
```

### Repository layout

```
contracts/macro_stance.py        the Intelligent Contract (single file)
deployments/
  mock_feeds/{a,b,c}/            deterministic feed fixtures, one directory per slot
  macro_stance_abi.json          ABI reflected from the contract (scripts/export_abi.py)
  studio-next.json               current deployment record
  history/                       superseded deployment records
deploy/deploy_macro_stance.ts    `genlayer deploy` script with strict receipt + source-hash verification
scripts/export_abi.py            ABI export and frontend call-site check
scripts/gen_mock_feeds.py        regenerates deployments/mock_feeds
scripts/verify_feeds.py           live HTTP check: every fixture on every registry host
tests/                           864 direct-mode tests (pytest)
frontend/                        Next.js 16.4.1 + React 19 + Tailwind 4 terminal UI, 806 vitest tests
```

---

## 2. Invariants and where they are enforced

| # | Invariant | Enforced by | Pinned by |
|---|---|---|---|
| 1 | Callers never pass URLs; hosts unique across active **and** staged slots; 7-day rotation timelock | `propose_epoch_sync(epoch_id)` takes only an id (grammar `YYYY-Qn-TAG`, which is also what makes it URL-safe); `stage_feed_rotation` checks every other slot's active and staged host **and registrable domain**; `activate_feed_rotation` re-checks | `test_feed_registry.py`, `test_epoch_ids.py` |
| 2 | Deterministic stance buckets; parameters bound to the tier only | `_derive_stance` (integer math, HAWKISH → DOVISH → NEUTRAL); `RISK_TABLE` indexed by stance | `test_stance_rules.py`, TS parity vectors |
| 3 | 2-of-3 quorum; an outage that could complete quorum never costs the reporter | `_resolve_quorum`; exact 5-key payload in `_payload_ok` / `_validator_agrees` | `test_quorum_resolution.py` (independent oracle, exhaustive), `test_validator.py` |
| 4 | 24 h activation timelock; a retraction aborts the transition **at any time before execution** | `activates_at`; `challenge_staged_epoch` re-queries the feeds and is callable throughout the 7-day validity window; only one pending transition; epochs monotonic by quarter | `test_epoch_lifecycle.py`, `test_challenge.py`, `test_audit_recheck.py` |
| 4a | An older release can never replace a newer one | feed documents carry `release_ts`; nothing at or below `active_release_ts` (or the epoch's own `executed_epoch_releases` entry) can be staged or executed (`ERR_STALE_REVISION_ROLLBACK`); a challenge also revokes the exact cancelled release | `test_audit_recheck.py`, `test_temporal_invariants.py` |
| 4b | A staged transition cannot be applied arbitrarily late | `PROPOSAL_VALIDITY_WINDOW` = 7 d after `activates_at`; past it `execute_stance_transition` *commits* an `EXPIRED` status and releases the bond | `test_temporal_invariants.py` |
| 4c | An epoch cannot name a quarter that has not begun | `seq ≤ quarter(block clock)`, else `ERR_FUTURE_EPOCH`; checked before any network or bond handling | `test_audit_recheck.py` |
| 5 | Vault enforces the active tier in integer BPS, zero dust | RAY-scaled global index, ceil in the protocol's favour, LTV checked by cross-multiplication | `test_vault.py`, `test_accounting.py` |

**On "step-change guard".** The brief names it without defining it. It is
implemented as the combination that actually prevents a bad step: one pending
transition at a time, execution only after the full timelock, epochs monotonic
by quarter (a stale quarter can never roll parameters back), and parameters
drawn only from the fixed per-tier table. It does *not* forbid a direct
DOVISH→HAWKISH jump — a real shock may need one, and forcing an intermediate
tier would require fabricating a release.

### Stance table

| Tier | Trigger (checked in this priority) | Max LTV | Borrow | Reserve factor |
|---|---|---|---|---|
| `2 HAWKISH` | rate hike ≥ 25 bps **or** CPI YoY > 300 bps | 6500 | 900 | 2500 |
| `0 DOVISH` | rate cut ≥ 25 bps **or** CPI YoY < 200 bps | 8000 | 350 | 1000 |
| `1 NEUTRAL` | otherwise | 7500 | 550 | 1500 |

A cut with hot CPI (stagflation) resolves HAWKISH. Feed documents carry the raw
`rate_change_bps` and `cpi_yoy_bps`; the contract derives the stance and rejects
a document that tries to assert one.

A `CONFIRMED` feed document is:

```json
{ "epoch_id": "2026-Q3-FOMC", "status": "CONFIRMED",
  "rate_change_bps": 25, "cpi_yoy_bps": 320, "release_ts": 1789587600 }
```

`release_ts` (integer unix seconds, 2000–2100) is the release's identity for
ordering and is folded into `release_hash`
(`sha256("epoch|rate|cpi|stance|release_ts")`): a re-publication with identical
numbers and a later timestamp is a *newer* release, and sources only match when
they agree on it. A document without it is a schema error (an outage of that
source). A `RETRACTED` document needs only `epoch_id` and `status`.

---

## 3. Verdict resolution `[A, B, C]`

Per source: **C** = `200` + valid schema + `CONFIRMED` (same stance, release
hash and `release_ts` as its peers), **D** = `CONFIRMED` with a *different* stance from C (C and D are symmetric: `3×D` verifies exactly like `3×C`), **R** =
`200` + `RETRACTED`, **N** = `404`, **O** = outage (5xx, 429, redirect, timeout,
transport fault, bad JSON, wrong schema, wrong epoch id, out-of-range or
non-integer numerics, body > 4 KiB).

Rules, in order:

1. any **R** → `REJECTED_RELEASE`
2. two sources hold the same `(stance, release_hash)` → `VERIFIED_STANCE`, unless a third **confirmed** source holds a different stance → `REJECTED_RELEASE`
3. two confirmed sources with different stances → `REJECTED_RELEASE`
4. even if every down source confirmed, no 2-of-3 is reachable → `REJECTED_RELEASE`
5. all three answered cleanly and none match → `REJECTED_RELEASE`
6. otherwise a down source could still complete the quorum → `QUORUM_FAILURE` (retryable, **bond returned**)

Order of `A, B, C` is irrelevant (tested). Full table by multiset, generated from
the contract's own logic (`source_count` is the matching group's size when
verified, otherwise the number of clean responses):

| Sources (any order) | Verdict | Reported `source_count` |
|---|---|---|
| 3×C | **VERIFIED_STANCE** | 3 |
| 2×C + D | **REJECTED_RELEASE** | 3 |
| 2×C + R | **REJECTED_RELEASE** | 3 |
| 2×C + N | **VERIFIED_STANCE** | 2 |
| 2×C + O | **VERIFIED_STANCE** | 2 |
| C + 2×D | **REJECTED_RELEASE** | 3 |
| C + D + R | **REJECTED_RELEASE** | 3 |
| C + D + N | **REJECTED_RELEASE** | 3 |
| C + D + O | **REJECTED_RELEASE** | 2 |
| C + 2×R | **REJECTED_RELEASE** | 3 |
| C + R + N | **REJECTED_RELEASE** | 3 |
| C + R + O | **REJECTED_RELEASE** | 2 |
| C + 2×N | **REJECTED_RELEASE** | 3 |
| C + N + O | **QUORUM_FAILURE** | 2 |
| C + 2×O | **QUORUM_FAILURE** | 1 |
| 3×D | **VERIFIED_STANCE** | 3 |
| 2×D + R | **REJECTED_RELEASE** | 3 |
| 2×D + N | **VERIFIED_STANCE** | 2 |
| 2×D + O | **VERIFIED_STANCE** | 2 |
| D + 2×R | **REJECTED_RELEASE** | 3 |
| D + R + N | **REJECTED_RELEASE** | 3 |
| D + R + O | **REJECTED_RELEASE** | 2 |
| D + 2×N | **REJECTED_RELEASE** | 3 |
| D + N + O | **QUORUM_FAILURE** | 2 |
| D + 2×O | **QUORUM_FAILURE** | 1 |
| 3×R | **REJECTED_RELEASE** | 3 |
| 2×R + N | **REJECTED_RELEASE** | 3 |
| 2×R + O | **REJECTED_RELEASE** | 2 |
| R + 2×N | **REJECTED_RELEASE** | 3 |
| R + N + O | **REJECTED_RELEASE** | 2 |
| R + 2×O | **REJECTED_RELEASE** | 1 |
| 3×N | **REJECTED_RELEASE** | 3 |
| 2×N + O | **REJECTED_RELEASE** | 2 |
| N + 2×O | **QUORUM_FAILURE** | 1 |
| 3×O | **QUORUM_FAILURE** | 0 |

(The 35 multisets are exhaustively asserted in
`tests/test_quorum_resolution.py` against an independent oracle, and as 216
ordered combinations replayed byte-for-byte by the TypeScript port.)

### Value flows

| Event | Reporter bond | Challenger bond |
|---|---|---|
| `VERIFIED`, then executed | escrowed → refunded + keeper reward (≤ 0.01 GEN, paid from forfeited bonds) | — |
| `QUORUM_FAILURE` | returned immediately | — |
| `REJECTED_RELEASE` | forfeited → treasury | — |
| challenge succeeds (retraction / conflict / superseded / feeds gone) | slashed → challenger | returned |
| challenge fails (still confirmed) | kept | forfeited → reporter |
| challenge inconclusive (outage) | stays escrowed | returned |

Payouts are pull-pattern (`claim_payout`); every wei sits in exactly one of
`total_escrow`, `total_claimable`, `treasury`, `total_collateral`
(`get_solvency`). The treasury is governor-withdrawable.

---

## 4. Threat model

| Threat | Mitigation | Residual risk |
|---|---|---|
| Caller injects a malicious URL (SSRF, path traversal) | No URL parameter exists; epoch id grammar is `^\d{4}-Q[1-4]-[A-Z0-9]{2,12}$`; URL = registry prefix + id + `.json` | Governor can register any `https` host (timelocked) |
| One host registered under several names to fake a quorum | Exact host *and* last-two-label domain unique across active + staged slots, re-checked at activation | The two-label approximation also rejects distinct hosts under `co.uk`-style suffixes (fails safe) |
| Leader smuggles free text or a forged verdict | Payload is exactly 5 typed keys with cross-field invariants; validators recompute and require equality; contract re-validates before touching state | — |
| Outage forfeits an honest reporter | Rule 6 / oracle-checked; unreachable ⇒ retryable | A *persistent* outage of 2 hosts blocks syncing (liveness, not safety) |
| Stale or replayed release rolls parameters back | `seq` monotonic by quarter **and** `release_ts` strictly greater than the live release's, for the same epoch id or any other id; cancelled releases are revoked | The feeds must carry an honest, monotone `release_ts`; a publisher that back-dates a revision cannot supersede (it only fails to apply) |
| Bad release activates before anyone can react | 24 h timelock + bonded challenge re-query, which stays available until the stage executes or expires (a retraction on day 2 or day 5 still aborts it) | Challenger must act within the window; one retracting source is enough to cancel (fail-closed), so a compromised mirror can grief the reporter's bond |
| Feed numbers differ but stance matches | Release hash binds `epoch|rate|cpi|stance`; a differing hash is a different release | — |
| Rounding exploits in the vault | Scaled debt and debt are `ceil`; repay scaled is `floor`; index growth `ceil`; LTV by cross-multiplication; dust repays refused; `available_to_borrow` is a **closed-form inverse** of the borrow rule (exact at any index) | Borrowing exactly `floor(collateral·ltv)` can fail by ≤ `idx/RAY + 1` wei — use `available_to_borrow` |
| Stale mirror re-serves a release a challenge already cancelled | Cancelled hashes are revoked per epoch; proposing one reverts `ERR_STALE_RELEASE` (reporter keeps the bond); execute re-checks | Only *revoked* releases are ordered. Two unrelated hashes for one epoch have no feed-supplied order (see §8) |
| A stage nobody executes sits forever, then applies stale data | 7-day validity window; expired stages release the bond; a stuck stage auto-expires on the next proposal so it cannot block the protocol | — |
| Far-future epoch ids (e.g. `2027-Q4` in 2026) staging and locking out every earlier quarter | Rejected `ERR_FUTURE_EPOCH` unless the quarter has begun on the block clock | The *current* quarter's id is accepted as soon as feeds publish it |
| A far-future `release_ts` becoming a high-water mark that blocks every later release | Static bounds (2000–2100) in every validator, plus a contract guard: more than 24 h ahead of the block clock reverts `ERR_FUTURE_RELEASE` | A release dated up to 24 h ahead is accepted and raises the mark accordingly |
| Account key confusion (checksum vs lowercase) | One canonical lowercase key (found and fixed by a probe test) | — |
| GitHub / CDN caching serves a stale file | Validators agree on whatever they observe; disagreement forces rotation | `raw.githubusercontent.com` caches ~5 min: a retraction can be visible late |
| Governor key compromise | Rotations timelocked; `transfer_governor`; treasury withdrawal | Single key; no multisig. **The deployed governor is the CLI deployer (see §6)** |
| Emitted native transfer fails after the tx | Debit-before-transfer with restore on enqueue failure | Async settlement failures are outside the frame (same as any `emit_transfer`) |

**What the vault is not.** Debt is a **ledger of notional units**: collateral is
real native GEN in custody, but nothing is lent, so `repay` is free. It exists
to demonstrate stance-bound risk parameters, not to be a money market.

---

## 5. Tests and clean-room reproduction

| Suite | Count | What it covers |
|---|---|---|
| `tests/test_quorum_resolution.py` | 317 | 216 source-state combos vs independent oracle; 64 HTTP-level combos with bond routing; spec cases |
| `tests/test_stance_rules.py` | 91 | boundaries, priority, grid vs reference, tier binding |
| `tests/test_feed_registry.py` | 67 | URL validation, duplicate host (active/staged/apex/case), 7-day timelock |
| `tests/test_feed_classification.py` | 78 | per-source schema/status/bounds |
| `tests/test_validator.py` | 66 | forged payloads, wrong types, extra keys, validator disagreement |
| `tests/test_vault.py` | 52 | LTV boundaries, exact interest, reserve factor, rounding, tightening |
| `tests/test_epoch_ids.py` | 42 | grammar |
| `tests/test_audit_recheck.py` | 47 | **recheck**: release-timestamp rollback guard, challenge window through the validity window, current-quarter bound (incl. every quarter boundary) |
| `tests/test_temporal_invariants.py` | 27 | **audit #1 #2 #7**: revoked releases, proposal expiry, large-index borrow ceiling |
| `tests/test_epoch_lifecycle.py` | 36 | bonds, 24 h timelock, monotonic epochs, rewards, claims |
| `tests/test_challenge.py` | 21 | retraction slashing, windows, frivolous / inconclusive challenges |
| `tests/test_mock_feeds.py`, `test_pickling.py`, `test_accounting.py`, `test_vectors.py` | 20 | fixtures, cloudpickle closures, value conservation, TS parity vectors |
| **pytest total** | **864** | |
| `frontend/src/lib/*.test.ts` | **806** | stance/quorum/classification parity vectors incl. `release_ts` and the quarter bound (535), input parsing (76), receipts (39), lending math incl. large-index ceiling (38), strict JSON (34), SDK fee + receipt flow (32), SerialLoader incl. `readAt` (21), account-keyed tx status (14), monotone chain clock (10), unknown-state display (7) |

Roughly 280 of the pytest cases and ~470 of the vitest cases are exhaustive or
golden-vector enumerations; the hand-written scenario tests are the remainder.

### Reproduce from a clean checkout

```bash
# Python: contract lint + direct-mode tests
uv venv --python 3.12
uv pip install --prerelease=allow -r requirements-dev.txt
export GENVM_VERSION=v0.6.0-rc3      # see "Runner pin" below
.venv/bin/genvm-lint check     contracts/macro_stance.py   # 0 errors
.venv/bin/genvm-lint typecheck contracts/macro_stance.py   # 0 errors
.venv/bin/python scripts/export_abi.py --check
.venv/bin/python -m pytest -q                              # 864 passed

# Frontend
cd frontend
npm ci
npx tsc --noEmit
npm run lint                 # eslint, 0 errors / 0 warnings
npm test                      # 806 passed
npm run build                 # static export -> out/
```

**Runner pin.** Studio Next recognises `py-genlayer:5jycge…` and rejects the
other two hashes in circulation (`1jb45…` and `babwp9…` return *runner
malformed*; checked with `gen_getContractSchemaForCode`). `5jycge…` ships in the
`v0.6.0-rc3` GenVM bundle and not in newer ones, and `genvm-lint` / the direct
harness default to the newest bundle, so **`GENVM_VERSION=v0.6.0-rc3` is
required** for the commands above. Without it they fail with `runner … not found`.

Direct mode executes only the leader; validator logic is exercised through
`direct_vm.run_validator`. **No test here has run a validator network** — see §6.

---

## 6. Deployment

`genlayer deploy --rpc https://studio-next.genlayer.com/api` runs
`deploy/deploy_macro_stance.ts`, which:

1. quotes the fee from the SDK (`estimateTransactionFees`, no hard-coded deposit),
2. deploys with the three registry prefixes as constructor arguments,
3. refuses anything but `ACCEPTED|FINALIZED` + `FINISHED_WITH_RETURN` + `MAJORITY_AGREE`,
4. fetches the deployed source over `gen_getContractCode` and compares SHA-256 to the file,
5. reads `get_feed_registry` back and compares it to the constructor arguments,
6. writes `deployments/studio-next.json`.

`MACROSTANCE_DRY_RUN=1` stops after the fee quote; `MACROSTANCE_RECORD_TX=<hash>`
re-runs verification and recording for an existing deployment without submitting
a new transaction.

### Deployments

| | Address | Source SHA-256 | Status |
|---|---|---|---|
| **Current** (third check of `549ee91`) | `0x6F5836e013b56738e1c27BDbFA36fB001369D867` | `43d82465…af0e8` | live; tx `0x7473f0d0…6260`; **commit-pinned registry** |
| Superseded (branch-URL registry) | `0xd13BF8a675565E0cA08383f94dD350cbf70B74F0` | `43d82465…af0e8` | same source, branch prefixes; `deployments/history/studio-next.549ee91.json` |
| Superseded (recheck of `dda2887`) | `0xdb529461aaD3511d6454e93764FE074746286966` | `2e3384c3…bdd3` | `deployments/history/studio-next.0b2cdb2.json` |
| Superseded (original) | `0x3AE6f881382c62811169921Fb77D97a0c18e49B3` | `f0774263…c5163` | `deployments/history/studio-next.dda2887.json` |

Superseded contracts are not upgradable and remain callable; point nothing at them.

### What to know about the recorded deployment

* The current deployment was **decided `ACCEPTED`**, `FINISHED_WITH_RETURN`,
  `MAJORITY_AGREE`, and the script's strict receipt gate passed on the first try.
  Local and on-chain source SHA-256 are identical (step 4 ✔).
* **Registry state read-back is unavailable; calldata verification stands in.**
  Studio Next's `gen_call` has returned a server-side SQLAlchemy session error
  (`Can't reconnect until invalid transaction is rolled back`) for *every*
  contract, including an unrelated known-good one, and for both
  `latest-nonfinal` and `latest-final`. The deploy script therefore records
  `constructor_readback.status`:
  `verified` (live `get_feed_registry` equals the constructor arguments),
  `calldata_verified` (the deploy transaction's own calldata carries exactly the
  three arguments; an *input* check, not a state read — **this is the current
  level**) or `unverified`. Non-`gen_call` RPCs work and were used: the
  transaction (`FINALIZED`, `MAJORITY_AGREE`, 3 AGREE / 2 IDLE), the deployed
  source (`gen_getContractCode`, hash equal) and the schema (30 methods).
  Re-run `MACROSTANCE_RECORD_TX=0x7473… genlayer deploy --rpc …` once the RPC
  recovers to upgrade to `verified`.
* **The deployer / governor is the CLI's active account**
  `0x6Ec5cb7469a661B8E23B4867359893A25116eA19` (the keystore was named for a
  different project). It paid the 0.1 GEN fee deposit and is the contract's
  `governor`. Rotate with `transfer_governor` if that is not what you want.
* **The registry is pinned to a commit.** All three prefixes contain the commit
  SHA `e2cd0c02…` that last changed `deployments/mock_feeds/`. A commit URL names
  immutable bytes, so no CDN can hold a stale copy and nothing needs purging.
  This replaced branch (`main`) prefixes, which three CDNs cache on three clocks:
  across the project's history jsDelivr, then GitHack, then jsDelivr again served
  pre-`release_ts` documents (GitHack answers with `immutable, max-age=1y` and
  has no purge). `python scripts/verify_feeds.py` fetches every fixture from
  every host and checks it against the committed file: **24 documents served and
  valid, 4 scenario fixtures correctly 404, 0 problems**. The branch-ref jsDelivr
  entries were also purged (`purge.jsdelivr.net`, all 8 files `finished`).
  **Changing a fixture needs a new commit and therefore a registry rotation
  (7-day timelock) or a redeploy** — that is the deliberate trade.
* **No live consensus round has been executed on Studio Next**, so validator
  agreement on `run_nondet` is unproven on a real network. A write needs a
  ~0.1 GEN fee deposit on top of the 0.1 GEN bond.
* An earlier version of the deploy script used a wrong numeric table for the
  consensus result (`6`; the SDK maps `1 → MAJORITY_AGREE`) and aborted a
  deployment after it was already decided. It was fixed from the SDK's own
  tables and the frontend classifier has a regression test for it.

---

## 7. Frontend

```bash
cd frontend
cp .env.example .env.local     # optional; defaults to the recorded deployment
npm run dev                    # http://localhost:3000
npm run build && npm run serve # static export in out/
```

Editorial macro-finance terminal: ivory page, navy headers, monospace BPS tables;
`HAWKISH` crimson, `NEUTRAL` blue, `DOVISH` emerald.

| Component | Role |
|---|---|
| `StanceMonitor` | active stance, live BPS row, tier trigger table |
| `FeedQuorumRadar` | reads the on-chain registry, fetches the three feeds in the browser, classifies with the TS port, previews the verdict |
| `EpochSyncDesk` | propose (validated bond), 24 h countdown, execute, bonded challenge, claim, epoch ledger |
| `LendingSandbox` | deposit / borrow / repay / withdraw with pre-flight limits and a per-tier what-if |
| `ProtocolExplainer` | four-card architecture summary |
| `SyncAlert` | honest RPC banner with per-item failures |
| `Footer` | contract chip, explorer links |

Hardening, each with unit tests:

* **SDK fees only.** `estimateTransactionFeesForWrite` → `writeContract({fees})`.
  A test asserts no `gas`/`gasLimit` key is ever passed.
* **Strict finality.** A write succeeds only for `ACCEPTED|FINALIZED` +
  `FINISHED_WITH_RETURN` + `MAJORITY_AGREE`, using the SDK's numeric tables
  (a live receipt exposed the wrong table once; there is now a regression test).
* **Account-scoped state.** One `SerialLoader` per wallet address, reset on
  switch; late results from the old generation are discarded; panels are `key`ed
  by address.
* **`SerialLoader`.** Single-flight with coalesced follow-ups (a polling tick
  never aborts a load), and **per-item `failedIds`** that only the same item's
  later success can clear.
* **Account-keyed transaction banners.** Statuses live in a `TxStatusStore`
  keyed by the originating address; `accountsChanged` purges every other
  account's entries before the new address is rendered, and a late completion
  from the old account is ignored by id.
* **Monotone chain clock.** Activation / challenge / expiry countdowns are
  anchored only on a *verified* overview read (`SerialLoader.readAt` is stamped
  on success) and advance by a steady local interval; a failed RPC read can no
  longer reset the timer, and the estimate never decreases.
* **Unknown is not zero.** Collateral, debt, availability and claimable render
  as `—` while unread or after a failed read, actions are disabled, and
  `SyncAlert` says why.
* **Input validation.** `^(0|[1-9]\d*)(\.\d{1,18})?$` over BigInt; rejects
  negative, zero, `1e3`, hex, `.5`, `5.`, thousands separators, 19 decimals.
* **TS ⇄ Python parity.** Vectors are generated *from the contract*; the strict
  JSON parser exists because `25.0` is a float in Python and an integer in
  `JSON.parse`.

### npm audit disclosure

`npm audit --registry=https://registry.npmjs.org` (the default mirror has no
audit endpoint): **0 vulnerabilities, 0 in production dependencies** (was 12:
1 critical, 9 high, 2 moderate). Clean-room `npm ci` from the committed
lockfile reproduces the same tree.

| Finding (before) | Resolution |
|---|---|
| `next@14.2.35`, **critical** — 23 advisories in the server runtime (Image Optimizer incl. AVIF RCE, Server Components / Actions DoS, rewrites SSRF, middleware / i18n bypass, WebSocket SSRF, cache poisoning) | **Upgraded to `next@16.4.1`**, the first release npm reports clean. This departs from the original brief's 14.2.35 pin; the audit was the stronger requirement. Independently, `output: "export"` ships plain files and **no Next server**, so these server-runtime classes were never reachable from the deployed artifact — kept as defence in depth. `next dev` is a developer-machine surface only |
| `postcss@8.4.31` nested in `next`, high | Next 16.4.1 vendors `postcss@8.5.23` |
| `tailwindcss@3.4.19` → `braces`, `micromatch`, `fast-glob`, `chokidar`, `postcss-nested`, `postcss-selector-parser`, high / moderate | **Migrated to Tailwind 4** (CSS-first `@theme`, `@tailwindcss/postcss`), which drops that glob chain |
| `eslint-config-next` → `@next/eslint-plugin-next` → `fast-glob` → `micromatch` → `braces` (advisory range `*`, **no patched release exists**; npm's suggested "fix" is a downgrade) | **Removed the Next ESLint preset.** ESLint 9 now runs `typescript-eslint`, `eslint-plugin-react-hooks` (incl. the React 19 compiler rules) and `jsx-a11y`. Dropped: Next-specific rules for `<img>`, `<a href="/…">`, `next/script`, pages-router — none used by this static client app. Lint: 0 errors, 0 warnings |
| `vitest`, `@vitest/mocker`, `tinypool` | Upgraded to `vitest@5.0.4` (previous round) |

What the upgrade also surfaced: React 19's hook rules flagged a ref written from
inside a loader callback; that was a real smell (the epoch count was shadowed in
a ref instead of read from loader state) and is fixed rather than suppressed.

---

## 8. Known limitations

* Untested on a live validator network (§6).
* **Release ordering depends on the feeds' `release_ts`.** The contract enforces
  "strictly newer" but cannot know a publisher's timestamp is honest; a mirror that
  serves an old document with a *forged newer* timestamp would still need 2-of-3
  matching sources and survive the bonded challenge to be applied.
* **Feed documents must carry `release_ts`** (integer unix seconds, 2000–2100);
  a document without it is a schema error, i.e. an outage of that source. The
  committed mock feeds were regenerated accordingly.
* **Fixtures are commit-pinned, so they are immutable by design.** Updating a
  fixture means a new commit and a registry rotation (7-day timelock) or a new
  deployment. Epoch ids already executed are unaffected.
* Studio Next's `gen_call` read path was unavailable throughout; live state
  (`get_protocol_overview`, `get_feed_registry`) has not been read back (§6).
* An executed epoch can be revised: re-proposing it succeeds only with a strictly
  newer `release_ts`, and a cancelled or expired revision leaves the previously
  executed stance live.
* Single governor key; no multisig.
* Registrable-domain uniqueness is approximated by the last two labels.
* One pending transition at a time: a staged-but-unexecuted epoch blocks new
  proposals until anyone executes it after 24 h.
* The vault is a simulator; debt is not a token.
* A `404` on two mirrors during a challenge cancels the stage (verdict
  `REJECTED_RELEASE`), by design.
