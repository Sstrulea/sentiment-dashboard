import test from "node:test";
import assert from "node:assert/strict";
import { firstDecisionItem, plan, regularDue, regularKey, rssItems, timedDue, windowPhase } from "../src/schedule.js";
import { at, boj, event, rssOf, sched } from "./helpers.mjs";

test("a decision is due from the last tick not later than its fire time, and for its grace period", () => {
  const e = event();                                                              // fire 18:01, lead 5, grace 90
  assert.equal(timedDue(e, at("2026-09-16T17:55:00Z")), false);                   // two ticks before: not yet
  assert.equal(timedDue(e, at("2026-09-16T17:56:00Z")), true);                    // the lead starts here
  assert.equal(timedDue(e, at("2026-09-16T18:00:00Z")), true);                    // the tick of the decision itself: the job is polling by 18:01
  assert.equal(timedDue(e, at("2026-09-16T18:05:00Z")), true);                    // a missed tick is made up
  assert.equal(timedDue(e, at("2026-09-16T19:31:00Z")), true);
  assert.equal(timedDue(e, at("2026-09-16T19:31:01Z")), false);                   // grace over
});

test("the same decision an hour later in UTC (the other side of a DST change) is due an hour later: the file holds instants, not local times", () => {
  const summer = event({ fire_at: "2026-09-16T18:01:00Z" }), winter = event({ id: "decision:USD:2026-12-09", fire_at: "2026-12-09T19:01:00Z" });
  assert.equal(timedDue(summer, at("2026-09-16T18:00:00Z")), true);
  assert.equal(timedDue(winter, at("2026-12-09T18:00:00Z")), false);
  assert.equal(timedDue(winter, at("2026-12-09T19:00:00Z")), true);
});

test("a conference has no lead: due at its fire time, not before", () => {
  const c = { id: "conference:USD:2026-09-16", kind: "conference", fire_at: "2026-09-16T20:30:00Z", lead_minutes: 0, grace_minutes: 240, dispatch: { workflow: "cb-refresh.yml", inputs: {} } };
  assert.equal(timedDue(c, at("2026-09-16T20:25:00Z")), false);
  assert.equal(timedDue(c, at("2026-09-16T20:30:00Z")), true);
  assert.equal(timedDue(c, at("2026-09-17T00:30:00Z")), true);
  assert.equal(timedDue(c, at("2026-09-17T00:31:00Z")), false);
});

test("the BoJ window: watch inside 11:30-13:30 JST (02:30-04:30Z), fallback after it for the grace period, idle otherwise", () => {
  const e = boj();
  assert.equal(windowPhase(e, at("2026-09-18T02:25:00Z")), "idle");
  assert.equal(windowPhase(e, at("2026-09-18T02:30:00Z")), "watch");             // 11:30 JST
  assert.equal(windowPhase(e, at("2026-09-18T04:25:00Z")), "watch");
  assert.equal(windowPhase(e, at("2026-09-18T04:30:00Z")), "fallback");          // 13:30 JST
  assert.equal(windowPhase(e, at("2026-09-18T05:00:00Z")), "fallback");
  assert.equal(windowPhase(e, at("2026-09-18T05:05:00Z")), "idle");
  assert.equal(windowPhase(boj({ fallback_at_window_end: false }), at("2026-09-18T04:30:00Z")), "idle");
  assert.equal(windowPhase(e, at("2026-09-19T03:00:00Z")), "idle");              // the next day: the window is of its own date
});

test("regular runs: the first tick not earlier than the minute, on the listed hours and days only", () => {
  const cb = { workflow: "cb-refresh.yml", minute: 37, hours: [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22], dow: [0, 1, 2, 3, 4, 5, 6] };
  assert.equal(regularDue(cb, at("2026-09-21T10:35:00Z")), false);               // before :37
  assert.equal(regularDue(cb, at("2026-09-21T10:40:00Z")), true);                // the :40 tick
  assert.equal(regularDue(cb, at("2026-09-21T10:45:00Z")), false);               // one run per hour
  assert.equal(regularDue(cb, at("2026-09-21T11:40:00Z")), false);               // odd hour
  const econ = { workflow: "econ-refresh.yml", minute: 5, hours: [0, 4, 8, 12, 16, 20], dow: [0, 6] };
  assert.equal(regularDue(econ, at("2026-09-19T08:05:00Z")), true);              // a Saturday
  assert.equal(regularDue(econ, at("2026-09-21T08:05:00Z")), false);             // a Monday
  const five = { ...cb, minute: 35 };
  assert.equal(regularDue(five, at("2026-09-21T10:35:00Z")), true);
  assert.equal(regularDue(five, at("2026-09-21T10:40:00Z")), false);            // 5 minutes later is the NEXT tick's: never two ticks for one run
  assert.equal(regularKey(cb, at("2026-09-21T10:40:00Z")), "regular:cb-refresh.yml:2026-09-21T10");
  assert.equal(regularKey(cb, at("2026-09-21T10:40:00Z")), regularKey(cb, at("2026-09-21T10:40:30Z")));
});

test("plan: what is due at a tick, and the BoJ windows apart", () => {
  const s = sched([event(), boj(), event({ id: "decision:EUR:2026-09-10", fire_at: "2026-09-10T12:16:00Z" })], [{ workflow: "econ-refresh.yml", minute: 5, hours: [18], dow: [3] }]);
  const p = plan(s, at("2026-09-16T18:05:00Z"));                                   // a Wednesday
  assert.deepEqual(p.due.map((d) => d.key), ["decision:USD:2026-09-16", "regular:econ-refresh.yml:2026-09-16T18"]);
  assert.deepEqual(p.due[0].inputs, { event: "decision", bank: "USD", date: "2026-09-16" });
  assert.equal(p.due[1].inputs, undefined);
  assert.equal(p.windows.length, 0);
  assert.equal(plan(s, at("2026-09-18T03:00:00Z")).windows[0].phase, "watch");
});

test("the RSS: items with their times, a link is matched only inside the window", () => {
  const xml = rssOf({ title: "Amendment &quot;X&quot;", pub: "Fri, 18 Sep 2026 12:30:00 +0900", link: "http://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/mpr260918b.pdf" },
                    { title: "Flow of Funds", pub: "Thu, 17 Sep 2026 08:50:00 +0900", link: "http://www.boj.or.jp/en/statistics/sj/sj.htm" },
                    { title: "Change in the Guideline", pub: "Fri, 18 Sep 2026 12:40:00 +0900", link: "http://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/k260918a.pdf" });
  const items = rssItems(xml);
  assert.equal(items.length, 3);
  assert.equal(items[0].pubMs, at("2026-09-18T03:30:00Z"));                        // +0900 -> UTC
  const hit = firstDecisionItem(items, boj(), "/mopo/mpmdeci/");
  assert.equal(hit.link.endsWith("mpr260918b.pdf"), true);                        // the earliest matching item since the window opened
  assert.equal(firstDecisionItem(items, boj({ window_start: "2026-09-18T03:45:00Z" }), "/mopo/mpmdeci/"), null);   // both matching items are older than this window
  assert.equal(firstDecisionItem(items.slice(1, 2), boj(), "/mopo/mpmdeci/"), null);                              // a release that is not a decision document
  const other = rssItems(rssOf({ title: "Flow of Funds", pub: "Fri, 18 Sep 2026 12:50:00 +0900", link: "http://www.boj.or.jp/en/statistics/sj/sj.htm" }));
  assert.equal(firstDecisionItem(other, boj(), "/mopo/mpmdeci/"), null);        // published in the window, but not a decision document
  assert.deepEqual(rssItems(""), []);
});
