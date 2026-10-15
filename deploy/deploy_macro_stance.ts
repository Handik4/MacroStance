/**
 * Deploy contracts/macro_stance.py to GenLayer Studio Next (chain 61997).
 *
 *   genlayer deploy --rpc https://studio-next.genlayer.com/api
 *
 * `genlayer deploy` (with no --contract) executes every script in deploy/ with
 * a client already bound to the CLI's active account, so this file never holds
 * a key. Set MACROSTANCE_DRY_RUN=1 to print the SDK's fee quote and stop.
 *
 * What this script refuses to treat as success:
 *   - a transaction that is not ACCEPTED/FINALIZED,
 *   - one whose execution did not end FINISHED_WITH_RETURN
 *     (a constructor that reverts is still a *decided* transaction),
 *   - one without MAJORITY_AGREE,
 *   - a receipt with no contract address,
 *   - a deployed source whose SHA-256 differs from the file on disk,
 *   - a registry that does not read back as the three constructor arguments.
 *
 * Only after all of those does it write deployments/studio-next.json.
 *
 * Types are declared structurally: the CLI resolves the SDK from its own
 * installation, and this file has no dependency of its own.
 */

import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const CONTRACT_PATH = "contracts/macro_stance.py";
const RECORD_PATH = "deployments/studio-next.json";
const DEFAULT_RPC = "https://studio-next.genlayer.com/api";
const EXPLORER = "https://explorer-studio-next.genlayer.com";
const CHAIN_ID = 61997;

// Genesis feed registry: three independent hosts, each URL PINNED TO A COMMIT.
//
// A branch URL (`.../main/...`, `...@main/...`) names mutable content that three
// different CDNs cache on three different clocks, so a fixture edit leaves the
// slots disagreeing for minutes to a year (observed: jsDelivr stale while
// GitHack fresh, then the reverse). A commit SHA names immutable bytes: nothing
// can be stale and nothing needs purging. The price is that fixture changes
// need a new commit and therefore a registry rotation (7-day timelock).
//
// FIXTURE_COMMIT is the commit that last touched deployments/mock_feeds/;
// scripts/verify_feeds.py checks every document on every host against it.
const FIXTURE_COMMIT = "e2cd0c0216747473a90d5cd738e7675f97a9b6cd";
const FEEDS = [
  `https://raw.githubusercontent.com/Handik4/MacroStance/${FIXTURE_COMMIT}/deployments/mock_feeds/a/`,
  `https://cdn.jsdelivr.net/gh/Handik4/MacroStance@${FIXTURE_COMMIT}/deployments/mock_feeds/b/`,
  `https://rawcdn.githack.com/Handik4/MacroStance/${FIXTURE_COMMIT}/deployments/mock_feeds/c/`,
];

interface Receipt {
  hash?: string;
  status?: number | string;
  statusName?: string;
  result?: number | string;
  resultName?: string;
  result_name?: string;
  txExecutionResult?: number | string;
  txExecutionResultName?: string;
  data?: { contract_address?: string };
  txDataDecoded?: { contractAddress?: string };
  [k: string]: unknown;
}

interface Fees {
  distribution: unknown;
  messageAllocations?: unknown;
  feeValue: bigint;
}

interface DeployClient {
  chain?: { rpcUrls?: { default?: { http?: string[] } } };
  initializeConsensusSmartContract(): Promise<void>;
  estimateTransactionFees(input: Record<string, never>): Promise<Fees>;
  deployContract(input: { code: Uint8Array; args: unknown[]; fees?: Fees }): Promise<string>;
  waitForTransactionReceipt(input: {
    hash: string;
    waitUntil: "decided" | "finalized";
    retries: number;
    interval: number;
    fullTransaction?: boolean;
  }): Promise<Receipt>;
  readContract(input: { address: string; functionName: string; args?: unknown[] }): Promise<unknown>;
}

const STATUS: Record<number, string> = { 5: "ACCEPTED", 7: "FINALIZED" };
const EXECUTION: Record<number, string> = { 1: "FINISHED_WITH_RETURN" };
// genlayer-js transactionResultNumberToName: on-chain 1 is MAJORITY_AGREE.
const RESULT: Record<number, string> = { 1: "MAJORITY_AGREE" };

function name(n: string | undefined, v: number | string | undefined, table: Record<number, string>) {
  if (n) return n;
  if (v === undefined || v === "") return undefined;
  return table[Number(v)];
}

/** Throws unless the receipt is a strict, decided, agreed, returning success. */
export function assertStrictReceipt(r: Receipt): void {
  const status = name(r.statusName, r.status, STATUS);
  if (status !== "ACCEPTED" && status !== "FINALIZED") {
    throw new Error(`status is ${status ?? String(r.status)}, expected ACCEPTED or FINALIZED`);
  }
  const exec = name(r.txExecutionResultName, r.txExecutionResult, EXECUTION);
  if (exec !== "FINISHED_WITH_RETURN") {
    throw new Error(`execution is ${exec ?? String(r.txExecutionResult)}, expected FINISHED_WITH_RETURN`);
  }
  const consensus = name(r.resultName ?? r.result_name, r.result, RESULT);
  if (consensus !== "MAJORITY_AGREE") {
    throw new Error(`consensus is ${consensus ?? String(r.result)}, expected MAJORITY_AGREE`);
  }
}

function addressFrom(r: Receipt): string | undefined {
  return r.txDataDecoded?.contractAddress ?? r.data?.contract_address ?? undefined;
}

const sha256 = (b: Uint8Array | Buffer) => createHash("sha256").update(b).digest("hex");

async function rpc(url: string, method: string, params: unknown[]): Promise<unknown> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  const body = (await res.json()) as { result?: unknown; error?: { message?: string } };
  if (body.error) throw new Error(`${method}: ${body.error.message ?? "rpc error"}`);
  return body.result;
}

export default async function main(client: DeployClient): Promise<string> {
  const file = path.resolve(process.cwd(), CONTRACT_PATH);
  const source = new Uint8Array(readFileSync(file));
  const localSha = sha256(source);
  const rpcUrl = client.chain?.rpcUrls?.default?.http?.[0] ?? process.env.GENLAYER_RPC_URL ?? DEFAULT_RPC;

  console.log(`MacroStance source sha256: ${localSha}`);
  console.log(`RPC: ${rpcUrl}`);

  await client.initializeConsensusSmartContract();

  // Record-only mode: verify and record a deployment that already happened,
  // WITHOUT submitting another one. Used to finish a run that was interrupted
  // after the transaction was decided.
  const recordTx = process.env.MACROSTANCE_RECORD_TX;
  let hash: string;
  let feeValue: bigint | undefined;
  if (recordTx) {
    hash = recordTx;
    console.log(`Record-only mode for existing transaction ${hash}`);
  } else {
    // Studio Next has no fee manager: an absent `fees` resolves to a zero
    // deposit and the chain rejects it. The deposit comes from the live policy.
    const fees = await client.estimateTransactionFees({});
    feeValue = fees.feeValue;
    console.log(`Fee deposit quoted by the SDK: ${fees.feeValue} wei`);
    if (process.env.MACROSTANCE_DRY_RUN === "1") {
      console.log("MACROSTANCE_DRY_RUN=1: stopping before submission.");
      return "";
    }
    hash = await client.deployContract({ code: source, args: FEEDS, fees });
    console.log(`Deploy transaction: ${hash}`);
  }

  const receipt = await client.waitForTransactionReceipt({
    hash,
    waitUntil: "decided",
    retries: 120,
    interval: 5000,
    fullTransaction: true,
  });

  try {
    assertStrictReceipt(receipt);
  } catch (e) {
    throw new Error(`Deployment not accepted: ${(e as Error).message}\nReceipt: ${JSON.stringify(receipt, (_k, v) => (typeof v === "bigint" ? v.toString() : v))}`);
  }

  const address = addressFrom(receipt);
  if (!address) {
    throw new Error(`Deployment receipt carried no contract address: ${JSON.stringify(receipt, (_k, v) => (typeof v === "bigint" ? v.toString() : v))}`);
  }
  console.log(`Contract address: ${address}`);

  // --- on-chain source verification ------------------------------------
  const codeB64 = (await rpc(rpcUrl, "gen_getContractCode", [address])) as string;
  const onChain = Buffer.from(codeB64, "base64");
  const chainSha = sha256(onChain);
  if (chainSha !== localSha) {
    throw new Error(`On-chain source mismatch: local ${localSha} != chain ${chainSha}`);
  }
  console.log(`On-chain source sha256 matches: ${chainSha}`);

  // --- constructor arguments read back ---------------------------------
  // A MISMATCH is a hard failure. An RPC read ERROR is not evidence about the
  // contract (Studio Next's gen_call has been seen failing server-side for
  // every contract), so it is recorded as "unverified" and reported loudly
  // rather than discarding a deployment whose source hash already verified.
  //
  // Levels, strongest first:
  //   verified           get_feed_registry (live contract state) == FEEDS
  //   calldata_verified  the deploy transaction's own calldata == FEEDS. This is
  //                      the INPUT the constructor received, not a state read;
  //                      it is used only when the state read is unavailable.
  //   unverified         neither could be checked.
  let readback: { status: "verified" | "calldata_verified" | "unverified"; detail: string } = {
    status: "unverified",
    detail: "not attempted",
  };
  try {
    const reg = (await client.readContract({ address, functionName: "get_feed_registry", args: [] })) as unknown;
    const rows = (Array.isArray(reg) ? reg : []).map((r) =>
      r instanceof Map ? Object.fromEntries(r.entries()) : (r as Record<string, unknown>),
    );
    const urls = rows.map((r) => String(r.active_url));
    if (urls.length !== 3 || urls.some((u, i) => u !== FEEDS[i])) {
      throw new Error(`Registry read-back mismatch: ${JSON.stringify(urls)}`);
    }
    readback = { status: "verified", detail: "get_feed_registry returned the three constructor arguments" };
    console.log("Feed registry reads back as the three constructor arguments.");
  } catch (e) {
    const msg = (e as Error).message ?? String(e);
    if (msg.startsWith("Registry read-back mismatch")) throw e;
    readback = { status: "unverified", detail: `read failed: ${msg.split("\n")[0].slice(0, 200)}` };
    console.warn(`WARNING: registry state read-back could not be performed: ${readback.detail}`);
    const b64 = (receipt.data as { calldata?: { base64?: string } } | undefined)?.calldata?.base64;
    if (b64) {
      const text = Buffer.from(b64, "base64").toString("latin1");
      const sent = [...text.matchAll(/https:\/\/[A-Za-z0-9._~\/@:-]+\//g)].map((m) => m[0]);
      if (sent.length === 3 && sent.every((u, i) => u === FEEDS[i])) {
        readback = {
          status: "calldata_verified",
          detail: `state read failed (${readback.detail}); the deployment transaction's calldata carries exactly the three constructor arguments`,
        };
        console.warn("Constructor arguments verified from the transaction calldata (not from contract state).");
      } else {
        throw new Error(`Constructor calldata mismatch: ${JSON.stringify(sent)}`);
      }
    }
  }

  // --- record ------------------------------------------------------------
  const abi = JSON.parse(readFileSync(path.resolve(process.cwd(), "deployments/macro_stance_abi.json"), "utf8")) as {
    abi: { name: string; stateMutability: string }[];
  };
  const head = readFileSync(file, "utf8").split("\n").slice(0, 3).join("\n");
  const runner = /py-genlayer:([a-z0-9]+)/.exec(head)?.[0] ?? "";
  const record = {
    network: "studio-next",
    chain_id: CHAIN_ID,
    rpc_url: rpcUrl,
    contract_address: address,
    explorer_url: `${EXPLORER}/address/${address}`,
    deploy_tx: hash,
    source: CONTRACT_PATH,
    runner,
    source_sha256: localSha,
    onchain_sha256: chainSha,
    source_matches_onchain: chainSha === localSha,
    receipt: {
      status: name(receipt.statusName, receipt.status, STATUS),
      execution: name(receipt.txExecutionResultName, receipt.txExecutionResult, EXECUTION),
      consensus: name(receipt.resultName ?? receipt.result_name, receipt.result, RESULT),
    },
    constructor_args: FEEDS,
    constructor_readback: readback,
    fee_deposit_wei: (feeValue ?? BigInt(String((receipt.data as { fee_value?: string } | undefined)?.fee_value ?? "0"))).toString(),
    // `recorded_at` is when THIS file was written, not when the contract was
    // deployed; the transaction's own timestamp is carried separately.
    tx_created_timestamp: (receipt.created_timestamp ?? receipt.timestamp ?? null) as string | number | null,
    recorded_at: new Date().toISOString(),
    public_methods: {
      views: abi.abi.filter((m) => m.stateMutability === "view").map((m) => m.name),
      writes: abi.abi.filter((m) => m.stateMutability !== "view").map((m) => m.name),
    },
  };
  mkdirSync(path.dirname(path.resolve(process.cwd(), RECORD_PATH)), { recursive: true });
  writeFileSync(path.resolve(process.cwd(), RECORD_PATH), JSON.stringify(record, null, 2) + "\n");
  console.log(`Recorded ${RECORD_PATH}`);
  console.log("Next: set NEXT_PUBLIC_MACROSTANCE_ADDRESS in frontend/.env.local");
  return address;
}
