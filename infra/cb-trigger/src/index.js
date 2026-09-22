// cb-trigger: at every cron tick (every 5 minutes) fire what the schedule says is due - the statement watch a minute after a decision, the transcript / video run two hours after a
// press conference, the BoJ when its RSS shows the decision, and the regular cb-refresh / econ-refresh runs. One dispatch per (event, date): the mark is in KV. A dispatch that fails
// after its retries opens an issue in the repository (an email to its owner) - once per event - and is tried again at the next tick until the event's grace period ends.
import { dispatchWorkflow, loadSchedule, openIssue } from "./github.js";
import { firstDecisionItem, plan, rssItems } from "./schedule.js";

const DONE_TTL_S = 14 * 24 * 3600;
const log = (o) => console.log(JSON.stringify(o));

async function fire(env, nowMs, item, deps) {
  if (await env.STATE.get(`done:${item.key}`)) return { key: item.key, action: "skip", why: "already dispatched" };
  const res = await dispatchWorkflow(env, item.workflow, item.inputs, deps);
  if (res.ok) {
    await env.STATE.put(`done:${item.key}`, new Date(nowMs).toISOString(), { expirationTtl: DONE_TTL_S });
    return { key: item.key, action: "dispatched", workflow: item.workflow, inputs: item.inputs, attempts: res.attempts, why: item.why };
  }
  const out = { key: item.key, action: "failed", workflow: item.workflow, attempts: res.attempts, status: res.status, error: res.error, why: item.why };
  if (!(await env.STATE.get(`issue:${item.key}`))) {                     // one issue per event, however many ticks retry it
    const issue = await openIssue(env, `cb-trigger: dispatch failed - ${item.key}`,
      `The Cloudflare Worker cb-trigger could not dispatch \`${item.workflow}\` for **${item.why}** after ${res.attempts} attempts.\n\n` +
      `- when: ${new Date(nowMs).toISOString()}\n- last status: ${res.status}\n- last error: ${res.error || "(none)"}\n- inputs: \`${JSON.stringify(item.inputs || {})}\`\n\n` +
      `The regular cb-refresh runs will collect the documents anyway (every 2 hours). To run it now: Actions -> ${item.workflow} -> Run workflow.`, deps);
    if (issue.ok) await env.STATE.put(`issue:${item.key}`, new Date(nowMs).toISOString(), { expirationTtl: DONE_TTL_S });
    out.issue = issue.ok ? "opened" : `not opened (${issue.status})`;
  }
  return out;
}

/** One tick. Returns what was done (also logged as JSON lines: `wrangler tail`). `deps` = {fetch, sleep} for the tests. */
export async function run(env, nowMs, deps = {}) {
  let loaded;
  try {
    loaded = await loadSchedule(env, deps);
  } catch (e) {
    const out = [{ action: "error", why: `no schedule: ${String(e.message || e)}` }];
    out.forEach(log);
    return out;
  }
  const { due, windows } = plan(loaded.schedule, nowMs);
  const items = [...due];
  const out = [];
  if (windows.length) {
    const watching = windows.filter((w) => w.phase === "watch");
    let rss = null;
    if (watching.length) {
      try {
        const res = await (deps.fetch || globalThis.fetch)(loaded.schedule.boj.rss, { headers: { "User-Agent": "cb-trigger-worker (+https://github.com/" + env.GH_REPO + ")" } });
        rss = res.ok ? rssItems(await res.text()) : null;
        if (!res.ok) out.push({ action: "warn", why: `BoJ RSS answered ${res.status}` });
      } catch (e) {
        out.push({ action: "warn", why: `BoJ RSS unreachable: ${String(e.message || e)}` });
      }
    }
    for (const { event, phase } of windows) {
      const base = { key: event.id, kind: event.kind, workflow: event.dispatch.workflow, inputs: event.dispatch.inputs };
      if (phase === "fallback") items.push({ ...base, why: `BoJ window closed without an RSS item: fallback ${event.date}` });
      else if (rss) {
        const hit = firstDecisionItem(rss, event, loaded.schedule.boj.item_match);
        if (hit) items.push({ ...base, why: `BoJ RSS item "${hit.title}" (${new Date(hit.pubMs).toISOString()})` });
      }
    }
  }
  for (const item of items) out.push(await fire(env, nowMs, item, deps));
  if (loaded.source === "stale") out.push({ action: "warn", why: `the schedule is the cached copy: ${loaded.error}` });
  out.forEach(log);
  if (!out.length) log({ action: "idle", at: new Date(nowMs).toISOString(), schedule: loaded.source });
  return out;
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(run(env, event.scheduledTime));
  },
};
