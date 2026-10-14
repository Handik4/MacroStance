"use client";

import { useEffect, useMemo, useState, useSyncExternalStore } from "react";

import type { FeedSlot } from "@/lib/contract";
import { SerialLoader } from "@/lib/serialLoader";
import {
  VERDICT_NAMES,
  buildFeedUrl,
  classifyResponse,
  isValidEpochId,
  resolveQuorum,
  type SourceState,
} from "@/lib/stance";
import { StanceBadge } from "./StanceMonitor";

interface SlotResult {
  slot: number;
  url: string;
  http: number | null;
  note: string;
  state: SourceState;
}

const KIND_STYLE: Record<string, string> = {
  CONFIRMED: "bg-dovish-soft text-dovish-ink border-dovish",
  RETRACTED: "bg-hawkish-soft text-hawkish-ink border-hawkish",
  NOT_FOUND: "bg-amber-50 text-amber-900 border-amber-600",
  OUTAGE: "bg-slate-200 text-slate-700 border-slate-500",
};

const VERDICT_STYLE = [
  "border-hawkish bg-hawkish-soft text-hawkish-ink",
  "border-dovish bg-dovish-soft text-dovish-ink",
  "border-amber-600 bg-amber-50 text-amber-900",
];

const VERDICT_TEXT = [
  "The release would be forfeited: explicit retraction, conflicting stances, or no way to reach 2-of-3.",
  "A quorum of matching sources: the stance would be staged behind the 24 h timelock.",
  "Retryable. A source that is down could still complete 2-of-3, so no bond would be forfeited.",
];

const short = (s: string) => (s ? `${s.slice(0, 10)}…${s.slice(-6)}` : "—");

async function fetchSlot(slot: FeedSlot, epochId: string, signal: AbortSignal): Promise<SlotResult> {
  const url = buildFeedUrl(slot.active_url, epochId);
  const outage = (note: string, http: number | null = null): SlotResult => ({
    slot: slot.slot,
    url,
    http,
    note,
    state: { kind: "OUTAGE", stance: 0, releaseHash: "" },
  });
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), 8000);
  const onAbort = () => timeout.abort();
  signal.addEventListener("abort", onAbort, { once: true });
  try {
    const res = await fetch(url, { cache: "no-store", signal: timeout.signal });
    const body = await res.text();
    const state = await classifyResponse(res.status, body, epochId);
    return { slot: slot.slot, url, http: res.status, note: "", state };
  } catch (e) {
    if (signal.aborted) throw e;
    // A network or CORS fault is what a validator would also see as an outage.
    return outage((e as Error)?.name === "AbortError" ? "timed out after 8 s" : "network error");
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", onAbort);
  }
}

export default function FeedQuorumRadar({ registry }: { registry: FeedSlot[] | null }) {
  const [epochId, setEpochId] = useState("2026-Q4-FOMC");
  const [armed, setArmed] = useState<string | null>(null);

  // One loader per (registry, epoch): single-flight and discarded on change.
  const key = `${armed ?? ""}|${(registry ?? []).map((r) => r.active_url).join(",")}`;
  const loader = useMemo(
    () =>
      new SerialLoader<SlotResult>(async (id, signal) => {
        const slot = registry?.find((r) => String(r.slot) === id);
        if (!slot || !armed) throw new Error("not ready");
        return fetchSlot(slot, armed, signal);
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key],
  );
  useEffect(() => () => loader.reset(), [loader]);
  const state = useSyncExternalStore(loader.subscribe, loader.getState, loader.getState);

  useEffect(() => {
    if (armed && registry) void loader.refresh(registry.map((r) => String(r.slot)));
  }, [armed, registry, loader]);

  const results = registry?.map((r) => state.values.get(String(r.slot)) ?? null) ?? [];
  const complete = results.length === 3 && results.every((r) => r !== null);
  const quorum = complete ? resolveQuorum(results.map((r) => (r as SlotResult).state)) : null;
  const valid = isValidEpochId(epochId);

  return (
    <section className="panel" aria-labelledby="radar-title">
      <header className="panel-head">
        <h2 id="radar-title" className="panel-title">
          Feed Quorum Radar
        </h2>
        <span className="label !text-slate-400">3 independent hosts · 2-of-3</span>
      </header>

      <form
        className="flex flex-wrap items-end gap-3 border-b border-slate-200 p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) {
            setArmed(epochId);
            if (armed === epochId && registry) void loader.refresh(registry.map((r) => String(r.slot)));
          }
        }}
      >
        <label className="flex-1 min-w-[200px]">
          <span className="label">Epoch id</span>
          <input
            className="field mt-1"
            value={epochId}
            onChange={(e) => setEpochId(e.target.value.trim())}
            spellCheck={false}
            aria-invalid={!valid}
            aria-describedby="epoch-hint"
          />
        </label>
        <button className="btn" disabled={!valid || !registry}>
          Inspect feeds
        </button>
        <p id="epoch-hint" className={`w-full font-mono text-[11px] ${valid ? "text-slate-500" : "text-hawkish"}`}>
          {valid ? "Format YYYY-Qn-TAG. Feed URLs are read from the on-chain registry, never typed." : "Use the form 2026-Q4-FOMC (year, quarter 1-4, A-Z/0-9 tag)."}
        </p>
      </form>

      <ul className="divide-y divide-slate-200">
        {(registry ?? [null, null, null]).map((slot, i) => {
          const r = slot ? state.values.get(String(slot.slot)) : null;
          const failed = slot ? state.failedIds.has(String(slot.slot)) : false;
          return (
            <li key={slot?.slot ?? i} className="grid gap-2 p-4 sm:grid-cols-[1fr_auto]">
              <div className="min-w-0">
                <div className="label">Slot {slot ? "ABC"[slot.slot] : "·"}</div>
                <div className="truncate font-serif text-base font-semibold text-slate-900">
                  {slot ? slot.label.replace(/^[ABC]: /, "") : "—"}
                </div>
                <div className="truncate font-mono text-xs text-slate-500">{slot ? slot.active_host : "waiting for registry"}</div>
                {slot && slot.staged_url && (
                  <div className="mt-1 font-mono text-[11px] text-amber-800">
                    rotation staged → {slot.staged_host}
                  </div>
                )}
                {r && (
                  <div className="mt-1 truncate font-mono text-[11px] text-slate-500" title={r.url}>
                    {r.http !== null ? `HTTP ${r.http}` : r.note} · {short(r.state.releaseHash)}
                  </div>
                )}
                {failed && <div className="font-mono text-[11px] text-hawkish">read failed</div>}
              </div>
              <div className="flex items-center gap-2 sm:justify-end">
                {r ? (
                  <>
                    {r.state.kind === "CONFIRMED" && <StanceBadge stance={r.state.stance} />}
                    <span
                      className={`border px-2 py-0.5 font-mono text-[11px] font-semibold tracking-wider ${KIND_STYLE[r.state.kind]}`}
                    >
                      {r.state.kind.replace("_", " ")}
                    </span>
                  </>
                ) : (
                  <span className="font-mono text-[11px] text-slate-400">{armed ? "reading…" : "not inspected"}</span>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      <div className="border-t border-slate-300 p-4">
        {quorum ? (
          <div className={`border-l-4 p-3 ${VERDICT_STYLE[quorum.verdict]}`}>
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-mono text-xs font-bold tracking-[0.14em]">{VERDICT_NAMES[quorum.verdict]}</span>
              {quorum.verdict === 1 && <StanceBadge stance={quorum.stance} />}
              <span className="num text-xs">{quorum.sourceCount} source(s)</span>
            </div>
            <p className="mt-1 text-sm">{VERDICT_TEXT[quorum.verdict]}</p>
          </div>
        ) : (
          <p className="font-mono text-xs text-slate-500">Enter an epoch id and inspect to preview the quorum.</p>
        )}
        <p className="mt-2 font-mono text-[11px] text-slate-500">
          Browser preview only. Validators fetch each source themselves; a browser-side CORS or network error is shown
          as OUTAGE, exactly how a validator would treat an unreachable source.
        </p>
      </div>
    </section>
  );
}
