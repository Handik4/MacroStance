"use client";

import { useState } from "react";

import { CHAIN_ID, CONTRACT_ADDRESS, EXPLORER_URL, RPC_URL, explorerAddressUrl } from "@/lib/contract";

export default function Footer() {
  const [copied, setCopied] = useState(false);
  const addr = CONTRACT_ADDRESS;
  const live = /^0x[0-9a-fA-F]{40}$/.test(addr);

  return (
    <footer className="mt-10 border-t border-slate-300 bg-slate-950 text-slate-300">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-6 sm:px-6">
        <div>
          <div className="font-serif text-lg font-semibold text-white">MacroStance</div>
          <p className="mt-1 max-w-md font-mono text-[11px] leading-relaxed text-slate-400">
            Testnet software. Debt in the lending sandbox is a ledger simulation; no tokens are lent. Collateral and bonds
            are native test GEN.
          </p>
        </div>
        <div className="space-y-2 text-right font-mono text-xs">
          <div className="flex items-center justify-end gap-2">
            <span className="text-slate-500">contract</span>
            {live ? (
              <>
                <a className="border border-slate-600 px-2 py-1 text-slate-100 hover:bg-slate-800" href={explorerAddressUrl(addr)} target="_blank" rel="noreferrer">
                  {addr.slice(0, 8)}…{addr.slice(-6)}
                </a>
                <button
                  className="border border-slate-600 px-2 py-1 hover:bg-slate-800"
                  onClick={() => {
                    void navigator.clipboard?.writeText(addr).then(() => {
                      setCopied(true);
                      setTimeout(() => setCopied(false), 1500);
                    });
                  }}
                >
                  {copied ? "copied" : "copy"}
                </button>
              </>
            ) : (
              <span className="border border-hawkish px-2 py-1 text-red-300">not configured</span>
            )}
          </div>
          <div className="text-slate-500">
            GenLayer Studio Next · chain {CHAIN_ID} ·{" "}
            <a className="underline hover:text-white" href={EXPLORER_URL} target="_blank" rel="noreferrer">
              explorer
            </a>{" "}
            · <span title={RPC_URL}>rpc</span>
          </div>
        </div>
      </div>
    </footer>
  );
}
