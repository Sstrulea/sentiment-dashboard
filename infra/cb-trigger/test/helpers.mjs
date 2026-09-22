// Fakes for the Worker tests: a KV namespace, a GitHub that answers the three endpoints the Worker uses, and a clock in UTC.
export class FakeKV {
  constructor() { this.m = new Map(); this.puts = []; }
  async get(k) { return this.m.has(k) ? this.m.get(k) : null; }
  async put(k, v, opts) { this.m.set(k, v); this.puts.push([k, opts]); }
}

export const at = (iso) => Date.parse(iso);

export function env(over = {}) {
  return { STATE: new FakeKV(), GH_REPO: "owner/repo", GH_REF: "main", SCHEDULE_PATH: "data/cb/trigger_schedule.json", GH_API: "https://gh.test", GH_TOKEN: "SECRET-TOKEN", ...over };
}

const res = (status, body = "", headers = {}) => ({ status, ok: status >= 200 && status < 300, headers: { get: (k) => headers[k.toLowerCase()] ?? null }, text: async () => body });

/** A GitHub: `schedule` is the file's content (or a function of the request), `dispatch` a list of statuses to answer in turn (last one repeats), `rss` the BoJ feed. Every request is recorded. */
export function github({ schedule, etag = '"v1"', dispatch = [204], issue = 201, rss = null, scheduleStatus = 200 } = {}) {
  const calls = [];
  let n = 0;
  const fetch = async (url, init = {}) => {
    calls.push({ url, method: init.method || "GET", headers: init.headers || {}, body: init.body ? JSON.parse(init.body) : null });
    if (url.includes("/contents/")) {
      if (scheduleStatus !== 200) return res(scheduleStatus, "boom");
      if ((init.headers || {})["If-None-Match"] === etag) return res(304);
      return res(200, JSON.stringify(schedule), { etag });
    }
    if (url.endsWith("/dispatches")) { const s = dispatch[Math.min(n++, dispatch.length - 1)]; return s === "network" ? Promise.reject(new Error("socket hang up")) : res(s, s === 204 ? "" : `{"message":"status ${s}"}`); }
    if (url.endsWith("/issues")) return res(issue, "{}");
    if (url === "https://www.boj.or.jp/en/rss/whatsnew.xml") return rss === null ? res(503, "down") : res(200, rss);
    throw new Error("unexpected request " + url);
  };
  return { fetch, calls, dispatches: () => calls.filter((c) => c.url.endsWith("/dispatches")), issues: () => calls.filter((c) => c.url.endsWith("/issues")) };
}

export const noSleep = () => { const waits = []; return { sleep: async (t) => { waits.push(t); }, waits }; };

export const event = (over = {}) => ({ id: "decision:USD:2026-09-16", kind: "decision", bank: "USD", date: "2026-09-16", at: "2026-09-16T18:00:00Z", fire_at: "2026-09-16T18:01:00Z", lead_minutes: 5, grace_minutes: 90,
  dispatch: { workflow: "cb-refresh.yml", inputs: { event: "decision", bank: "USD", date: "2026-09-16" } }, ...over });

export const boj = (over = {}) => ({ id: "decision:JPY:2026-09-18", kind: "boj_window", bank: "JPY", date: "2026-09-18", window_start: "2026-09-18T02:30:00Z", window_end: "2026-09-18T04:30:00Z",
  fallback_at_window_end: true, grace_minutes: 30, dispatch: { workflow: "cb-refresh.yml", inputs: { event: "decision", bank: "JPY", date: "2026-09-18" } }, ...over });

export const sched = (events = [], regular = []) => ({ version: 1, tick_minutes: 5, boj: { rss: "https://www.boj.or.jp/en/rss/whatsnew.xml", item_match: "/mopo/mpmdeci/" }, events, regular });

export const rssOf = (...items) => `<?xml version="1.0"?><rss version="2.0"><channel><title>Bank of Japan</title>${items.map((i) =>
  `<item><title>${i.title}</title><description></description><pubDate>${i.pub}</pubDate><link>${i.link}</link></item>`).join("")}</channel></rss>`;
