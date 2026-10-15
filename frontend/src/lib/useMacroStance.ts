"use client";

/**
 * Wallet + chain state for the terminal.
 *
 * State partitioning (this is what prevents cross-account leakage):
 *   - `publicLoader`  : protocol overview, feed registry, epochs. Stable.
 *   - `accountLoader` : this wallet's health and claimable balance. A NEW
 *     SerialLoader per address; the previous one is reset on switch, so a
 *     MetaMask account change can never show the old account's numbers.
 */

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import {
  CHAIN_ID,
  CONTRACT_ADDRESS,
  MacroStanceClient,
  RPC_URL,
  type AccountHealth,
  type EpochRecord,
  type FeedSlot,
  type Overview,
  type WriteOutcome,
} from "./contract";
import { ChainClock } from "./chainClock";
import { isAddress, normalizeAddress } from "./inputs";
import { SerialLoader, type LoaderState } from "./serialLoader";
import { TxStatusStore, type TxStatus } from "./txStatus";

const POLL_MS = 15_000;
const CHAIN_HEX = "0x" + CHAIN_ID.toString(16);

type PublicValue = Overview | FeedSlot[] | EpochRecord;
type AccountValue = AccountHealth | bigint;

export interface Terminal {
  configured: boolean;
  account: string | null;
  chainOk: boolean | null;
  connecting: boolean;
  walletError: string | null;
  connect(): Promise<void>;
  disconnect(): void;
  overview: Overview | null;
  registry: FeedSlot[] | null;
  epochs: EpochRecord[];
  health: AccountHealth | null;
  claimable: bigint | null;
  publicState: LoaderState<PublicValue>;
  accountState: LoaderState<AccountValue>;
  readAtMs: number | null;
  /** Verified-chain-time estimate (seconds); monotone; null before the first read. */
  chainNow: number | null;
  /** The status of the CONNECTED account's latest action; never another account's. */
  write: TxStatus | null;
  /** Connected, but the position has neither been read nor failed (still syncing). */
  accountUnknown: boolean;
  run(label: string, fn: (c: MacroStanceClient) => Promise<WriteOutcome>): Promise<boolean>;
  refresh(): void;
  client: MacroStanceClient;
}

function useLoaderState<T>(loader: SerialLoader<T>): LoaderState<T> {
  return useSyncExternalStore(loader.subscribe, loader.getState, loader.getState);
}

export function useMacroStance(): Terminal {
  const client = useMemo(() => new MacroStanceClient(CONTRACT_ADDRESS, RPC_URL), []);
  const [account, setAccount] = useState<string | null>(null);
  const [chainOk, setChainOk] = useState<boolean | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [walletError, setWalletError] = useState<string | null>(null);
  const [readAtMs, setReadAtMs] = useState<number | null>(null);
  const txStore = useMemo(() => new TxStatusStore(), []);
  const clock = useMemo(() => new ChainClock(), []);
  useSyncExternalStore(txStore.subscribe, txStore.getVersion, txStore.getVersion);
  const epochCount = useRef(0);

  // ---- public loader (stable for the page lifetime)
  const publicLoader = useMemo(
    () =>
      new SerialLoader<PublicValue>(async (id, signal) => {
        if (id === "overview") {
          const o = await client.getOverview(signal);
          epochCount.current = Number(o.epoch_count);
          return o;
        }
        if (id === "registry") return client.getFeedRegistry(signal);
        if (id.startsWith("epoch:")) return client.getEpochByIndex(Number(id.slice(6)), signal);
        throw new Error(`unknown item ${id}`);
      }),
    [client],
  );

  // ---- account loader: a fresh instance per address
  const accountLoader = useMemo(
    () =>
      new SerialLoader<AccountValue>(async (id, signal) => {
        if (!account) throw new Error("no account");
        if (id === "health") return client.getAccountHealth(account, signal);
        if (id === "claimable") return client.claimableOf(account, signal);
        throw new Error(`unknown item ${id}`);
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [client, account],
  );

  useEffect(() => () => accountLoader.reset(), [accountLoader]);

  const publicState = useLoaderState(publicLoader);
  const accountState = useLoaderState(accountLoader);

  // Anchor the clock ONLY on a verified overview read. `readAt` is stamped on
  // success, so a failed poll (which leaves the stale overview in place) can
  // never re-anchor the countdown backward.
  const overviewReadAt = publicState.readAt.get("overview") ?? null;
  const observedAt = useRef<number | null>(null);
  const ov = publicState.values.get("overview") as Overview | undefined;
  useEffect(() => {
    if (overviewReadAt !== null && overviewReadAt !== observedAt.current && ov) {
      observedAt.current = overviewReadAt;
      clock.observe(ov.now);
    }
  }, [overviewReadAt, ov, clock]);

  const refresh = useCallback(() => {
    if (!client.configured) return;
    const ids = ["overview", "registry"];
    for (let i = 0; i < epochCount.current; i++) ids.push(`epoch:${i}`);
    void publicLoader.refresh(ids).then(() => {
      // epoch count may have grown in this pass: pick up new rows immediately
      const want = epochCount.current;
      const have = [...publicLoader.getState().values.keys()].filter((k) => k.startsWith("epoch:")).length;
      if (want !== have) {
        const next = ["overview", "registry"];
        for (let i = 0; i < want; i++) next.push(`epoch:${i}`);
        void publicLoader.refresh(next);
      }
      setReadAtMs(Date.now());
    });
    if (account) void accountLoader.refresh(["health", "claimable"]);
  }, [client, publicLoader, accountLoader, account]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  // ---- wallet lifecycle
  const bind = useCallback(
    (addr: string | null) => {
      if (addr && isAddress(addr) && window.ethereum) {
        // Purge BEFORE the new account becomes visible: no render may pair the
        // new address with the previous account's banner.
        txStore.purgeExcept(normalizeAddress(addr));
        client.connect(addr, window.ethereum);
        setAccount(normalizeAddress(addr));
      } else {
        txStore.purgeExcept(null);
        client.disconnect();
        setAccount(null);
      }
    },
    [client, txStore],
  );

  useEffect(() => {
    const eth = typeof window !== "undefined" ? window.ethereum : undefined;
    if (!eth?.on) return;
    const onAccounts = (...a: unknown[]) => {
      const list = a[0] as string[] | undefined;
      bind(list && list.length > 0 ? list[0] : null);
    };
    const onChain = (...a: unknown[]) => setChainOk(String(a[0]).toLowerCase() === CHAIN_HEX);
    eth.on("accountsChanged", onAccounts);
    eth.on("chainChanged", onChain);
    return () => {
      eth.removeListener?.("accountsChanged", onAccounts);
      eth.removeListener?.("chainChanged", onChain);
    };
  }, [bind]);

  const connect = useCallback(async () => {
    const eth = window.ethereum;
    if (!eth) {
      setWalletError("No injected wallet found. Install MetaMask to transact.");
      return;
    }
    setConnecting(true);
    setWalletError(null);
    try {
      const accounts = (await eth.request({ method: "eth_requestAccounts" })) as string[];
      let id = String(await eth.request({ method: "eth_chainId" })).toLowerCase();
      if (id !== CHAIN_HEX) {
        try {
          await eth.request({ method: "wallet_switchEthereumChain", params: [{ chainId: CHAIN_HEX }] });
        } catch {
          await eth.request({
            method: "wallet_addEthereumChain",
            params: [
              {
                chainId: CHAIN_HEX,
                chainName: "GenLayer Studio Next",
                nativeCurrency: { name: "GEN", symbol: "GEN", decimals: 18 },
                rpcUrls: [RPC_URL],
              },
            ],
          });
        }
        id = String(await eth.request({ method: "eth_chainId" })).toLowerCase();
      }
      setChainOk(id === CHAIN_HEX);
      bind(accounts[0] ?? null);
    } catch (e) {
      setWalletError((e as Error)?.message ?? "Wallet connection failed.");
    } finally {
      setConnecting(false);
    }
  }, [bind]);

  const disconnect = useCallback(() => bind(null), [bind]);

  const run = useCallback(
    async (label: string, fn: (c: MacroStanceClient) => Promise<WriteOutcome>) => {
      const origin = client.account;
      if (!origin) return false;
      // The status belongs to the account that started the action. If the
      // wallet switches mid-flight the entry is purged and these completions
      // are ignored (ids no longer match), so nothing leaks to the new account.
      const id = txStore.begin(origin, label);
      try {
        const out = await fn(client);
        txStore.succeed(origin, id, out.hash);
        refresh();
        return true;
      } catch (e) {
        txStore.fail(origin, id, (e as Error)?.message ?? "Transaction failed.");
        return false;
      }
    },
    [client, refresh, txStore],
  );

  const overview = (publicState.values.get("overview") as Overview | undefined) ?? null;
  const registry = (publicState.values.get("registry") as FeedSlot[] | undefined) ?? null;
  const epochs = useMemo(() => {
    const rows: EpochRecord[] = [];
    for (const [k, v] of publicState.values) if (k.startsWith("epoch:")) rows.push(v as EpochRecord);
    return rows.sort((a, b) => b.seq - a.seq || b.staged_at - a.staged_at);
  }, [publicState]);
  const health = (accountState.values.get("health") as AccountHealth | undefined) ?? null;
  const claimableRaw = accountState.values.get("claimable");
  const claimable = typeof claimableRaw === "bigint" ? claimableRaw : null;
  const accountUnknown =
    account !== null && health === null && !accountState.loading && !accountState.failedIds.has("health");

  return {
    configured: client.configured,
    account,
    chainOk,
    connecting,
    walletError,
    connect,
    disconnect,
    overview,
    registry,
    epochs,
    health,
    claimable,
    publicState,
    accountState,
    readAtMs,
    chainNow: clock.estimate(),
    write: txStore.get(account),
    accountUnknown,
    run,
    refresh,
    client,
  };
}
