"use client";

import type { LoaderState } from "@/lib/serialLoader";

interface Props {
  configured: boolean;
  chainOk: boolean | null;
  publicState: LoaderState<unknown>;
  accountState: LoaderState<unknown>;
  account: string | null;
  now: number;
}

function ago(ms: number | null, now: number): string {
  if (ms === null) return "never";
  const s = Math.max(0, Math.round((now - ms) / 1000));
  return s < 2 ? "just now" : `${s}s ago`;
}

/**
 * Honest sync banner. It reports what the terminal could NOT read, by item,
 * and it keeps reporting it: a later successful read of another item never
 * clears a failure (SerialLoader tracks failedIds per item).
 */
export default function SyncAlert({ configured, chainOk, publicState, accountState, account, now }: Props) {
  const notes: { tone: "warn" | "bad" | "info"; text: string }[] = [];

  if (!configured) {
    notes.push({
      tone: "bad",
      text: "No contract address is configured (NEXT_PUBLIC_MACROSTANCE_ADDRESS). Nothing on this page is live.",
    });
  }
  if (account && chainOk === false) {
    notes.push({
      tone: "bad",
      text: "Your wallet is on a different network. Switch to GenLayer Studio Next (chain 61997) before transacting.",
    });
  }
  const pubFailed = [...publicState.failedIds];
  const accFailed = [...accountState.failedIds];
  if (configured && pubFailed.length > 0) {
    const detail = pubFailed.map((id) => `${id}${publicState.errors.get(id) ? ` (${publicState.errors.get(id)})` : ""}`);
    notes.push({
      tone: "warn",
      text: `Partial read failure — ${pubFailed.length} item${pubFailed.length > 1 ? "s" : ""} not refreshed: ${detail.join("; ")}. Values shown for these may be stale.`,
    });
  }
  if (account && accFailed.length > 0) {
    notes.push({
      tone: "warn",
      text: `Account read failure — ${accFailed.join(", ")} could not be read for your wallet. Displayed account figures may be stale.`,
    });
  }
  if (configured && pubFailed.length === 0 && publicState.lastCompletedAt === null && !publicState.loading) {
    notes.push({ tone: "info", text: "Waiting for the first successful read from the RPC…" });
  }

  const tones = {
    bad: "border-hawkish bg-hawkish-soft text-hawkish-ink",
    warn: "border-amber-600 bg-amber-50 text-amber-900",
    info: "border-slate-400 bg-slate-100 text-slate-700",
  } as const;

  return (
    <div aria-live="polite" className="space-y-px">
      {notes.map((n, i) => (
        <div key={i} className={`border-l-4 px-4 py-2 font-mono text-xs ${tones[n.tone]}`}>
          {n.text}
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 bg-slate-100 px-4 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
        <span>
          RPC sync:{" "}
          <span className={publicState.loading ? "text-amber-700" : "text-slate-700"}>
            {publicState.loading ? "reading…" : ago(publicState.lastCompletedAt, now)}
          </span>
        </span>
        {account && (
          <span>
            Account sync: <span className="text-slate-700">{ago(accountState.lastCompletedAt, now)}</span>
          </span>
        )}
        <span>
          Items: {publicState.values.size} ok / {publicState.failedIds.size} failed
        </span>
      </div>
    </div>
  );
}
