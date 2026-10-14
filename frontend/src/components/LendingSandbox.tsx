"use client";

import { useState } from "react";

import type { AccountHealth, MacroStanceClient, Overview, WriteOutcome } from "@/lib/contract";
import { formatBps, formatGen, parseGenAmount, parseWei } from "@/lib/inputs";
import { healthStatus, isOverLtv, ltvBps, previewWithdraw } from "@/lib/lending";
import { RISK_TABLE } from "@/lib/stance";
import { StanceBadge } from "./StanceMonitor";

interface Props {
  account: string | null;
  health: AccountHealth | null;
  overview: Overview | null;
  busy: boolean;
  run(label: string, fn: (c: MacroStanceClient) => Promise<WriteOutcome>): Promise<boolean>;
}

type Action = "deposit" | "borrow" | "repay" | "withdraw";

const ACTIONS: { id: Action; label: string; hint: string }[] = [
  { id: "deposit", label: "Deposit collateral", hint: "Native GEN held in custody by the contract." },
  { id: "borrow", label: "Borrow", hint: "Ledger debt units, bounded by the active max LTV." },
  { id: "repay", label: "Repay", hint: "Reduces debt; paying more than owed clamps to the debt." },
  { id: "withdraw", label: "Withdraw collateral", hint: "Allowed only while the position stays within max LTV." },
];

export default function LendingSandbox({ account, health, overview, busy, run }: Props) {
  const [action, setAction] = useState<Action>("deposit");
  const [amount, setAmount] = useState("");

  const collateral = parseWei(health?.collateral) ?? 0n;
  const debt = parseWei(health?.debt) ?? 0n;
  const available = parseWei(health?.available_to_borrow) ?? 0n;
  const maxLtv = health?.max_ltv_bps ?? overview?.max_ltv_bps ?? 7500;

  const parsed = parseGenAmount(amount);
  const check = (() => {
    if (!account) return "Connect a wallet to transact.";
    if (!parsed.ok) return amount === "" ? "Enter an amount." : parsed.error;
    const w = parsed.wei;
    if (action === "borrow") {
      if (collateral === 0n) return "Deposit collateral first.";
      if (w > available) return `Exceeds available capacity (${formatGen(available)} GEN).`;
    }
    if (action === "repay" && debt === 0n) return "There is no debt to repay.";
    if (action === "withdraw") {
      const p = previewWithdraw(collateral, debt, w, maxLtv);
      if (!p.ok) return p.reason === "exceeds-collateral" ? "More than your collateral." : "Would breach max LTV.";
    }
    return null;
  })();

  const submit = () => {
    if (check !== null || !parsed.ok) return;
    const w = parsed.wei;
    const label = ACTIONS.find((a) => a.id === action)!.label;
    const call = {
      deposit: (c: MacroStanceClient) => c.depositCollateral(w),
      borrow: (c: MacroStanceClient) => c.borrowAgainstCollateral(w),
      repay: (c: MacroStanceClient) => c.repay(w),
      withdraw: (c: MacroStanceClient) => c.withdrawCollateral(w),
    }[action];
    void run(`${label} ${amount} GEN`, call).then((ok) => ok && setAmount(""));
  };

  const ltv = health ? health.ltv_bps : ltvBps(collateral, debt);
  const fill = Math.min(100, (ltv / 10000) * 100);

  return (
    <section className="panel" aria-labelledby="lend-title">
      <header className="panel-head">
        <h2 id="lend-title" className="panel-title">
          Lending Sandbox
        </h2>
        <span className="label !text-slate-400">risk simulator · integer BPS</span>
      </header>

      {!account ? (
        <p className="p-4 text-sm text-slate-600">Connect a wallet to open a position. State here is keyed to your address.</p>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-px bg-slate-200 sm:grid-cols-4">
            {[
              ["Collateral", `${formatGen(collateral)} GEN`],
              ["Debt", `${formatGen(debt)}`],
              ["Available", `${formatGen(available)}`],
              ["Health", health ? (health.status === "NO_DEBT" ? "no debt" : `${(health.health_factor_bps / 10000).toFixed(2)}×`) : "—"],
            ].map(([k, v]) => (
              <div key={k} className="bg-white p-3">
                <dt className="label">{k}</dt>
                <dd className="num mt-1 text-lg font-semibold text-slate-950">{v}</dd>
              </div>
            ))}
          </dl>

          <div className="border-t border-slate-200 p-4">
            <div className="flex items-center justify-between font-mono text-[11px] text-slate-600">
              <span>LTV {formatBps(ltv)}</span>
              <span>max {formatBps(maxLtv)}</span>
            </div>
            <div className="relative mt-1 h-3 border border-slate-400 bg-slate-100" role="img" aria-label={`LTV ${formatBps(ltv)} of max ${formatBps(maxLtv)}`}>
              <div
                className={`h-full ${health?.status === "OVER_LTV" ? "bg-hawkish" : "bg-neutral"}`}
                style={{ width: `${fill}%` }}
              />
              <div className="absolute inset-y-0 w-0.5 bg-slate-950" style={{ left: `${(maxLtv / 10000) * 100}%` }} />
            </div>
            {health?.status === "OVER_LTV" && (
              <p className="mt-2 border-l-4 border-hawkish bg-hawkish-soft px-3 py-2 font-mono text-xs text-hawkish-ink">
                Over the active max LTV. Nothing is seized; new borrows and withdrawals are blocked until you repay.
              </p>
            )}
          </div>

          <div className="border-t border-slate-200 p-4">
            <div role="tablist" aria-label="Vault action" className="flex flex-wrap gap-1">
              {ACTIONS.map((a) => (
                <button
                  key={a.id}
                  role="tab"
                  aria-selected={action === a.id}
                  onClick={() => setAction(a.id)}
                  className={`border px-3 py-1.5 font-mono text-[11px] font-semibold uppercase tracking-wider ${
                    action === a.id ? "border-slate-950 bg-slate-950 text-white" : "border-slate-400 bg-white text-slate-700 hover:bg-slate-100"
                  }`}
                >
                  {a.label}
                </button>
              ))}
            </div>
            <p className="mt-2 font-mono text-[11px] text-slate-500">{ACTIONS.find((a) => a.id === action)!.hint}</p>
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <label className="min-w-[180px] flex-1">
                <span className="label">Amount (GEN)</span>
                <input
                  className="field mt-1"
                  inputMode="decimal"
                  autoComplete="off"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  placeholder="0.0"
                  aria-invalid={amount !== "" && !parsed.ok}
                />
              </label>
              <button className="btn" disabled={busy || check !== null} onClick={submit}>
                Submit
              </button>
            </div>
            {check && amount !== "" && <p className="mt-2 font-mono text-[11px] text-hawkish">{check}</p>}
          </div>

          <div className="overflow-x-auto border-t border-slate-300">
            <table className="w-full min-w-[480px] text-sm">
              <caption className="px-4 pt-3 text-left font-mono text-[11px] uppercase tracking-wider text-slate-500">
                This position under each tier
              </caption>
              <thead>
                <tr className="text-left">
                  <th className="label px-4 py-2">Tier</th>
                  <th className="label px-4 py-2 text-right">Max LTV</th>
                  <th className="label px-4 py-2 text-right">Borrow APR</th>
                  <th className="label px-4 py-2 text-right">Capacity</th>
                  <th className="label px-4 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {RISK_TABLE.map((r) => {
                  const st = healthStatus(collateral, debt, r.maxLtvBps);
                  return (
                    <tr key={r.name} className="border-t border-slate-200">
                      <td className="px-4 py-2">
                        <StanceBadge stance={r.stance} />
                      </td>
                      <td className="num px-4 py-2 text-right">{formatBps(r.maxLtvBps)}</td>
                      <td className="num px-4 py-2 text-right">{formatBps(r.borrowRateBps)}</td>
                      <td className="num px-4 py-2 text-right">{formatGen((collateral * BigInt(r.maxLtvBps)) / 10000n)}</td>
                      <td
                        className={`px-4 py-2 font-mono text-xs font-semibold ${
                          isOverLtv(collateral, debt, r.maxLtvBps) ? "text-hawkish" : "text-dovish"
                        }`}
                      >
                        {st.replace("_", " ")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
