"use client";

import { formatBps } from "@/lib/inputs";
import { RISK_TABLE, type Stance } from "@/lib/stance";
import type { Overview } from "@/lib/contract";

export const STANCE_STYLE: Record<number, { badge: string; ring: string; text: string; soft: string }> = {
  0: { badge: "bg-dovish text-white", ring: "border-dovish", text: "text-dovish", soft: "bg-dovish-soft" },
  1: { badge: "bg-neutral text-white", ring: "border-neutral", text: "text-neutral", soft: "bg-neutral-soft" },
  2: { badge: "bg-hawkish text-white", ring: "border-hawkish", text: "text-hawkish", soft: "bg-hawkish-soft" },
};

export function StanceBadge({ stance, className = "" }: { stance: number; className?: string }) {
  const row = RISK_TABLE[stance];
  const s = STANCE_STYLE[stance] ?? STANCE_STYLE[1];
  return (
    <span className={`inline-block px-2 py-0.5 font-mono text-[11px] font-bold tracking-[0.14em] ${s.badge} ${className}`}>
      {row ? row.name : "UNKNOWN"}
    </span>
  );
}

export default function StanceMonitor({ overview }: { overview: Overview | null }) {
  const active = overview ? (overview.active_stance as Stance) : null;
  const style = active !== null ? STANCE_STYLE[active] : null;

  return (
    <section className="panel" aria-labelledby="stance-title">
      <header className="panel-head">
        <h2 id="stance-title" className="panel-title">
          Current Macro Stance
        </h2>
        <span className="label !text-slate-400">on-chain · bound to discrete tier</span>
      </header>

      <div className={`border-l-8 p-5 ${style ? style.ring : "border-slate-300"}`}>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="label">Active stance</div>
            <div className={`font-serif text-5xl font-bold tracking-tight ${style ? style.text : "text-slate-400"}`}>
              {overview ? overview.active_stance_name : "—"}
            </div>
            <div className="mt-1 font-mono text-xs text-slate-500">
              {overview?.active_epoch_id
                ? `set by epoch ${overview.active_epoch_id}`
                : overview
                  ? "genesis default — no epoch executed yet"
                  : "waiting for chain"}
            </div>
          </div>
          <dl className="grid grid-cols-3 gap-6 text-right">
            {[
              ["Max LTV", overview?.max_ltv_bps],
              ["Borrow rate", overview?.borrow_rate_bps],
              ["Reserve factor", overview?.reserve_factor_bps],
            ].map(([k, v]) => (
              <div key={k as string}>
                <dt className="label">{k}</dt>
                <dd className="num text-2xl font-semibold text-slate-950">
                  {typeof v === "number" ? formatBps(v) : "—"}
                </dd>
                <dd className="num text-[11px] text-slate-500">{typeof v === "number" ? `${v} bps` : ""}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>

      <div className="overflow-x-auto border-t border-slate-300">
        <table className="w-full min-w-[520px] text-sm">
          <caption className="sr-only">Risk parameter table by stance</caption>
          <thead>
            <tr className="bg-slate-100 text-left">
              <th className="label px-4 py-2">Tier</th>
              <th className="label px-4 py-2">Trigger</th>
              <th className="label px-4 py-2 text-right">Max LTV</th>
              <th className="label px-4 py-2 text-right">Borrow</th>
              <th className="label px-4 py-2 text-right">Reserve</th>
            </tr>
          </thead>
          <tbody>
            {RISK_TABLE.map((r) => {
              const isActive = active === r.stance;
              return (
                <tr key={r.name} className={`border-t border-slate-200 ${isActive ? STANCE_STYLE[r.stance].soft : ""}`}>
                  <td className="px-4 py-2">
                    <StanceBadge stance={r.stance} />
                    {isActive && <span className="ml-2 font-mono text-[10px] uppercase text-slate-600">active</span>}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-slate-600">
                    {r.stance === 0 && "cut ≥ 25 bps  or  CPI < 2.00%"}
                    {r.stance === 1 && "otherwise (hold, CPI 2.00–3.00%)"}
                    {r.stance === 2 && "hike ≥ 25 bps  or  CPI > 3.00%"}
                  </td>
                  <td className="num px-4 py-2 text-right">{r.maxLtvBps}</td>
                  <td className="num px-4 py-2 text-right">{r.borrowRateBps}</td>
                  <td className="num px-4 py-2 text-right">{r.reserveFactorBps}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="border-t border-slate-200 px-4 py-2 font-mono text-[11px] text-slate-500">
        Priority is fixed: HAWKISH is tested first, then DOVISH, else NEUTRAL. Parameters bind to the tier, never to the
        raw rate or CPI figure.
      </p>
    </section>
  );
}
