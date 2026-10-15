/**
 * Monotone chain clock.
 *
 * Countdowns (challenge window, activation timelock, proposal expiry) are
 * driven by CHAIN time, which the browser only learns from RPC reads. The
 * failure this prevents: advancing an anchor on every poll *attempt* and
 * pairing it with the last successfully read chain timestamp. When a read
 * fails the anchor resets while the timestamp stays old, so the countdown jumps
 * BACKWARD (a 3-minute-old "ready" reappears as "00:03:00 left").
 *
 * Rules:
 *   - the clock only re-anchors on a *verified* chain timestamp (`observe`),
 *     never on a poll attempt;
 *   - between observations it advances by a steady local interval
 *     (`performance.now`-style, immune to wall-clock changes);
 *   - `estimate()` never decreases, even if a lagging node later reports an
 *     older block time: the displayed value holds until the anchor catches up.
 */

export class ChainClock {
  private anchorChain: number | null = null;
  private anchorMono = 0;
  private floor = 0;

  constructor(private readonly mono: () => number = () => performance.now()) {}

  /** Record a verified chain timestamp (seconds) as of now. */
  observe(chainSeconds: number): void {
    if (!Number.isFinite(chainSeconds) || chainSeconds <= 0) return;
    // Pin the floor first so a re-anchor to an older block cannot lower it.
    const current = this.rawEstimate();
    if (current !== null) this.floor = Math.max(this.floor, current);
    this.anchorChain = chainSeconds;
    this.anchorMono = this.mono();
  }

  private rawEstimate(): number | null {
    if (this.anchorChain === null) return null;
    const elapsed = Math.max(0, Math.floor((this.mono() - this.anchorMono) / 1000));
    return this.anchorChain + elapsed;
  }

  /** Current chain time in seconds, or null before the first verified read. */
  estimate(): number | null {
    const raw = this.rawEstimate();
    if (raw === null) return null;
    const v = Math.max(raw, this.floor);
    this.floor = v;
    return v;
  }

  get anchored(): boolean {
    return this.anchorChain !== null;
  }
}
