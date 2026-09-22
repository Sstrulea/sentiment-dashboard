// The GitHub side of the Worker: read the schedule file (conditional GET, cached in KV), dispatch a workflow (3 retries with backoff), open an issue when a dispatch finally fails.
// The token is the Worker secret GH_TOKEN; it is only ever sent to env.GH_API.

const RETRY_DELAYS_MS = [2000, 6000, 18000];               // backoff between the 4 attempts (1 + 3 retries)

const headers = (env, extra = {}) => ({
  Authorization: `Bearer ${env.GH_TOKEN}`,
  Accept: "application/vnd.github+json",
  "X-GitHub-Api-Version": "2022-11-28",
  "User-Agent": "cb-trigger-worker",
  ...extra,
});

/** Worth another attempt: the network, a server error, a timeout, a rate limit. Not: a wrong token, a missing workflow, invalid inputs (the same request fails the same way). */
export function retryable(status, res) {
  if (status === 0 || status >= 500 || status === 429 || status === 408) return true;
  return status === 403 && Boolean(res && res.headers && (res.headers.get("retry-after") || res.headers.get("x-ratelimit-remaining") === "0"));
}

/** POST workflow_dispatch. Returns {ok, attempts, status, error}. `sleep` is injectable (tests). */
export async function dispatchWorkflow(env, workflow, inputs, { fetch = globalThis.fetch, sleep = (t) => new Promise((r) => setTimeout(r, t)) } = {}) {
  const url = `${env.GH_API}/repos/${env.GH_REPO}/actions/workflows/${workflow}/dispatches`;
  const body = JSON.stringify(inputs ? { ref: env.GH_REF, inputs } : { ref: env.GH_REF });
  let last = { status: 0, error: "" };
  for (let attempt = 1; attempt <= RETRY_DELAYS_MS.length + 1; attempt++) {
    let res = null;
    try {
      res = await fetch(url, { method: "POST", headers: headers(env, { "Content-Type": "application/json" }), body });
      last = { status: res.status, error: res.status === 204 ? "" : (await res.text()).slice(0, 300) };
    } catch (e) {
      last = { status: 0, error: String(e && e.message || e).slice(0, 300) };
    }
    if (last.status === 204) return { ok: true, attempts: attempt, ...last };
    if (!retryable(last.status, res) || attempt > RETRY_DELAYS_MS.length) return { ok: false, attempts: attempt, ...last };
    await sleep(RETRY_DELAYS_MS[attempt - 1]);
  }
  return { ok: false, attempts: RETRY_DELAYS_MS.length + 1, ...last };
}

/** The issue the repository owner is emailed about when a dispatch fails for good. */
export async function openIssue(env, title, body, { fetch = globalThis.fetch } = {}) {
  try {
    const res = await fetch(`${env.GH_API}/repos/${env.GH_REPO}/issues`, { method: "POST", headers: headers(env, { "Content-Type": "application/json" }), body: JSON.stringify({ title, body }) });
    return { ok: res.status === 201, status: res.status };
  } catch (e) {
    return { ok: false, status: 0, error: String(e && e.message || e) };
  }
}

/** The schedule file from the repository (works for a private one: the token reads it through the contents API). A conditional GET - a 304 costs no rate limit - with the last good copy kept
 *  in KV, which also serves when GitHub cannot be reached. Returns {schedule, source: "github" | "cache" | "stale"} or throws when there is neither. */
export async function loadSchedule(env, { fetch = globalThis.fetch } = {}) {
  const cachedEtag = await env.STATE.get("schedule:etag");
  const cachedBody = await env.STATE.get("schedule:body");
  const url = `${env.GH_API}/repos/${env.GH_REPO}/contents/${env.SCHEDULE_PATH}?ref=${encodeURIComponent(env.GH_REF)}`;
  try {
    const res = await fetch(url, { headers: headers(env, { Accept: "application/vnd.github.raw+json", ...(cachedEtag && cachedBody ? { "If-None-Match": cachedEtag } : {}) }) });
    if (res.status === 304 && cachedBody) return { schedule: JSON.parse(cachedBody), source: "cache" };
    if (res.status === 200) {
      const text = await res.text();
      const schedule = JSON.parse(text);
      await env.STATE.put("schedule:body", text);
      if (res.headers.get("etag")) await env.STATE.put("schedule:etag", res.headers.get("etag"));
      return { schedule, source: "github" };
    }
    throw new Error(`the schedule file could not be read: GitHub answered ${res.status}`);          // (the catch below serves the cached copy, when there is one)
  } catch (e) {
    if (cachedBody) return { schedule: JSON.parse(cachedBody), source: "stale", error: String(e && e.message || e) };
    throw e;
  }
}
