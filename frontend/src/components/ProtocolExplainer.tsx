const CARDS = [
  {
    n: "01",
    title: "Three hosts, no URLs from callers",
    body: "The contract holds a governed 3-slot registry: a GitHub raw mirror, a jsDelivr mirror and a GitHack mirror. Hosts must be unique across active and staged slots, so a quorum can never be bought with one host under three names. Rotation sits behind a 7-day timelock.",
  },
  {
    n: "02",
    title: "A stance, not a number",
    body: "Rate change and CPI fold into DOVISH, NEUTRAL or HAWKISH by fixed integer rules, HAWKISH first. Max LTV, borrow rate and reserve factor are read from the tier’s row — a 3.01% and a 9.00% CPI print produce the same parameters, so there is no economic drift to exploit.",
  },
  {
    n: "03",
    title: "2-of-3 without punishing outages",
    body: "Two matching sources verify a stance. If a source is down and could still complete the quorum, the round is a retryable QUORUM_FAILURE and the bond comes back. Only an explicit retraction, a conflict, two 404s or three clean answers with no match forfeit it.",
  },
  {
    n: "04",
    title: "24 hours to object",
    body: "A verified stance waits a full day before touching the vault, so borrowers can adjust. Inside that window anyone can post a bond and re-query the feeds; a retraction or conflict cancels the transition and pays the reporter’s bond to the challenger.",
  },
];

export default function ProtocolExplainer() {
  return (
    <section aria-labelledby="explain-title">
      <h2 id="explain-title" className="label mb-3">
        How the protocol is built
      </h2>
      <div className="grid gap-px border border-slate-300 bg-slate-300 sm:grid-cols-2 xl:grid-cols-4">
        {CARDS.map((c) => (
          <article key={c.n} className="bg-white p-5">
            <div className="num text-xs font-semibold text-slate-400">{c.n}</div>
            <h3 className="mt-1 font-serif text-lg font-semibold leading-snug text-slate-950">{c.title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-600">{c.body}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
