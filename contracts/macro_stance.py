# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

# MacroStance: Autonomous Macroeconomic Policy Oracle & DeFi Risk Controller.
#
# Ingests a macroeconomic release (FOMC rate decision + CPI YoY) from THREE
# independently-hosted, governor-registered feeds, resolves a 2-of-3 quorum
# into a discrete monetary stance (DOVISH / NEUTRAL / HAWKISH) through a
# validator-checked non-deterministic block, and -- after a 24 hour activation
# timelock that any bonded challenger can break with proof of retraction --
# rebinds an integrated lending vault's risk parameters to the stance's fixed
# table. Risk parameters are bound to the discrete tier, never to raw numbers.
#
# Invariants (each is pinned by the suites under tests/):
#   1. Callers never pass URLs. URLs are registry state; hosts (and their
#      registrable domains) are unique across ACTIVE and STAGED slots; feed
#      rotation is behind a 7 day timelock.
#   2. Stance is pure integer math with fixed rule priority; the BPS table is
#      indexed by stance only.
#   3. Quorum verdicts are exactly {0 REJECTED, 1 VERIFIED, 2 QUORUM_FAILURE};
#      an outage that could still complete 2-of-3 never costs the reporter.
#   4. VERIFIED stances are staged 24h before activation; a retraction inside
#      the window cancels the transition and pays the reporter's bond to the
#      challenger. The step-change guard is the combination of: one pending
#      transition at a time, epochs monotonic by quarter (a stale quarter can
#      never roll parameters back), execution only after the full timelock,
#      and parameters drawn only from the fixed per-tier table.
#   5. The vault is a ledger-denominated risk simulator: collateral is real
#      native GEN in custody, debt is notional units (no tokens are lent).
#      Interest accrues through a RAY-scaled global index, always rounding in
#      the protocol's favour, so there is no dust and no rounding exploit.

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

import genlayer as gl
from genlayer import Address, u256
from genlayer.storage import TreeMap

# genvm-lint matches the bare name `allow_storage` on storage dataclasses.
allow_storage = gl.storage.allow

# --- Error classification ---------------------------------------------------
ERR_UNAUTHORIZED = "ERR_UNAUTHORIZED"
ERR_BOND = "ERR_INSUFFICIENT_BOND"
ERR_EPOCH_ID = "ERR_INVALID_EPOCH_ID"
ERR_EPOCH_STATE = "ERR_INVALID_EPOCH_STATE"
ERR_STALE = "ERR_STALE_EPOCH"
ERR_PENDING = "ERR_TRANSITION_PENDING"
ERR_TIMELOCK = "ERR_TIMELOCK_ACTIVE"
ERR_WINDOW = "ERR_CHALLENGE_WINDOW_CLOSED"
ERR_FEED_URL = "ERR_UNSAFE_FEED_URL"
ERR_FEED_HOST = "ERR_DUPLICATE_FEED_HOST"
ERR_SLOT = "ERR_INVALID_SLOT"
ERR_NO_STAGED = "ERR_NO_STAGED_FEED"
ERR_AMOUNT = "ERR_INVALID_AMOUNT"
ERR_LTV = "ERR_LTV_EXCEEDED"
ERR_COLLATERAL = "ERR_INSUFFICIENT_COLLATERAL"
ERR_NO_DEBT = "ERR_NO_DEBT"
ERR_NO_BALANCE = "ERR_NO_CLAIMABLE_BALANCE"
ERR_TRANSFER = "ERR_TRANSFER_FAILED_RESTORED"
ERR_PAYLOAD = "ERR_FORGED_PAYLOAD"
ERR_STANCE = "ERR_INVALID_STANCE"

# --- Stance tiers and their bound risk tables (basis points) ------------------
DOVISH = 0
NEUTRAL = 1
HAWKISH = 2
STANCE_NAMES = ("DOVISH", "NEUTRAL", "HAWKISH")
# stance -> (max_ltv_bps, borrow_rate_bps, reserve_factor_bps)
RISK_TABLE = (
    (8000, 350, 1000),
    (7500, 550, 1500),
    (6500, 900, 2500),
)

# Stance rule thresholds (basis points). Integer math only, no floats.
HIKE_BPS = 25
CUT_BPS = 25
CPI_HAWKISH_ABOVE_BPS = 300
CPI_DOVISH_BELOW_BPS = 200
# Sanity bounds for a feed's numeric fields; outside them the document is a
# schema error (treated as an OUTAGE of that source, never as a confirmation).
RATE_MIN_BPS = -2000
RATE_MAX_BPS = 2000
CPI_MIN_BPS = -1000
CPI_MAX_BPS = 10000

# --- Quorum verdicts ---------------------------------------------------------
REJECTED_RELEASE = 0
VERIFIED_STANCE = 1
QUORUM_FAILURE = 2
VERDICT_NAMES = ("REJECTED_RELEASE", "VERIFIED_STANCE", "QUORUM_FAILURE")

# Per-source classification inside one quorum round.
SRC_CONFIRMED = "CONFIRMED"
SRC_RETRACTED = "RETRACTED"
SRC_NOT_FOUND = "NOT_FOUND"
SRC_OUTAGE = "OUTAGE"
SOURCE_KINDS = (SRC_CONFIRMED, SRC_RETRACTED, SRC_NOT_FOUND, SRC_OUTAGE)
MAX_FEED_BODY = 4096

# --- Epoch lifecycle ---------------------------------------------------------
EP_STAGED = "STAGED"
EP_EXECUTED = "EXECUTED"
EP_CANCELLED = "CANCELLED"
EP_REJECTED = "REJECTED"
EP_QUORUM_FAILED = "QUORUM_FAILED"
# States from which an epoch may be (re-)proposed. EXECUTED and STAGED are
# terminal / in-flight; the rest are retryable.
RETRYABLE = (EP_CANCELLED, EP_REJECTED, EP_QUORUM_FAILED)

# --- Economics (wei; 1 GEN == 10 ** 18) ---------------------------------------
ATTO = 10**18
REPORTER_BOND = ATTO // 10  # 0.1 GEN minimum
MAX_BOND = 10 * ATTO
CHALLENGER_BOND = ATTO // 10
KEEPER_REWARD = ATTO // 100  # paid from forfeited bonds, never from escrow

# --- Time --------------------------------------------------------------------
ACTIVATION_TIMELOCK = 24 * 3600
FEED_ROTATION_TIMELOCK = 7 * 24 * 3600
SECONDS_PER_YEAR = 365 * 24 * 3600

# --- Vault math --------------------------------------------------------------
BPS = 10000
RAY = 10**27

# --- Feed registry -----------------------------------------------------------
SLOT_COUNT = 3
SLOT_LABELS = (
    "A: FOMC / Monetary Release Mirror",
    "B: FRED Macro Feed Mirror",
    "C: BLS CPI / Rate Feed Mirror",
)
MAX_URL_LEN = 200
_BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".test", ".invalid")
_HOST_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-."


# =========================================================================
# Pure logic. Everything below the storage class is deterministic and takes
# no storage reference, so the same functions run in the leader, in the
# validator and in the read-only preview views.
# =========================================================================
def _ceil_div(a: int, b: int) -> int:
    return -((-a) // b)


def _is_int(v) -> bool:
    # bool is an int subclass in Python: True would otherwise pass as 1.
    return isinstance(v, int) and not isinstance(v, bool)


def _derive_stance(rate_change_bps: int, cpi_yoy_bps: int) -> int:
    """Deterministic stance. Priority: HAWKISH, then DOVISH, else NEUTRAL, so a
    stagflation print (cut + hot CPI) resolves HAWKISH."""
    if rate_change_bps >= HIKE_BPS or cpi_yoy_bps > CPI_HAWKISH_ABOVE_BPS:
        return HAWKISH
    if rate_change_bps <= -CUT_BPS or cpi_yoy_bps < CPI_DOVISH_BELOW_BPS:
        return DOVISH
    return NEUTRAL


def _release_hash(epoch_id: str, rate_change_bps: int, cpi_yoy_bps: int, stance: int) -> str:
    canon = f"{epoch_id}|{rate_change_bps}|{cpi_yoy_bps}|{stance}"
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _parse_epoch_id(epoch_id: str) -> int:
    """`YYYY-Qn-TAG` -> monotonic sequence year*4 + (n-1), or -1 if malformed.
    The strict charset is also what makes the id safe to splice into a URL."""
    if not isinstance(epoch_id, str) or len(epoch_id) > 24:
        return -1
    parts = epoch_id.split("-")
    if len(parts) != 3:
        return -1
    year, quarter, tag = parts
    if len(year) != 4 or not all("0" <= c <= "9" for c in year):
        return -1
    if len(quarter) != 2 or quarter[0] != "Q" or quarter[1] not in "1234":
        return -1
    if not 2 <= len(tag) <= 12:
        return -1
    if not all(("A" <= c <= "Z") or ("0" <= c <= "9") for c in tag):
        return -1
    y = int(year)
    if y < 2000 or y > 2200:
        return -1
    return y * 4 + (int(quarter[1]) - 1)


def _is_hex64(s) -> bool:
    return isinstance(s, str) and len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def _classify_response(status, body, epoch_id: str) -> tuple:
    """(kind, stance, release_hash) for one HTTP response. Only a clean 200
    with a schema-valid document is a CONFIRMED / RETRACTED vote; a 404 is the
    release not being published (yet); EVERYTHING else -- 5xx, 429, redirects,
    transport faults, non-JSON, wrong schema, wrong epoch -- is an OUTAGE, which
    can never be mistaken for a vote and never forfeits a bond on its own."""
    out = (SRC_OUTAGE, 0, "")
    if not _is_int(status):
        return out
    if status == 404:
        return (SRC_NOT_FOUND, 0, "")
    if status != 200:
        return out
    if isinstance(body, str):
        raw = body.encode("utf-8")
    elif isinstance(body, (bytes, bytearray)):
        raw = bytes(body)
    else:
        return out
    if len(raw) == 0 or len(raw) > MAX_FEED_BODY:
        return out
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception:
        return out
    if not isinstance(doc, dict):
        return out
    if doc.get("epoch_id") != epoch_id:
        return out
    st = doc.get("status")
    if st == "RETRACTED":
        return (SRC_RETRACTED, 0, "")
    if st != "CONFIRMED":
        return out
    rate = doc.get("rate_change_bps")
    cpi = doc.get("cpi_yoy_bps")
    # Inline isinstance (not _is_int) so the type checker narrows `rate`/`cpi`.
    if not isinstance(rate, int) or isinstance(rate, bool):
        return out
    if not isinstance(cpi, int) or isinstance(cpi, bool):
        return out
    if rate < RATE_MIN_BPS or rate > RATE_MAX_BPS or cpi < CPI_MIN_BPS or cpi > CPI_MAX_BPS:
        return out
    stance = _derive_stance(rate, cpi)
    return (SRC_CONFIRMED, stance, _release_hash(epoch_id, rate, cpi, stance))


def _fetch_source(url: str, epoch_id: str) -> tuple:
    try:
        res = gl.nondet.web.get(url)
        status = res.status
        body = res.body
    except Exception:
        return (SRC_OUTAGE, 0, "")
    return _classify_response(status, body, epoch_id)


def _resolve_quorum(states) -> dict:
    """Fold the three per-source states into the 4-field validator payload.

    Resolution order (see README verdict table):
      1. any RETRACTED                              -> REJECTED
      2. a (stance, hash) pair held by >= 2 sources -> VERIFIED, unless a third
         CONFIRMED source holds a different stance  -> REJECTED
      3. two CONFIRMED with different stances       -> REJECTED
      4. quorum unreachable even if every down
         source confirmed (best group + outages < 2)-> REJECTED
      5. all three answered cleanly, no match       -> REJECTED
      6. otherwise a down source could still
         complete 2-of-3                            -> QUORUM_FAILURE
    """
    clean = 0
    outages = 0
    retracted = 0
    groups: dict = {}
    stances: list = []
    for kind, stance, rhash in states:
        if kind == SRC_OUTAGE:
            outages += 1
            continue
        clean += 1
        if kind == SRC_RETRACTED:
            retracted += 1
        elif kind == SRC_CONFIRMED:
            groups[(stance, rhash)] = groups.get((stance, rhash), 0) + 1
            stances.append(stance)

    def out(verdict: int, stance: int, count: int, rhash: str) -> dict:
        return {"verdict": verdict, "stance": stance, "source_count": count, "release_hash": rhash}

    if retracted >= 1:
        return out(REJECTED_RELEASE, 0, clean, "")

    best_key = None
    best_n = 0
    for key in sorted(groups.keys()):  # sorted: deterministic tie-break
        if groups[key] > best_n:
            best_key = key
            best_n = groups[key]

    if best_key is not None and best_n >= 2:
        if any(s != best_key[0] for s in stances):
            return out(REJECTED_RELEASE, 0, clean, "")
        return out(VERIFIED_STANCE, best_key[0], best_n, best_key[1])

    if len(set(stances)) > 1:
        return out(REJECTED_RELEASE, 0, clean, "")
    if best_n + outages < 2:
        return out(REJECTED_RELEASE, 0, clean, "")
    if outages == 0:
        return out(REJECTED_RELEASE, 0, clean, "")
    return out(QUORUM_FAILURE, 0, clean, "")


PAYLOAD_KEYS = ("verdict", "stance", "source_count", "release_hash")


def _payload_ok(p) -> bool:
    """Exact 4-key schema + types + cross-field invariants. Used by the
    validator on the leader's result and by the contract before it trusts the
    consensus output (defence in depth against a forged payload)."""
    if not isinstance(p, dict) or len(p) != 4:
        return False
    for k in PAYLOAD_KEYS:
        if k not in p:
            return False
    v = p["verdict"]
    s = p["stance"]
    n = p["source_count"]
    h = p["release_hash"]
    if not (_is_int(v) and _is_int(s) and _is_int(n) and isinstance(h, str)):
        return False
    if v not in (0, 1, 2) or s not in (0, 1, 2) or n < 0 or n > SLOT_COUNT:
        return False
    if v == VERIFIED_STANCE:
        return n >= 2 and _is_hex64(h)
    return s == 0 and h == ""


def _validator_agrees(leader_payload, mine) -> bool:
    """`run_validator` core: leader payload must be schema-valid and equal to
    the validator's own independent re-derivation on all four fields."""
    if not _payload_ok(leader_payload) or not _payload_ok(mine):
        return False
    for k in PAYLOAD_KEYS:
        if leader_payload[k] != mine[k]:
            return False
    return True


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def _apex_of(host: str) -> str:
    # Registrable domain approximated by the last two labels. Conservative:
    # it may reject two genuinely distinct hosts under a public suffix such as
    # co.uk, but it can never accept two hosts that share infrastructure.
    labels = host.split(".")
    return ".".join(labels[-2:])


def _feed_prefix_error(url) -> str:
    """'' when `url` is an acceptable feed directory prefix, else the reason.
    A prefix is `https://host/path/`; the contract appends `<epoch_id>.json`."""
    if not isinstance(url, str) or len(url) == 0 or len(url) > MAX_URL_LEN:
        return "length"
    for c in url:
        o = ord(c)
        if o < 33 or o > 126:
            return "characters"
    if not url.startswith("https://"):
        return "scheme"
    if "%" in url or "\\" in url or "?" in url or "#" in url:
        return "forbidden character"
    try:
        parts = urlsplit(url)
        port = parts.port
    except Exception:
        return "unparseable"
    # `@` is legal in the path (jsDelivr's `repo@ref`) but never in the authority.
    if parts.scheme != "https" or port is not None or parts.username or parts.password or "@" in parts.netloc:
        return "authority"
    host = (parts.hostname or "").lower()
    if host == "" or ":" in parts.netloc or "[" in parts.netloc:
        return "host"
    if not all(c in _HOST_CHARS for c in host):
        return "host"
    labels = host.split(".")
    if len(labels) < 2 or any(len(lab) == 0 or lab[0] == "-" or lab[-1] == "-" for lab in labels):
        return "host"
    tld = labels[-1]
    if len(tld) < 2 or not all("a" <= c <= "z" for c in tld):  # also rejects dotted-quad IPs
        return "host"
    if host == "localhost" or any(host.endswith(s) for s in _BLOCKED_SUFFIXES):
        return "host"
    path = parts.path
    if not path.startswith("/") or not path.endswith("/") or "//" in path:
        return "path"
    for seg in path.split("/"):
        if seg == "." or seg == "..":
            return "path"
    return ""


def _key(addr) -> str:
    """Canonical storage key for an address: lowercase 0x-hex. `Address.as_hex`
    is checksummed (mixed case) while clients pass whatever case they hold, so
    every map key and every lookup goes through this one normaliser."""
    return addr.as_hex.lower()


def _risk_for(stance: int) -> tuple:
    return RISK_TABLE[stance]


# =========================================================================
# Storage
# =========================================================================
@allow_storage
@dataclass
class Epoch:
    status: str
    stance: u256
    release_hash: str
    reporter: Address
    bond: u256
    staged_at: u256
    activates_at: u256
    source_count: u256
    attempts: u256
    last_verdict: u256
    resolved_by: str  # challenger address hex on cancellation, else ""


@allow_storage
@dataclass
class Position:
    collateral: u256
    scaled_debt: u256


class MacroStance(gl.contract.Contract):
    governor: Address

    # Feed registry (slot index 0..2)
    feed_url: TreeMap[u256, str]
    staged_feed_url: TreeMap[u256, str]
    staged_feed_at: TreeMap[u256, u256]  # activation time, 0 == nothing staged

    # Epoch machine
    epochs: TreeMap[str, Epoch]
    epoch_index: TreeMap[u256, str]
    epoch_count: u256
    pending_epoch: str
    last_executed_seq: u256
    active_stance: u256
    active_epoch_id: str
    active_since: u256

    # Value accounting (wei). balance >= sum of the four buckets below.
    claimable: TreeMap[str, u256]
    total_claimable: u256
    total_escrow: u256
    treasury: u256
    total_collateral: u256

    # Vault
    positions: TreeMap[str, Position]
    borrow_index: u256
    index_updated_at: u256
    total_scaled_debt: u256
    reserves_accrued: u256

    def __init__(self, feed_a: str, feed_b: str, feed_c: str):
        self.governor = gl.message.sender_address
        feeds = [feed_a, feed_b, feed_c]
        seen_hosts: list = []
        seen_apex: list = []
        for i in range(SLOT_COUNT):
            err = _feed_prefix_error(feeds[i])
            if err != "":
                raise gl.vm.UserError(f"{ERR_FEED_URL} slot {i}: {err}")
            host = _host_of(feeds[i])
            apex = _apex_of(host)
            if host in seen_hosts or apex in seen_apex:
                raise gl.vm.UserError(f"{ERR_FEED_HOST} slot {i}")
            seen_hosts.append(host)
            seen_apex.append(apex)
            self.feed_url[(i)] = feeds[i]
            self.staged_feed_url[(i)] = ""
            self.staged_feed_at[(i)] = (0)
        self.epoch_count = (0)
        self.pending_epoch = ""
        self.last_executed_seq = (0)
        self.active_stance = (NEUTRAL)
        self.active_epoch_id = ""
        self.active_since = (self._now())
        self.total_claimable = (0)
        self.total_escrow = (0)
        self.treasury = (0)
        self.total_collateral = (0)
        self.borrow_index = (RAY)
        self.index_updated_at = (self._now())
        self.total_scaled_debt = (0)
        self.reserves_accrued = (0)

    # ------------------------------------------------------------------ views
    @gl.public.view
    def get_protocol_overview(self) -> dict:
        t = _risk_for(int(self.active_stance))
        return {
            "governor": self.governor.as_hex,
            "now": self._now(),
            "active_stance": int(self.active_stance),
            "active_stance_name": STANCE_NAMES[int(self.active_stance)],
            "active_epoch_id": self.active_epoch_id,
            "active_since": int(self.active_since),
            "max_ltv_bps": t[0],
            "borrow_rate_bps": t[1],
            "reserve_factor_bps": t[2],
            "pending_epoch": self.pending_epoch,
            "last_executed_seq": int(self.last_executed_seq),
            "epoch_count": int(self.epoch_count),
            "treasury": str(self.treasury),
            "total_escrow": str(self.total_escrow),
            "total_claimable": str(self.total_claimable),
            "total_collateral": str(self.total_collateral),
            "total_scaled_debt": str(self.total_scaled_debt),
            "borrow_index": str(self.borrow_index),
            "reserves_accrued": str(self.reserves_accrued),
            "reporter_bond": str(REPORTER_BOND),
            "challenger_bond": str(CHALLENGER_BOND),
            "activation_timelock": ACTIVATION_TIMELOCK,
            "feed_rotation_timelock": FEED_ROTATION_TIMELOCK,
        }

    @gl.public.view
    def get_active_params(self) -> dict:
        s = int(self.active_stance)
        t = _risk_for(s)
        return {
            "stance": s,
            "stance_name": STANCE_NAMES[s],
            "max_ltv_bps": t[0],
            "borrow_rate_bps": t[1],
            "reserve_factor_bps": t[2],
            "epoch_id": self.active_epoch_id,
            "active_since": int(self.active_since),
        }

    @gl.public.view
    def get_risk_table(self, stance: int) -> dict:
        if stance < 0 or stance > 2:
            raise gl.vm.UserError(f"{ERR_STANCE} {stance}")
        t = _risk_for(stance)
        return {
            "stance": stance,
            "stance_name": STANCE_NAMES[stance],
            "max_ltv_bps": t[0],
            "borrow_rate_bps": t[1],
            "reserve_factor_bps": t[2],
        }

    @gl.public.view
    def get_feed_registry(self) -> list:
        out = []
        for i in range(SLOT_COUNT):
            active = self.feed_url[(i)]
            staged = self.staged_feed_url[(i)]
            out.append(
                {
                    "slot": i,
                    "label": SLOT_LABELS[i],
                    "active_url": active,
                    "active_host": _host_of(active),
                    "staged_url": staged,
                    "staged_host": _host_of(staged) if staged != "" else "",
                    "activates_at": int(self.staged_feed_at[(i)]),
                }
            )
        return out

    @gl.public.view
    def get_epoch(self, epoch_id: str) -> dict:
        if epoch_id not in self.epochs:
            raise gl.vm.UserError(f"{ERR_EPOCH_STATE} unknown epoch")
        return self._epoch_view(epoch_id)

    @gl.public.view
    def has_epoch(self, epoch_id: str) -> bool:
        return epoch_id in self.epochs

    @gl.public.view
    def get_epoch_count(self) -> int:
        return int(self.epoch_count)

    @gl.public.view
    def get_epoch_by_index(self, index: u256) -> dict:
        if index >= self.epoch_count:
            raise gl.vm.UserError(f"{ERR_EPOCH_STATE} index out of range")
        return self._epoch_view(self.epoch_index[index])

    @gl.public.view
    def claimable_of(self, owner_hex: str) -> str:
        key = owner_hex.lower()
        if key not in self.claimable:
            return "0"
        return str(self.claimable[key])

    @gl.public.view
    def whoami(self) -> str:
        return gl.message.sender_address.as_hex

    @gl.public.view
    def get_solvency(self) -> dict:
        owed = (
            int(self.total_claimable)
            + int(self.total_escrow)
            + int(self.treasury)
            + int(self.total_collateral)
        )
        return {"owed": str(owed), "balance": str(self.balance), "solvent": int(self.balance) >= owed}

    @gl.public.view
    def get_account_health(self, owner_hex: str) -> dict:
        key = owner_hex.lower()
        collateral = 0
        scaled = 0
        if key in self.positions:
            p = self.positions[key]
            collateral = int(p.collateral)
            scaled = int(p.scaled_debt)
        s = int(self.active_stance)
        max_ltv = _risk_for(s)[0]
        rate = _risk_for(s)[1]
        idx = self._projected_index(rate)
        debt = _ceil_div(scaled * idx, RAY)
        capacity = collateral * max_ltv // BPS
        over = debt * BPS > collateral * max_ltv
        if debt == 0:
            status = "NO_DEBT"
            ltv = 0
            health = 0
        else:
            status = "OVER_LTV" if over else "HEALTHY"
            ltv = debt * BPS // collateral if collateral > 0 else 1000000000
            health = (collateral * max_ltv // debt) if collateral > 0 else 0
        return {
            "owner": key,
            "collateral": str(collateral),
            "debt": str(debt),
            "stance": s,
            "stance_name": STANCE_NAMES[s],
            "max_ltv_bps": max_ltv,
            "borrow_rate_bps": rate,
            "reserve_factor_bps": _risk_for(s)[2],
            "ltv_bps": ltv,
            "borrow_capacity": str(capacity),
            "available_to_borrow": str(self._max_borrow(collateral, scaled, idx, max_ltv)),
            "health_factor_bps": health,
            "status": status,
        }

    # Pure helpers exposed so clients and tests share one definition of the rules.
    @gl.public.view
    def derive_stance(self, rate_change_bps: int, cpi_yoy_bps: int) -> int:
        return _derive_stance(rate_change_bps, cpi_yoy_bps)

    @gl.public.view
    def parse_epoch_id(self, epoch_id: str) -> int:
        return _parse_epoch_id(epoch_id)

    @gl.public.view
    def feed_prefix_error(self, url: str) -> str:
        return _feed_prefix_error(url)

    @gl.public.view
    def resolve_verdict(self, states_json: str) -> dict:
        """Fold three per-source states `[[kind, stance, hash], x3]` exactly as
        the quorum round does. Pure; used to publish the verdict table."""
        try:
            raw = json.loads(states_json)
        except Exception:
            raise gl.vm.UserError(f"{ERR_PAYLOAD} not JSON")
        if not isinstance(raw, list) or len(raw) != SLOT_COUNT:
            raise gl.vm.UserError(f"{ERR_PAYLOAD} need exactly {SLOT_COUNT} states")
        states = []
        for item in raw:
            if (
                not isinstance(item, list)
                or len(item) != 3
                or item[0] not in SOURCE_KINDS
                or not _is_int(item[1])
                or item[1] not in (0, 1, 2)
                or not isinstance(item[2], str)
            ):
                raise gl.vm.UserError(f"{ERR_PAYLOAD} bad state")
            states.append((item[0], item[1], item[2]))
        return _resolve_quorum(states)

    @gl.public.view
    def classify_feed_response(self, status: int, body: str, epoch_id: str) -> dict:
        kind, stance, rhash = _classify_response(status, body, epoch_id)
        return {"kind": kind, "stance": stance, "release_hash": rhash}

    # --------------------------------------------------- epoch: propose / sync
    @gl.public.write.payable
    def propose_epoch_sync(self, epoch_id: str) -> dict:
        seq = _parse_epoch_id(epoch_id)
        if seq < 0:
            raise gl.vm.UserError(f"{ERR_EPOCH_ID}")
        bond = int(gl.message.value)
        if bond < REPORTER_BOND:
            raise gl.vm.UserError(f"{ERR_BOND} reporter bond below minimum")
        if bond > MAX_BOND:
            raise gl.vm.UserError(f"{ERR_BOND} reporter bond above maximum")
        if self.pending_epoch != "":
            raise gl.vm.UserError(f"{ERR_PENDING}")
        if seq < int(self.last_executed_seq):
            raise gl.vm.UserError(f"{ERR_STALE}")
        attempts = 0
        if epoch_id in self.epochs:
            prev = self.epochs[epoch_id]
            if prev.status not in RETRYABLE:
                raise gl.vm.UserError(f"{ERR_EPOCH_STATE} {prev.status}")
            attempts = int(prev.attempts)

        res = self._run_quorum(epoch_id)
        verdict = int(res["verdict"])
        now = self._now()
        sender = gl.message.sender_address
        status = EP_QUORUM_FAILED
        activates_at = 0
        staged_at = 0
        if verdict == VERIFIED_STANCE:
            status = EP_STAGED
            staged_at = now
            activates_at = now + ACTIVATION_TIMELOCK
            self.total_escrow += bond
            self.pending_epoch = epoch_id
        elif verdict == QUORUM_FAILURE:
            # An outage is not the reporter's fault: the bond comes straight back.
            self._credit(_key(sender), bond)
        else:
            status = EP_REJECTED
            self.treasury += bond
        self._store_epoch(
            epoch_id,
            Epoch(
                status=status,
                stance=(int(res["stance"])),
                release_hash=str(res["release_hash"]),
                reporter=sender,
                bond=(bond if verdict == VERIFIED_STANCE else 0),
                staged_at=(staged_at),
                activates_at=(activates_at),
                source_count=(int(res["source_count"])),
                attempts=(attempts + 1),
                last_verdict=(verdict),
                resolved_by="",
            ),
        )
        return self._epoch_view(epoch_id)

    @gl.public.write.payable
    def challenge_staged_epoch(self, epoch_id: str) -> dict:
        bond = int(gl.message.value)
        if bond < CHALLENGER_BOND:
            raise gl.vm.UserError(f"{ERR_BOND} challenger bond below minimum")
        if bond > MAX_BOND:
            raise gl.vm.UserError(f"{ERR_BOND} challenger bond above maximum")
        if epoch_id not in self.epochs:
            raise gl.vm.UserError(f"{ERR_EPOCH_STATE} unknown epoch")
        ep = self.epochs[epoch_id]
        if ep.status != EP_STAGED:
            raise gl.vm.UserError(f"{ERR_EPOCH_STATE} {ep.status}")
        now = self._now()
        if now >= int(ep.activates_at):
            raise gl.vm.UserError(f"{ERR_WINDOW}")

        res = self._run_quorum(epoch_id)
        verdict = int(res["verdict"])
        challenger = _key(gl.message.sender_address)
        reporter_hex = _key(ep.reporter)
        reporter_bond = int(ep.bond)
        same = (
            verdict == VERIFIED_STANCE
            and int(res["stance"]) == int(ep.stance)
            and str(res["release_hash"]) == ep.release_hash
        )
        outcome = "UPHELD"
        if verdict == QUORUM_FAILURE:
            # Inconclusive (an outage): the challenger is not punished, the
            # staged epoch is left to run its timelock.
            self._credit(challenger, bond)
            outcome = "INCONCLUSIVE"
        elif same:
            # Feeds still confirm the staged release: frivolous challenge,
            # its bond compensates the reporter.
            self._credit(reporter_hex, bond)
        else:
            # Retracted, conflicting or superseded: cancel and slash.
            self.total_escrow -= reporter_bond
            self._credit(challenger, bond + reporter_bond)
            ep.status = EP_CANCELLED
            ep.bond = (0)
            ep.resolved_by = challenger
            ep.last_verdict = (verdict)
            self.epochs[epoch_id] = ep
            self.pending_epoch = ""
            outcome = "CANCELLED"
        view = self._epoch_view(epoch_id)
        view["challenge_outcome"] = outcome
        view["challenge_verdict"] = verdict
        return view

    @gl.public.write
    def execute_stance_transition(self, epoch_id: str) -> dict:
        if epoch_id not in self.epochs:
            raise gl.vm.UserError(f"{ERR_EPOCH_STATE} unknown epoch")
        ep = self.epochs[epoch_id]
        if ep.status != EP_STAGED:
            raise gl.vm.UserError(f"{ERR_EPOCH_STATE} {ep.status}")
        if self._now() < int(ep.activates_at):
            raise gl.vm.UserError(f"{ERR_TIMELOCK}")
        seq = _parse_epoch_id(epoch_id)
        if seq < int(self.last_executed_seq):
            raise gl.vm.UserError(f"{ERR_STALE}")
        # Interest through `now` accrues at the OLD stance's rate.
        self._accrue_global()
        stance = int(ep.stance)
        self.active_stance = (stance)
        self.active_epoch_id = epoch_id
        self.active_since = (self._now())
        self.last_executed_seq = (seq)
        self.pending_epoch = ""
        bond = int(ep.bond)
        reward = KEEPER_REWARD if int(self.treasury) >= KEEPER_REWARD else int(self.treasury)
        self.treasury -= reward
        self.total_escrow -= bond
        self._credit(_key(ep.reporter), bond + reward)
        ep.status = EP_EXECUTED
        ep.bond = (0)
        self.epochs[epoch_id] = ep
        t = _risk_for(stance)
        return {
            "epoch_id": epoch_id,
            "stance": stance,
            "stance_name": STANCE_NAMES[stance],
            "max_ltv_bps": t[0],
            "borrow_rate_bps": t[1],
            "reserve_factor_bps": t[2],
            "reporter_refund": str(bond),
            "keeper_reward": str(reward),
        }

    @gl.public.write
    def claim_payout(self) -> str:
        key = _key(gl.message.sender_address)
        if key not in self.claimable or self.claimable[key] == 0:
            raise gl.vm.UserError(f"{ERR_NO_BALANCE}")
        amount = int(self.claimable[key])
        self.claimable[key] = (0)
        self.total_claimable -= amount
        self._pay(gl.message.sender_address, amount, restore_claimable=key)
        return str(amount)

    # --------------------------------------------------- governance: feeds
    @gl.public.write
    def stage_feed_rotation(self, slot: u256, url_prefix: str) -> dict:
        self._only_governor()
        s = self._slot(slot)
        err = _feed_prefix_error(url_prefix)
        if err != "":
            raise gl.vm.UserError(f"{ERR_FEED_URL} {err}")
        host = _host_of(url_prefix)
        apex = _apex_of(host)
        for j in range(SLOT_COUNT):
            if j == s:
                continue  # a slot may be re-pointed within its own host
            for u in (self.feed_url[(j)], self.staged_feed_url[(j)]):
                if u == "":
                    continue
                h = _host_of(u)
                if h == host or _apex_of(h) == apex:
                    raise gl.vm.UserError(f"{ERR_FEED_HOST} slot {j}")
        activates_at = self._now() + FEED_ROTATION_TIMELOCK
        self.staged_feed_url[(s)] = url_prefix
        self.staged_feed_at[(s)] = (activates_at)
        return {"slot": s, "staged_url": url_prefix, "activates_at": activates_at}

    @gl.public.write
    def activate_feed_rotation(self, slot: u256) -> dict:
        s = self._slot(slot)
        staged = self.staged_feed_url[(s)]
        if staged == "":
            raise gl.vm.UserError(f"{ERR_NO_STAGED}")
        if self._now() < int(self.staged_feed_at[(s)]):
            raise gl.vm.UserError(f"{ERR_TIMELOCK}")
        host = _host_of(staged)
        apex = _apex_of(host)
        for j in range(SLOT_COUNT):
            if j == s:
                continue
            h = _host_of(self.feed_url[(j)])
            if h == host or _apex_of(h) == apex:
                raise gl.vm.UserError(f"{ERR_FEED_HOST} slot {j}")
        self.feed_url[(s)] = staged
        self.staged_feed_url[(s)] = ""
        self.staged_feed_at[(s)] = (0)
        return {"slot": s, "active_url": staged}

    @gl.public.write
    def cancel_feed_rotation(self, slot: u256) -> None:
        self._only_governor()
        s = self._slot(slot)
        if self.staged_feed_url[(s)] == "":
            raise gl.vm.UserError(f"{ERR_NO_STAGED}")
        self.staged_feed_url[(s)] = ""
        self.staged_feed_at[(s)] = (0)

    @gl.public.write
    def transfer_governor(self, new_governor_hex: str) -> None:
        self._only_governor()
        new_gov = Address(new_governor_hex)
        if new_gov == self.governor:
            raise gl.vm.UserError(f"{ERR_UNAUTHORIZED} already governor")
        self.governor = new_gov

    @gl.public.write
    def withdraw_treasury(self, to_hex: str, amount: u256) -> str:
        self._only_governor()
        amt = int(amount)
        if amt <= 0 or amt > int(self.treasury):
            raise gl.vm.UserError(f"{ERR_AMOUNT}")
        self.treasury -= amt
        self._pay(Address(to_hex), amt, restore_treasury=True)
        return str(amt)

    # ----------------------------------------------------------- vault
    @gl.public.write.payable
    def deposit_collateral(self) -> dict:
        amt = int(gl.message.value)
        if amt <= 0:
            raise gl.vm.UserError(f"{ERR_AMOUNT} deposit must be positive")
        self._accrue_global()
        key = _key(gl.message.sender_address)
        collateral, scaled = self._position(key)
        collateral += amt
        self.positions[key] = Position(collateral=(collateral), scaled_debt=(scaled))
        self.total_collateral += amt
        return {"collateral": str(collateral)}

    @gl.public.write
    def borrow_against_collateral(self, amount: u256) -> dict:
        amt = int(amount)
        if amt <= 0:
            raise gl.vm.UserError(f"{ERR_AMOUNT} borrow must be positive")
        self._accrue_global()
        key = _key(gl.message.sender_address)
        collateral, scaled = self._position(key)
        if collateral == 0:
            raise gl.vm.UserError(f"{ERR_COLLATERAL}")
        idx = int(self.borrow_index)
        add = _ceil_div(amt * RAY, idx)  # round UP: recorded debt >= amount
        new_scaled = scaled + add
        new_debt = _ceil_div(new_scaled * idx, RAY)
        max_ltv = _risk_for(int(self.active_stance))[0]
        if new_debt * BPS > collateral * max_ltv:
            raise gl.vm.UserError(f"{ERR_LTV}")
        self.positions[key] = Position(collateral=(collateral), scaled_debt=(new_scaled))
        self.total_scaled_debt += add
        return {"borrowed": str(amt), "debt": str(new_debt), "max_ltv_bps": max_ltv}

    @gl.public.write
    def repay(self, amount: u256) -> dict:
        amt = int(amount)
        if amt <= 0:
            raise gl.vm.UserError(f"{ERR_AMOUNT} repay must be positive")
        self._accrue_global()
        key = _key(gl.message.sender_address)
        collateral, scaled = self._position(key)
        idx = int(self.borrow_index)
        debt = _ceil_div(scaled * idx, RAY)
        if debt == 0:
            raise gl.vm.UserError(f"{ERR_NO_DEBT}")
        if amt >= debt:
            sub = scaled  # full repay zeroes the position exactly: no dust
        else:
            sub = amt * RAY // idx  # round DOWN: never repays more than paid
            if sub == 0:
                raise gl.vm.UserError(f"{ERR_AMOUNT} repay below one scaled unit")
        new_scaled = scaled - sub
        new_debt = _ceil_div(new_scaled * idx, RAY)
        self.positions[key] = Position(collateral=(collateral), scaled_debt=(new_scaled))
        self.total_scaled_debt -= sub
        return {"repaid": str(debt - new_debt), "debt": str(new_debt)}

    @gl.public.write
    def withdraw_collateral(self, amount: u256) -> dict:
        amt = int(amount)
        if amt <= 0:
            raise gl.vm.UserError(f"{ERR_AMOUNT} withdrawal must be positive")
        self._accrue_global()
        key = _key(gl.message.sender_address)
        collateral, scaled = self._position(key)
        if amt > collateral:
            raise gl.vm.UserError(f"{ERR_COLLATERAL}")
        remaining = collateral - amt
        debt = _ceil_div(scaled * int(self.borrow_index), RAY)
        max_ltv = _risk_for(int(self.active_stance))[0]
        if debt * BPS > remaining * max_ltv:
            raise gl.vm.UserError(f"{ERR_LTV}")
        self.positions[key] = Position(collateral=(remaining), scaled_debt=(scaled))
        self.total_collateral -= amt
        try:
            gl.chain.Account(gl.message.sender_address).emit_transfer(amt, on="finalized")
        except Exception:
            self.positions[key] = Position(collateral=(collateral), scaled_debt=(scaled))
            self.total_collateral += amt
            raise gl.vm.UserError(f"{ERR_TRANSFER}")
        return {"withdrawn": str(amt), "collateral": str(remaining)}

    # ------------------------------------------------------------ internals
    def _only_governor(self) -> None:
        if gl.message.sender_address != self.governor:
            raise gl.vm.UserError(f"{ERR_UNAUTHORIZED} governor only")

    def _slot(self, slot) -> int:
        s = int(slot)
        if s < 0 or s >= SLOT_COUNT:
            raise gl.vm.UserError(f"{ERR_SLOT}")
        return s

    def _now(self) -> int:
        # Inside GenVM datetime.now() is the transaction timestamp; the direct
        # harness patches it identically so warp() controls the clock.
        return int(datetime.now(timezone.utc).timestamp())

    def _credit(self, owner_hex: str, amount: int) -> None:
        if amount <= 0:
            return
        cur = int(self.claimable[owner_hex]) if owner_hex in self.claimable else 0
        self.claimable[owner_hex] = (cur + amount)
        self.total_claimable += amount

    def _pay(self, dest: Address, amount: int, restore_claimable: str = "", restore_treasury: bool = False) -> None:
        # Debit-before-transfer; an enqueue-time failure restores the debit.
        try:
            gl.chain.Account(dest).emit_transfer(amount, on="finalized")
        except Exception:
            if restore_claimable != "":
                self.claimable[restore_claimable] = (amount)
                self.total_claimable += amount
            if restore_treasury:
                self.treasury += amount
            raise gl.vm.UserError(f"{ERR_TRANSFER}")

    def _position(self, key: str) -> tuple:
        if key in self.positions:
            p = self.positions[key]
            return (int(p.collateral), int(p.scaled_debt))
        return (0, 0)

    def _store_epoch(self, epoch_id: str, ep: Epoch) -> None:
        if epoch_id not in self.epochs:
            self.epoch_index[self.epoch_count] = epoch_id
            self.epoch_count += 1
        self.epochs[epoch_id] = ep

    def _epoch_view(self, epoch_id: str) -> dict:
        e = self.epochs[epoch_id]
        verdict = int(e.last_verdict)
        stance = int(e.stance)
        return {
            "epoch_id": epoch_id,
            "status": e.status,
            "verdict": verdict,
            "verdict_name": VERDICT_NAMES[verdict],
            "stance": stance,
            "stance_name": STANCE_NAMES[stance],
            "source_count": int(e.source_count),
            "release_hash": e.release_hash,
            "reporter": e.reporter.as_hex,
            "bond": str(e.bond),
            "staged_at": int(e.staged_at),
            "activates_at": int(e.activates_at),
            "attempts": int(e.attempts),
            "resolved_by": e.resolved_by,
            "seq": _parse_epoch_id(epoch_id),
        }

    def _projected_index(self, rate_bps: int) -> int:
        dt = self._now() - int(self.index_updated_at)
        idx = int(self.borrow_index)
        if dt <= 0:
            return idx
        return idx + _ceil_div(idx * rate_bps * dt, BPS * SECONDS_PER_YEAR)

    def _accrue_global(self) -> None:
        now = self._now()
        last = int(self.index_updated_at)
        if now <= last:
            return
        t = _risk_for(int(self.active_stance))
        idx = int(self.borrow_index)
        # Index growth rounds UP so no sequence of tiny accruals can undercharge.
        delta = _ceil_div(idx * t[1] * (now - last), BPS * SECONDS_PER_YEAR)
        interest = _ceil_div(int(self.total_scaled_debt) * delta, RAY)
        self.reserves_accrued += interest * t[2] // BPS
        self.borrow_index = (idx + delta)
        self.index_updated_at = (now)

    def _max_borrow(self, collateral: int, scaled: int, idx: int, max_ltv: int) -> int:
        capacity = collateral * max_ltv // BPS
        debt = _ceil_div(scaled * idx, RAY)
        cand = capacity - debt
        if cand <= 0:
            return 0
        # Ceil rounding can push the post-borrow debt a wei or two over; walk
        # down (bounded: the overshoot is at most idx/RAY + 1) to the exact max.
        for _ in range(4):
            after = _ceil_div((scaled + _ceil_div(cand * RAY, idx)) * idx, RAY)
            if after * BPS <= collateral * max_ltv:
                return cand
            cand -= 1
            if cand <= 0:
                return 0
        return 0

    def _run_quorum(self, epoch_id: str) -> dict:
        urls: list = []
        for i in range(SLOT_COUNT):
            urls.append(str(self.feed_url[(i)]) + epoch_id + ".json")

        def leader_fn() -> dict:
            states = []
            for u in urls:
                states.append(_fetch_source(u, epoch_id))
            return _resolve_quorum(states)

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            states = []
            for u in urls:
                states.append(_fetch_source(u, epoch_id))
            return _validator_agrees(leaders_res.calldata, _resolve_quorum(states))

        res = gl.vm.run_nondet(leader_fn, validator_fn)
        if not _payload_ok(res):
            raise gl.vm.UserError(f"{ERR_PAYLOAD}")
        return {
            "verdict": int(res["verdict"]),
            "stance": int(res["stance"]),
            "source_count": int(res["source_count"]),
            "release_hash": str(res["release_hash"]),
        }
