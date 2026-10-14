"use client";

import { useEffect, useState } from "react";

import EpochSyncDesk from "@/components/EpochSyncDesk";
import FeedQuorumRadar from "@/components/FeedQuorumRadar";
import Footer from "@/components/Footer";
import LendingSandbox from "@/components/LendingSandbox";
import ProtocolExplainer from "@/components/ProtocolExplainer";
import StanceMonitor from "@/components/StanceMonitor";
import SyncAlert from "@/components/SyncAlert";
import { useMacroStance } from "@/lib/useMacroStance";

export default function Page() {
  const t = useMacroStance();
  const [tick, setTick] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // Chain time advanced locally from the last read, so countdowns tick between polls.
  const chainNow =
    t.overview && t.readAtMs !== null ? t.overview.now + Math.max(0, Math.floor((tick - t.readAtMs) / 1000)) : null;
  const short = t.account ? `${t.account.slice(0, 6)}…${t.account.slice(-4)}` : null;

  return (
    <div>
      <header className="bg-slate-950 text-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6">
          <div>
            <h1 className="font-serif text-2xl font-bold tracking-tight">MacroStance</h1>
            <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-slate-400">
              Autonomous macroeconomic policy oracle &amp; DeFi risk controller
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden border border-slate-700 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-400 sm:inline">
              Studio Next · 61997
            </span>
            {short ? (
              <button className="btn-ghost !border-slate-600 !bg-transparent !text-white hover:!bg-slate-800" onClick={t.disconnect} title="Disconnect">
                {short}
              </button>
            ) : (
              <button className="btn !border-white !bg-white !text-slate-950 hover:!bg-slate-200" onClick={() => void t.connect()} disabled={t.connecting}>
                {t.connecting ? "Connecting…" : "Connect wallet"}
              </button>
            )}
          </div>
        </div>
        {t.walletError && <div className="bg-hawkish px-4 py-1.5 font-mono text-xs text-white sm:px-6">{t.walletError}</div>}
      </header>

      <SyncAlert
        configured={t.configured}
        chainOk={t.chainOk}
        publicState={t.publicState}
        accountState={t.accountState}
        account={t.account}
        now={tick}
      />

      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6">
        <div className="grid gap-6 xl:grid-cols-2">
          <StanceMonitor overview={t.overview} />
          <FeedQuorumRadar registry={t.registry} />
        </div>
        <div className="grid gap-6 xl:grid-cols-2">
          {/* Keyed by wallet: switching accounts remounts the panels and drops local form state. */}
          <EpochSyncDesk
            key={`desk-${t.account ?? "none"}`}
            overview={t.overview}
            epochs={t.epochs}
            account={t.account}
            claimable={t.claimable}
            chainNow={chainNow}
            write={t.write}
            run={t.run}
          />
          <LendingSandbox
            key={`lend-${t.account ?? "none"}`}
            account={t.account}
            health={t.health}
            overview={t.overview}
            busy={t.write.phase === "pending"}
            run={t.run}
          />
        </div>
        <ProtocolExplainer />
      </main>
      <Footer />
    </div>
  );
}
