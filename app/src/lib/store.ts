/** The shared client store. A Resource is one thing the server owns (today's plan, a memory file, the device list...).
 *  Pages read it with useResource(); nobody keeps a private copy. When the server says a topic changed, every mounted
 *  resource on that topic refetches; unmounted ones are only marked stale and refetch when next shown. */
import { useEffect, useSyncExternalStore } from "react";

export interface ResState<T> {
  data: T | undefined;
  error: string;
  loading: boolean;
  /** true once a load has finished at least once */
  ready: boolean;
}

const errText = (e: unknown) => String((e as { detail?: string })?.detail ?? (e as Error)?.message ?? e);

export class Resource<T> {
  private state: ResState<T> = { data: undefined, error: "", loading: false, ready: false };
  private subs = new Set<() => void>();
  private stale = true;
  private run = 0;

  constructor(
    readonly key: string,
    readonly topics: string[],
    private load: () => Promise<T>,
  ) {}

  get = () => this.state;
  subscribe = (l: () => void) => {
    this.subs.add(l);
    if (this.stale && this.subs.size === 1) void this.refresh();
    return () => void this.subs.delete(l);
  };
  mounted = () => this.subs.size > 0;

  private set(next: Partial<ResState<T>>) {
    this.state = { ...this.state, ...next };
    this.subs.forEach((l) => l());
  }

  /** Re-read from the server. A slow older answer never overwrites a newer one. */
  async refresh(): Promise<void> {
    const id = ++this.run;
    this.stale = false;
    this.set({ loading: true });
    try {
      const data = await this.load();
      if (id === this.run) this.set({ data, error: "", loading: false, ready: true });
    } catch (e) {
      if (id === this.run) this.set({ error: errText(e), loading: false, ready: true });
    }
  }

  markStale() {
    this.stale = true;
  }
}

const all = new Map<string, Resource<unknown>>();

/** One shared instance per key, so two components asking for the same thing share one fetch. */
export function resource<T>(key: string, topics: string[], load: () => Promise<T>): Resource<T> {
  let r = all.get(key) as Resource<T> | undefined;
  if (!r) {
    r = new Resource(key, topics, load);
    all.set(key, r as Resource<unknown>);
  }
  return r;
}

/** The server said `what` changed ("plan", "memory", ... or "all"). */
export function invalidate(what: string) {
  for (const r of all.values()) {
    if (what !== "all" && !r.topics.includes(what)) continue;
    if (r.mounted()) void r.refresh();
    else r.markStale();
  }
}

export function useResource<T>(r: Resource<T>): ResState<T> & { refresh: () => Promise<void> } {
  const s = useSyncExternalStore(r.subscribe, r.get);
  return { ...s, refresh: () => r.refresh() };
}

/** For tests: forget everything. */
export function resetStore() {
  all.clear();
}

export function useOnMount(fn: () => void | (() => void)) {
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(fn, []);
}
