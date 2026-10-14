"use client";

import { useState } from "react";

import type { EpochRecord, Overview, WriteOutcome } from "@/lib/contract";
import type { MacroStanceClient } from "@/lib/contract";
import { formatGen, parseGenAmount, parseWei } from "@/lib/inputs";
import type { WriteState } from "@/lib/useMacroStance";
import { parseEpochId } from "@/lib/stance";
import { StanceBadge } from "./StanceMonitor";

interface Props {
  overview: Overview | null;
  epochs: EpochRecord[];
  account: string | null;
  claimable: bigint | null;
  /** chain-time "now" in seconds, advanced locally between reads */
  chainNow: number | null;
  write: WriteState;
  run(label: string, fn: (c: MacroStanceClient) => Promise<WriteOutcome>): Promise<boolean>;
}

const STATUS_STYLE: Record<string, string> = {
  STAGED: "bg-neutral-soft text-neutral-ink border-neutral",
  EXECUTED: "bg-dovish-soft text-dovish-ink border-dovish",
  CANCELLED: "bg-hawkish-soft text-hawkish-ink border-hawkish",
  REJECTED: "bg-hawkish-soft text-hawkish-ink border-hawkish",
  QUORUM_FAILED: "bg-amber-50 text-amber-900 border-amber-600",
};

function countdown(seconds: number): string {
  if (seconds <= 0) return "ready";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export default function EpochSyncDesk({ overview, epochs, account, claimable, chainNow, write, run }: Props) {
  const [epochId, setEpochId] = useState("2026-Q4-FOMC");
  const [bond, setBond] = useState("0.1");
  const [challengeBond, setChallengeBond] = useState("0.1");

  const seq = parseEpochId(epochId);
  const minBond = parseWei(overview?.reporter_bond) ?? 10n ** 17n;
  const bondParsed = parseGenAmount(bond);
  const challengeParsed = parseGenAmount(challengeBond);
  const busy = write.phase === "pending";

  const proposalError = (() => {
    if (seq < 0) return "Epoch id must look like 2026-Q4-FOMC.";
    if (overview?.pending_epoch) return `Epoch ${overview.pending_epoch} is already staged; wait for it to execute or be challenged.`;
    if (overview && seq < overview.last_executed_seq) return "That quarter is older than the last executed epoch (stale).";
    if (!bondParsed.ok) return bondParsed.error;
    if (bondParsed.wei < minBond) return `Bond must be at least ${formatGen(minBond)} GEN.`;
    return null;
  })();

  const staged = epochs.find((e) => e.status === "STAGED") ?? null;
  const remaining = staged && chainNow !== null ? staged.activates_at - chainNow : null;

  return (
    <section className="panel" aria-labelledby="desk-title">
      <header className="panel-head">
        <h2 id="desk-title" className="panel-title">
          Epoch Sync Desk
        </h2>
        <span className="label !text-slate-400">propose · 24 h timelock · challenge · execute</span>
      </header>

      <div className="grid gap-px bg-slate-200 lg:grid-cols-2">
        <form
          className="bg-white p-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!proposalError && bondParsed.ok) {
              const w = bondParsed.wei;
              void run(`Propose ${epochId}`, (c) => c.proposeEpochSync(epochId, w));
            }
          }}
        >
          <h3 className="font-serif text-lg font-semibold text-slate-900">Propose epoch sync</h3>
          <label className="mt-3 block">
            <span className="label">Epoch id</span>
            <input className="field mt-1" value={epochId} onChange={(e) => setEpochId(e.target.value.trim())} spellCheck={false} />
          </label>
          <label className="mt-3 block">
            <span className="label">Reporter bond (GEN)</span>
            <input className="field mt-1" inputMode="decimal" value={bond} onChange={(e) => setBond(e.target.value)} />
          </label>
          {proposalError && <p className="mt-2 font-mono text-[11px] text-hawkish">{proposalError}</p>}
          <button className="btn mt-3" disabled={!account || busy || proposalError !== null}>
            {account ? "Post bond & sync" : "Connect wallet"}
          </button>
          <p className="mt-2 font-mono text-[11px] text-slate-500">
            Verified: bond held until execution. Outage: bond returned. Rejected: bond forfeited to treasury.
          </p>
        </form>

        <div className="bg-white p-4">
          <h3 className="font-serif text-lg font-semibold text-slate-900">Staged transition</h3>
          {staged ? (
            <div className="mt-3 space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-mono text-sm font-semibold">{staged.epoch_id}</span>
                <StanceBadge stance={staged.stance} />
              </div>
              <div>
                <div className="label">Activation in</div>
                <div className="num text-3xl font-semibold text-slate-950" aria-live="off">
                  {remaining === null ? "—" : countdown(remaining)}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  className="btn"
                  disabled={!account || busy || remaining === null || remaining > 0}
                  onClick={() => void run(`Execute ${staged.epoch_id}`, (c) => c.executeStanceTransition(staged.epoch_id))}
                >
                  Execute transition
                </button>
              </div>
              <div className="border-t border-slate-200 pt-3">
                <label className="block">
                  <span className="label">Challenger bond (GEN) — only valid inside the window</span>
                  <input
                    className="field mt-1"
                    inputMode="decimal"
                    value={challengeBond}
                    onChange={(e) => setChallengeBond(e.target.value)}
                  />
                </label>
                {!challengeParsed.ok && <p className="mt-1 font-mono text-[11px] text-hawkish">{challengeParsed.error}</p>}
                <button
                  className="btn-ghost mt-2"
                  disabled={!account || busy || !challengeParsed.ok || remaining === null || remaining <= 0}
                  onClick={() => {
                    if (challengeParsed.ok) {
                      const w = challengeParsed.wei;
                      void run(`Challenge ${staged.epoch_id}`, (c) => c.challengeStagedEpoch(staged.epoch_id, w));
                    }
                  }}
                >
                  Challenge staged epoch
                </button>
                <p className="mt-1 font-mono text-[11px] text-slate-500">
                  Re-queries all three feeds. A retraction or conflict cancels the stage and pays the reporter&apos;s bond to
                  you; a failed challenge forfeits yours to the reporter.
                </p>
              </div>
            </div>
          ) : (
            <p className="mt-3 text-sm text-slate-600">
              No transition is pending. Active parameters stay as they are until a verified epoch has waited out the
              24-hour timelock.
            </p>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-300 bg-slate-50 px-4 py-3">
        <div>
          <div className="label">Your claimable balance</div>
          <div className="num text-lg font-semibold text-slate-950">
            {account ? (claimable === null ? "…" : `${formatGen(claimable)} GEN`) : "connect a wallet"}
          </div>
        </div>
        <button
          className="btn"
          disabled={!account || busy || !claimable || claimable === 0n}
          onClick={() => void run("Claim payout", (c) => c.claimPayout())}
        >
          Claim payout
        </button>
      </div>

      {write.phase !== "idle" && (
        <div
          role="status"
          className={`border-t px-4 py-2 font-mono text-xs ${
            write.phase === "failed"
              ? "border-hawkish bg-hawkish-soft text-hawkish-ink"
              : write.phase === "done"
                ? "border-dovish bg-dovish-soft text-dovish-ink"
                : "border-slate-300 bg-slate-100 text-slate-700"
          }`}
        >
          {write.phase === "pending" && `${write.label}: awaiting wallet and consensus…`}
          {write.phase === "done" && `${write.label}: accepted by a validator majority · ${write.hash.slice(0, 18)}…`}
          {write.phase === "failed" && `${write.label}: ${write.message}`}
        </div>
      )}

      <div className="overflow-x-auto border-t border-slate-300">
        <table className="w-full min-w-[640px] text-sm">
          <caption className="sr-only">Epoch ledger</caption>
          <thead>
            <tr className="bg-slate-100 text-left">
              <th className="label px-4 py-2">Epoch</th>
              <th className="label px-4 py-2">Status</th>
              <th className="label px-4 py-2">Stance</th>
              <th className="label px-4 py-2 text-right">Sources</th>
              <th className="label px-4 py-2">Release hash</th>
              <th className="label px-4 py-2 text-right">Tries</th>
            </tr>
          </thead>
          <tbody>
            {epochs.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-4 font-mono text-xs text-slate-500">
                  No epoch has been proposed yet.
                </td>
              </tr>
            )}
            {epochs.map((e) => (
              <tr key={e.epoch_id} className="border-t border-slate-200">
                <td className="px-4 py-2 font-mono text-xs font-semibold">{e.epoch_id}</td>
                <td className="px-4 py-2">
                  <span className={`border px-2 py-0.5 font-mono text-[11px] font-semibold ${STATUS_STYLE[e.status] ?? ""}`}>
                    {e.status.replace("_", " ")}
                  </span>
                </td>
                <td className="px-4 py-2">{e.verdict === 1 ? <StanceBadge stance={e.stance} /> : <span className="font-mono text-xs text-slate-400">—</span>}</td>
                <td className="num px-4 py-2 text-right">{e.source_count}</td>
                <td className="px-4 py-2 font-mono text-[11px] text-slate-500" title={e.release_hash}>
                  {e.release_hash ? `${e.release_hash.slice(0, 12)}…` : "—"}
                </td>
                <td className="num px-4 py-2 text-right">{e.attempts}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
