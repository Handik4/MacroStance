/**
 * GenLayer SDK integration for the MacroStance contract.
 *
 *  - Reads use an account-less client (`gen_call` needs no signer), so the
 *    terminal shows live protocol state before any wallet connects.
 *  - Writes use an EIP-1193 provider; the SDK routes signing to the wallet and
 *    the key never enters this bundle.
 *  - Fees come from the SDK: `estimateTransactionFeesForWrite` quotes the live
 *    fee policy for this exact call and the quote is passed straight to
 *    `writeContract`. There are no hard-coded gas or fee overrides anywhere.
 *  - A write is reported successful only through `assertStrictSuccess`
 *    (ACCEPTED|FINALIZED + FINISHED_WITH_RETURN + MAJORITY_AGREE).
 */

import { createClient } from "genlayer-js";
import { studioDevnet } from "genlayer-js/chains";

import { isAddress, normalizeAddress, parseWei } from "./inputs";
import { assertStrictSuccess, type ReceiptLike } from "./receipt";
import { isValidEpochId } from "./stance";

export const CHAIN_ID = 61997;
export const RPC_URL =
  process.env.NEXT_PUBLIC_GENLAYER_RPC_URL ?? "https://studio-next.genlayer.com/api";
export const EXPLORER_URL =
  process.env.NEXT_PUBLIC_EXPLORER_URL ?? "https://explorer-studio-next.genlayer.com";
/**
 * The Studio Next deployment recorded in deployments/studio-next.json. The
 * address is public; override per environment with NEXT_PUBLIC_MACROSTANCE_ADDRESS.
 */
export const DEPLOYED_ADDRESS = "0x6F5836e013b56738e1c27BDbFA36fB001369D867";
export const CONTRACT_ADDRESS = (process.env.NEXT_PUBLIC_MACROSTANCE_ADDRESS ?? DEPLOYED_ADDRESS) as string;

export interface Eip1193Provider {
  request(args: { method: string; params?: unknown[] | object }): Promise<unknown>;
  on?(event: string, handler: (...args: unknown[]) => void): void;
  removeListener?(event: string, handler: (...args: unknown[]) => void): void;
}

declare global {
  interface Window {
    ethereum?: Eip1193Provider;
  }
}

// ----------------------------------------------------------- decoded views
export interface Overview {
  governor: string;
  now: number;
  active_stance: number;
  active_stance_name: string;
  active_epoch_id: string;
  active_since: number;
  max_ltv_bps: number;
  borrow_rate_bps: number;
  reserve_factor_bps: number;
  pending_epoch: string;
  last_executed_seq: number;
  active_version: number;
  active_epoch_revision: number;
  proposal_validity_window: number;
  max_epoch_seq: number;
  current_year: number;
  current_quarter: number;
  active_release_ts: number;
  epoch_count: number;
  treasury: string;
  total_escrow: string;
  total_claimable: string;
  total_collateral: string;
  total_scaled_debt: string;
  borrow_index: string;
  reserves_accrued: string;
  reporter_bond: string;
  challenger_bond: string;
  activation_timelock: number;
  feed_rotation_timelock: number;
}

export interface FeedSlot {
  slot: number;
  label: string;
  active_url: string;
  active_host: string;
  staged_url: string;
  staged_host: string;
  activates_at: number;
}

export interface EpochRecord {
  epoch_id: string;
  status: "STAGED" | "EXECUTED" | "EXPIRED" | "CANCELLED" | "REJECTED" | "QUORUM_FAILED";
  verdict: number;
  verdict_name: string;
  stance: number;
  stance_name: string;
  source_count: number;
  release_hash: string;
  reporter: string;
  bond: string;
  staged_at: number;
  activates_at: number;
  attempts: number;
  resolved_by: string;
  release_ts: number;
  base_version: number;
  revision: number;
  expires_at: number;
  seq: number;
}

export interface AccountHealth {
  owner: string;
  collateral: string;
  debt: string;
  stance: number;
  stance_name: string;
  max_ltv_bps: number;
  borrow_rate_bps: number;
  reserve_factor_bps: number;
  ltv_bps: number;
  borrow_capacity: string;
  available_to_borrow: string;
  health_factor_bps: number;
  status: "NO_DEBT" | "HEALTHY" | "OVER_LTV";
}

/** genlayer-js may decode a calldata dict as a Map or a plain object. */
export function toPlain(v: unknown): unknown {
  if (v instanceof Map) {
    const o: Record<string, unknown> = {};
    for (const [k, val] of v.entries()) o[String(k)] = toPlain(val);
    return o;
  }
  if (Array.isArray(v)) return v.map(toPlain);
  if (typeof v === "bigint") {
    return v <= BigInt(Number.MAX_SAFE_INTEGER) && v >= -BigInt(Number.MAX_SAFE_INTEGER)
      ? Number(v)
      : v.toString();
  }
  if (v && typeof v === "object") {
    const o: Record<string, unknown> = {};
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) o[k] = toPlain(val);
    return o;
  }
  return v;
}

export class ContractError extends Error {
  constructor(message: string, readonly cause?: unknown) {
    super(message);
    this.name = "ContractError";
  }
}

export class InputError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InputError";
  }
}

// ------------------------------------------------------------- SDK surface
interface SdkClient {
  readContract(a: {
    address: string;
    functionName: string;
    args?: unknown[];
  }): Promise<unknown>;
  estimateTransactionFeesForWrite(a: {
    address: string;
    functionName: string;
    args?: unknown[];
    value?: bigint;
  }): Promise<{ distribution: unknown; messageAllocations?: unknown; feeValue: bigint }>;
  writeContract(a: {
    address: string;
    functionName: string;
    args?: unknown[];
    value?: bigint;
    fees?: { distribution?: unknown; messageAllocations?: unknown; feeValue?: bigint };
  }): Promise<string>;
  waitForTransactionReceipt(a: {
    hash: string;
    waitUntil?: "decided" | "finalized";
    interval?: number;
    retries?: number;
  }): Promise<ReceiptLike>;
}

function chainFor(rpcUrl: string) {
  return { ...studioDevnet, rpcUrls: { default: { http: [rpcUrl] } } };
}

function sdk(cfg: Record<string, unknown>): SdkClient {
  return createClient(cfg as never) as unknown as SdkClient;
}

export function raceSignal<T>(p: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return p;
  if (signal.aborted) return Promise.reject(new DOMException("aborted", "AbortError"));
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(new DOMException("aborted", "AbortError"));
    signal.addEventListener("abort", onAbort, { once: true });
    p.then(
      (v) => {
        signal.removeEventListener("abort", onAbort);
        resolve(v);
      },
      (e) => {
        signal.removeEventListener("abort", onAbort);
        reject(e);
      },
    );
  });
}

export interface WriteOutcome {
  hash: string;
  receipt: ReceiptLike;
  feeValue: bigint;
}

export class MacroStanceClient {
  private reader: SdkClient | null = null;
  private writer: SdkClient | null = null;
  private writerAccount: string | null = null;

  constructor(
    readonly address: string = CONTRACT_ADDRESS,
    readonly rpcUrl: string = RPC_URL,
  ) {}

  get configured(): boolean {
    return isAddress(this.address);
  }

  private readClient(): SdkClient {
    if (!this.reader) this.reader = sdk({ chain: chainFor(this.rpcUrl) });
    return this.reader;
  }

  /** Bind the write client to `account`; replaces any previous binding. */
  connect(account: string, provider: Eip1193Provider): void {
    if (!isAddress(account)) throw new InputError("Wallet returned a malformed address.");
    this.writer = sdk({ chain: chainFor(this.rpcUrl), account, provider });
    this.writerAccount = normalizeAddress(account);
  }

  disconnect(): void {
    this.writer = null;
    this.writerAccount = null;
  }

  get account(): string | null {
    return this.writerAccount;
  }

  // ------------------------------------------------------------ reads
  async read<T>(functionName: string, args: unknown[] = [], signal?: AbortSignal): Promise<T> {
    if (!this.configured) throw new ContractError("Contract address is not configured.");
    try {
      const raw = await raceSignal(
        this.readClient().readContract({ address: this.address, functionName, args }),
        signal,
      );
      return toPlain(raw) as T;
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") throw e;
      throw new ContractError(`read ${functionName} failed: ${(e as Error)?.message ?? e}`, e);
    }
  }

  getOverview(signal?: AbortSignal) {
    return this.read<Overview>("get_protocol_overview", [], signal);
  }
  getFeedRegistry(signal?: AbortSignal) {
    return this.read<FeedSlot[]>("get_feed_registry", [], signal);
  }
  getEpochCount(signal?: AbortSignal) {
    return this.read<number>("get_epoch_count", [], signal);
  }
  getEpochByIndex(index: number, signal?: AbortSignal) {
    return this.read<EpochRecord>("get_epoch_by_index", [BigInt(index)], signal);
  }
  getEpoch(epochId: string, signal?: AbortSignal) {
    return this.read<EpochRecord>("get_epoch", [epochId], signal);
  }
  async getAccountHealth(owner: string, signal?: AbortSignal) {
    if (!isAddress(owner)) throw new InputError("Malformed account address.");
    return this.read<AccountHealth>("get_account_health", [normalizeAddress(owner)], signal);
  }
  async claimableOf(owner: string, signal?: AbortSignal): Promise<bigint> {
    if (!isAddress(owner)) throw new InputError("Malformed account address.");
    const raw = await this.read<string>("claimable_of", [normalizeAddress(owner)], signal);
    const wei = parseWei(raw);
    if (wei === null) throw new ContractError(`claimable_of returned a non-integer: ${String(raw)}`);
    return wei;
  }

  // ----------------------------------------------------------- writes
  /**
   * Quote fees from the SDK, submit, wait until decided and require a strict
   * success receipt. Throws `ReceiptError` for any other outcome.
   */
  async write(
    functionName: string,
    args: unknown[],
    value: bigint = 0n,
    opts: { interval?: number; retries?: number } = {},
  ): Promise<WriteOutcome> {
    if (!this.configured) throw new ContractError("Contract address is not configured.");
    if (!this.writer || !this.writerAccount) throw new ContractError("Connect a wallet first.");
    if (value < 0n) throw new InputError("Value cannot be negative.");
    const c = this.writer;
    const quote = await c.estimateTransactionFeesForWrite({
      address: this.address,
      functionName,
      args,
      value,
    });
    const fees = {
      distribution: quote.distribution,
      messageAllocations: quote.messageAllocations,
      feeValue: quote.feeValue,
    };
    const hash = await c.writeContract({
      address: this.address,
      functionName,
      args,
      value,
      fees,
    });
    const receipt = await c.waitForTransactionReceipt({
      hash,
      waitUntil: "decided",
      interval: opts.interval ?? 3000,
      retries: opts.retries ?? 200,
    });
    assertStrictSuccess(receipt);
    return { hash, receipt, feeValue: quote.feeValue };
  }

  proposeEpochSync(epochId: string, bondWei: bigint) {
    if (!isValidEpochId(epochId)) throw new InputError("Epoch id must look like 2026-Q4-FOMC.");
    if (bondWei <= 0n) throw new InputError("Bond must be greater than zero.");
    return this.write("propose_epoch_sync", [epochId], bondWei);
  }
  challengeStagedEpoch(epochId: string, bondWei: bigint) {
    if (!isValidEpochId(epochId)) throw new InputError("Epoch id must look like 2026-Q4-FOMC.");
    if (bondWei <= 0n) throw new InputError("Bond must be greater than zero.");
    return this.write("challenge_staged_epoch", [epochId], bondWei);
  }
  executeStanceTransition(epochId: string) {
    if (!isValidEpochId(epochId)) throw new InputError("Epoch id must look like 2026-Q4-FOMC.");
    return this.write("execute_stance_transition", [epochId]);
  }
  claimPayout() {
    return this.write("claim_payout", []);
  }
  depositCollateral(wei: bigint) {
    if (wei <= 0n) throw new InputError("Amount must be greater than zero.");
    return this.write("deposit_collateral", [], wei);
  }
  borrowAgainstCollateral(wei: bigint) {
    if (wei <= 0n) throw new InputError("Amount must be greater than zero.");
    return this.write("borrow_against_collateral", [wei]);
  }
  repay(wei: bigint) {
    if (wei <= 0n) throw new InputError("Amount must be greater than zero.");
    return this.write("repay", [wei]);
  }
  withdrawCollateral(wei: bigint) {
    if (wei <= 0n) throw new InputError("Amount must be greater than zero.");
    return this.write("withdraw_collateral", [wei]);
  }
}

export const explorerAddressUrl = (address: string) => `${EXPLORER_URL}/address/${address}`;
export const explorerTxUrl = (hash: string) => `${EXPLORER_URL}/tx/${hash}`;
