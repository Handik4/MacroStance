/**
 * SerialLoader: single-flight polling with per-item failure tracking.
 *
 * Two bugs this exists to prevent:
 *
 *  1. Overlapping abort loops. A poller that aborts the previous request on
 *     every tick starves under a slow RPC: each load is cancelled before it can
 *     finish, so the UI never updates. Here at most one load runs; a refresh
 *     requested meanwhile is COALESCED into exactly one follow-up run and the
 *     in-flight load is never aborted by a tick.
 *
 *  2. A later success masking an earlier failure. Failure is tracked per item
 *     (`failedIds`). An item leaves the set only when THAT item is read
 *     successfully; a successful read of its neighbours, or a whole later
 *     refresh that happened to skip it, never clears it.
 *
 * `reset()` is the account-switch path: it aborts in-flight work, bumps the
 * generation and empties values and failures, and results that arrive from an
 * older generation are discarded instead of leaking into the new account.
 */

export interface LoaderState<T> {
  readonly values: ReadonlyMap<string, T>;
  readonly failedIds: ReadonlySet<string>;
  readonly errors: ReadonlyMap<string, string>;
  /** per-item time of the last SUCCESSFUL read (not of the last attempt) */
  readonly readAt: ReadonlyMap<string, number>;
  readonly loading: boolean;
  readonly generation: number;
  readonly lastCompletedAt: number | null;
}

export type Reader<T> = (id: string, signal: AbortSignal) => Promise<T>;

export class SerialLoader<T> {
  private values = new Map<string, T>();
  private failed = new Set<string>();
  private errors = new Map<string, string>();
  private readAtMap = new Map<string, number>();
  private generation = 0;
  private running: Promise<void> | null = null;
  private rerun: string[] | null = null;
  private controller = new AbortController();
  private lastCompletedAt: number | null = null;
  private listeners = new Set<() => void>();
  private snapshot: LoaderState<T>;

  constructor(
    private readonly reader: Reader<T>,
    private readonly now: () => number = () => Date.now(),
  ) {
    this.snapshot = this.build(false);
  }

  getState = (): LoaderState<T> => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private build(loading: boolean): LoaderState<T> {
    return {
      values: new Map(this.values),
      failedIds: new Set(this.failed),
      errors: new Map(this.errors),
      readAt: new Map(this.readAtMap),
      loading,
      generation: this.generation,
      lastCompletedAt: this.lastCompletedAt,
    };
  }

  private publish(loading: boolean): void {
    this.snapshot = this.build(loading);
    for (const l of this.listeners) l();
  }

  /**
   * Read `ids`. If a load is already running the request is coalesced into one
   * follow-up pass over the latest requested ids and the same promise chain is
   * returned. Never aborts the load in flight.
   */
  refresh(ids: readonly string[]): Promise<void> {
    if (this.running) {
      this.rerun = [...ids];
      return this.running;
    }
    this.running = this.runLoop([...ids]).finally(() => {
      this.running = null;
    });
    return this.running;
  }

  private async runLoop(first: string[]): Promise<void> {
    let batch: string[] | null = first;
    while (batch !== null) {
      const gen = this.generation;
      this.publish(true);
      await this.readBatch(batch, gen);
      if (gen === this.generation) {
        this.lastCompletedAt = this.now();
      }
      batch = this.rerun;
      this.rerun = null;
    }
    this.publish(false);
  }

  private async readBatch(ids: string[], gen: number): Promise<void> {
    const signal = this.controller.signal;
    const unique = [...new Set(ids)];
    // Items that left the requested set are dropped together with their
    // failure: they are no longer shown, so they cannot be masked.
    for (const k of [...this.values.keys()]) {
      if (!unique.includes(k)) {
        this.values.delete(k);
        this.readAtMap.delete(k);
      }
    }
    for (const k of [...this.failed]) {
      if (!unique.includes(k)) {
        this.failed.delete(k);
        this.errors.delete(k);
      }
    }
    const settled = await Promise.allSettled(unique.map((id) => this.reader(id, signal)));
    if (gen !== this.generation) return; // account switched mid-flight: discard
    settled.forEach((res, idx) => {
      const id = unique[idx];
      if (res.status === "fulfilled") {
        this.values.set(id, res.value);
        this.readAtMap.set(id, this.now());
        this.failed.delete(id);
        this.errors.delete(id);
      } else {
        // keep the last good value (marked stale by membership in failedIds)
        this.failed.add(id);
        const e = res.reason;
        this.errors.set(id, e instanceof Error ? e.message : String(e));
      }
    });
  }

  /** Account switch / unmount: abort, clear, and invalidate in-flight results. */
  reset(): void {
    this.controller.abort();
    this.controller = new AbortController();
    this.generation++;
    this.values = new Map();
    this.failed = new Set();
    this.errors = new Map();
    this.readAtMap = new Map();
    this.rerun = null;
    this.lastCompletedAt = null;
    this.publish(false);
  }

  get hasPartialFailure(): boolean {
    return this.failed.size > 0;
  }
}
