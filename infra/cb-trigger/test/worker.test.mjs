import test from "node:test";
import assert from "node:assert/strict";
import { run } from "../src/index.js";
import { at, boj, env, event, github, noSleep, rssOf, sched } from "./helpers.mjs";

const TICK = "2026-09-16T18:00:00Z";

test("a due decision is dispatched once, with the ref and the inputs, and the token only goes to the GitHub API", async () => {
  const e = env(), gh = github({ schedule: sched([event()]) }), { sleep } = noSleep();
  const out = await run(e, at(TICK), { fetch: gh.fetch, sleep });
  assert.deepEqual(out.map((o) => [o.action, o.key]), [["dispatched", "decision:USD:2026-09-16"]]);
  const d = gh.dispatches();
  assert.equal(d.length, 1);
  assert.equal(d[0].url, "https://gh.test/repos/owner/repo/actions/workflows/cb-refresh.yml/dispatches");
  assert.deepEqual(d[0].body, { ref: "main", inputs: { event: "decision", bank: "USD", date: "2026-09-16" } });
  assert.ok(gh.calls.every((c) => c.url.startsWith("https://gh.test/")), "no request outside the GitHub API");
  assert.equal(d[0].headers.Authorization, "Bearer SECRET-TOKEN");
  assert.equal(e.STATE.puts.find(([k]) => k === "done:decision:USD:2026-09-16")[1].expirationTtl, 14 * 24 * 3600);
});

test("dedupe: the same event and date is never dispatched twice, on the next tick or after a restart", async () => {
  const e = env(), gh = github({ schedule: sched([event()]) }), { sleep } = noSleep();
  await run(e, at(TICK), { fetch: gh.fetch, sleep });
  const again = await run(e, at("2026-09-16T18:05:00Z"), { fetch: gh.fetch, sleep });
  assert.deepEqual(again.map((o) => o.action), ["skip"]);
  assert.equal(gh.dispatches().length, 1);
  const other = await run(e, at("2026-09-16T18:05:00Z"), { fetch: github({ schedule: sched([event({ id: "decision:EUR:2026-09-16", bank: "EUR" })]), etag: '"v2"' }).fetch, sleep });
  assert.equal(other[0].action, "dispatched");                                    // another (event, date) is another key
});

test("nothing is due: no dispatch, an idle line", async () => {
  const gh = github({ schedule: sched([event()]) });
  const out = await run(env(), at("2026-09-16T12:00:00Z"), { fetch: gh.fetch, sleep: noSleep().sleep });
  assert.deepEqual(out, []);
  assert.equal(gh.dispatches().length, 0);
});

test("3 retries with backoff, then success: 4 attempts at most, waiting 2 s, 6 s, 18 s", async () => {
  const e = env(), gh = github({ schedule: sched([event()]), dispatch: [502, "network", 429, 204] }), w = noSleep();
  const out = await run(e, at(TICK), { fetch: gh.fetch, sleep: w.sleep });
  assert.equal(out[0].action, "dispatched");
  assert.equal(out[0].attempts, 4);
  assert.deepEqual(w.waits, [2000, 6000, 18000]);
  assert.equal(gh.issues().length, 0);
});

test("a dispatch that fails for good opens ONE issue (never the token in it), and is tried again at the next tick", async () => {
  const e = env(), gh = github({ schedule: sched([event()]), dispatch: [500] }), w = noSleep();
  const first = await run(e, at(TICK), { fetch: gh.fetch, sleep: w.sleep });
  assert.equal(first[0].action, "failed");
  assert.equal(first[0].attempts, 4);
  assert.equal(first[0].issue, "opened");
  assert.equal(gh.dispatches().length, 4);
  const issue = gh.issues();
  assert.equal(issue.length, 1);
  assert.match(issue[0].body.title, /dispatch failed - decision:USD:2026-09-16/);
  assert.match(issue[0].body.body, /cb-refresh\.yml/);
  assert.ok(!JSON.stringify(issue[0].body).includes("SECRET-TOKEN"));
  const second = await run(e, at("2026-09-16T18:05:00Z"), { fetch: gh.fetch, sleep: w.sleep });   // still failing: retried, but no second issue
  assert.equal(second[0].action, "failed");
  assert.equal(second[0].issue, undefined);
  assert.equal(gh.issues().length, 1);
  assert.equal(await e.STATE.get("done:decision:USD:2026-09-16"), null);         // not marked done: a later tick may still succeed
  const healed = github({ schedule: sched([event()]) });
  const third = await run(e, at("2026-09-16T18:10:00Z"), { fetch: healed.fetch, sleep: w.sleep });
  assert.equal(third[0].action, "dispatched");
});

test("a request GitHub will always refuse (wrong token, unknown workflow, invalid inputs) is not retried", async () => {
  for (const status of [401, 404, 422, 403]) {
    const gh = github({ schedule: sched([event()]), dispatch: [status] }), w = noSleep();
    const out = await run(env(), at(TICK), { fetch: gh.fetch, sleep: w.sleep });
    assert.equal(out[0].attempts, 1, String(status));
    assert.deepEqual(w.waits, []);
    assert.equal(out[0].action, "failed");
  }
});

test("the schedule is read with a conditional GET and kept in KV: a 304 uses the copy, an unreachable GitHub uses the stale copy", async () => {
  const e = env(), gh = github({ schedule: sched([event()]) }), { sleep } = noSleep();
  await run(e, at("2026-09-16T12:00:00Z"), { fetch: gh.fetch, sleep });
  assert.equal(gh.calls[0].headers["If-None-Match"], undefined);                 // the first read is unconditional
  assert.equal(await e.STATE.get("schedule:etag"), '"v1"');
  await run(e, at("2026-09-16T12:05:00Z"), { fetch: gh.fetch, sleep });
  assert.equal(gh.calls.filter((c) => c.url.includes("/contents/"))[1].headers["If-None-Match"], '"v1"');
  const down = github({ schedule: sched([event()]), scheduleStatus: 502 });
  const out = await run(e, at(TICK), { fetch: down.fetch, sleep });               // GitHub is failing the read: the cached schedule still fires the decision
  assert.ok(out.some((o) => o.action === "dispatched"));
  assert.ok(out.some((o) => o.action === "warn" && /cached copy/.test(o.why)));
  const url = gh.calls[0].url;
  assert.match(url, /contents\/data\/cb\/trigger_schedule\.json\?ref=main$/);
});

test("no schedule at all (nothing cached, GitHub down): an error line and no dispatch", async () => {
  const out = await run(env(), at(TICK), { fetch: github({ schedule: null, scheduleStatus: 404 }).fetch, sleep: noSleep().sleep });
  assert.equal(out[0].action, "error");
});

test("regular runs are fired once per hour and workflow", async () => {
  const s = sched([], [{ workflow: "cb-refresh.yml", minute: 37, hours: [10], dow: [1] }, { workflow: "econ-refresh.yml", minute: 5, hours: [10], dow: [1] }]);
  const e = env(), gh = github({ schedule: s }), { sleep } = noSleep();
  await run(e, at("2026-09-21T10:05:00Z"), { fetch: gh.fetch, sleep });
  await run(e, at("2026-09-21T10:40:00Z"), { fetch: gh.fetch, sleep });
  await run(e, at("2026-09-21T10:45:00Z"), { fetch: gh.fetch, sleep });
  const sent = gh.dispatches().map((d) => [d.url.split("/workflows/")[1], d.body.inputs]);
  assert.deepEqual(sent, [["econ-refresh.yml/dispatches", undefined], ["cb-refresh.yml/dispatches", undefined]]);
});

const decisionRss = rssOf({ title: "Change in the Guideline", pub: "Fri, 18 Sep 2026 12:40:00 +0900", link: "http://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/k260918a.pdf" },
                          { title: "Flow of Funds", pub: "Thu, 17 Sep 2026 08:50:00 +0900", link: "http://www.boj.or.jp/en/statistics/sj/sj.htm" });

test("BoJ: in the window the RSS is read every tick and the first decision item fires the dispatch, once", async () => {
  const e = env(), gh = github({ schedule: sched([boj()]), rss: rssOf({ title: "Flow of Funds", pub: "Thu, 17 Sep 2026 08:50:00 +0900", link: "http://www.boj.or.jp/en/statistics/sj/sj.htm" }) }), { sleep } = noSleep();
  assert.deepEqual(await run(e, at("2026-09-18T02:25:00Z"), { fetch: gh.fetch, sleep }), []);   // before the window: no RSS request
  assert.equal(gh.calls.filter((c) => c.url.includes("boj.or.jp")).length, 0);
  assert.deepEqual(await run(e, at("2026-09-18T02:30:00Z"), { fetch: gh.fetch, sleep }), []);   // in the window: read, nothing new
  assert.equal(gh.calls.filter((c) => c.url.includes("boj.or.jp")).length, 1);
  const gh2 = github({ schedule: sched([boj()]), rss: decisionRss });
  const out = await run(e, at("2026-09-18T03:40:00Z"), { fetch: gh2.fetch, sleep });             // 12:40 JST
  assert.equal(out[0].action, "dispatched");
  assert.match(out[0].why, /Change in the Guideline/);
  assert.deepEqual(gh2.dispatches()[0].body.inputs, { event: "decision", bank: "JPY", date: "2026-09-18" });
  const again = await run(e, at("2026-09-18T03:45:00Z"), { fetch: gh2.fetch, sleep });
  assert.deepEqual(again.map((o) => o.action), ["skip"]);
  const fallback = await run(e, at("2026-09-18T04:30:00Z"), { fetch: gh2.fetch, sleep });        // the window closes: already done, no second dispatch
  assert.deepEqual(fallback.map((o) => o.action), ["skip"]);
  assert.equal(gh2.dispatches().length, 1);
});

test("BoJ: no RSS item (or an RSS that is down) by the end of the window: the fallback fires once", async () => {
  const e = env(), gh = github({ schedule: sched([boj()]), rss: null }), { sleep } = noSleep();   // the RSS answers 503
  const inside = await run(e, at("2026-09-18T03:00:00Z"), { fetch: gh.fetch, sleep });
  assert.deepEqual(inside.map((o) => o.action), ["warn"]);                                        // a warning, no dispatch
  const end = await run(e, at("2026-09-18T04:30:00Z"), { fetch: gh.fetch, sleep });
  assert.equal(end[0].action, "dispatched");
  assert.match(end[0].why, /fallback/);
  assert.equal(gh.calls.filter((c) => c.url.includes("boj.or.jp")).length, 1);                    // no RSS read after the window
  const later = await run(e, at("2026-09-18T04:35:00Z"), { fetch: gh.fetch, sleep });
  assert.deepEqual(later.map((o) => o.action), ["skip"]);
  assert.equal(gh.dispatches().length, 1);
});

test("a BoJ decision and a timed one at the same tick are both fired, each under its own key", async () => {
  const e = env(), gh = github({ schedule: sched([boj({ window_start: "2026-09-16T17:00:00Z", window_end: "2026-09-16T19:00:00Z", id: "decision:JPY:2026-09-16" }), event()]),
    rss: rssOf({ title: "Statement", pub: "Thu, 17 Sep 2026 02:59:00 +0900", link: "http://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/k260917a.pdf" }) });
  const out = await run(e, at(TICK), { fetch: gh.fetch, sleep: noSleep().sleep });
  assert.deepEqual(out.map((o) => o.key).sort(), ["decision:JPY:2026-09-16", "decision:USD:2026-09-16"]);
});
