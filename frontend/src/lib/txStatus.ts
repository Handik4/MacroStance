/**
 * Account-keyed transaction status.
 *
 * A transaction banner (pending / accepted / failed, with its hash) is the
 * outcome of an action taken by ONE wallet. Holding it in a single global slot
 * leaks it across an account switch: wallet B opens the page and is shown
 * "Claim payout accepted" for wallet A's transaction. Here every status is
 * keyed by the originating address, `get(account)` can only ever return that
 * address's own entry, and `purgeExcept` drops everything else the moment the
 * wallet reports `accountsChanged`.
 *
 * Completions that arrive after a purge are ignored (each entry has an id), so
 * a slow transaction from the previous account cannot resurrect itself.
 */

export type TxPhase = "pending" | "done" | "failed";

export interface TxStatus {
  readonly id: number;
  readonly account: string;
  readonly phase: TxPhase;
  readonly label: string;
  readonly hash?: string;
  readonly message?: string;
}

const norm = (a: string) => a.toLowerCase();

export class TxStatusStore {
  private entries = new Map<string, TxStatus>();
  private seq = 0;
  private listeners = new Set<() => void>();
  private version = 0;

  subscribe = (l: () => void): (() => void) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };

  /** Monotone counter; changes whenever any entry changes (useSyncExternalStore). */
  getVersion = (): number => this.version;

  private emit(): void {
    this.version++;
    for (const l of this.listeners) l();
  }

  /** Start a pending status for `account`; returns the id to complete it with. */
  begin(account: string, label: string): number {
    const id = ++this.seq;
    this.entries.set(norm(account), { id, account: norm(account), phase: "pending", label });
    this.emit();
    return id;
  }

  /** No-op when the entry was purged or replaced since `begin`. */
  succeed(account: string, id: number, hash: string): boolean {
    return this.settle(account, id, (e) => ({ ...e, phase: "done", hash }));
  }

  fail(account: string, id: number, message: string): boolean {
    return this.settle(account, id, (e) => ({ ...e, phase: "failed", message }));
  }

  private settle(account: string, id: number, f: (e: TxStatus) => TxStatus): boolean {
    const k = norm(account);
    const cur = this.entries.get(k);
    if (!cur || cur.id !== id) return false;
    this.entries.set(k, f(cur));
    this.emit();
    return true;
  }

  /** The status visible to `account`, or null. Never another account's. */
  get(account: string | null): TxStatus | null {
    if (!account) return null;
    return this.entries.get(norm(account)) ?? null;
  }

  dismiss(account: string | null): void {
    if (account && this.entries.delete(norm(account))) this.emit();
  }

  /** Drop every status that did not originate from `account` (null drops all). */
  purgeExcept(account: string | null): void {
    const keep = account ? norm(account) : null;
    let changed = false;
    for (const k of [...this.entries.keys()]) {
      if (k !== keep) {
        this.entries.delete(k);
        changed = true;
      }
    }
    if (changed) this.emit();
  }

  get size(): number {
    return this.entries.size;
  }
}
