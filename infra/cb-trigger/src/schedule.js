// What is due at a tick, from the schedule file (data/cb/trigger_schedule.json). Pure: no I/O, no clock - `nowMs` is the cron's scheduled time. Every instant in the file is UTC:
// the time zones and DST were resolved by the generator (src/cb_trigger/schedule.py), so nothing here knows a bank or a zone.

const MIN = 60 * 1000;

const ms = (iso) => Date.parse(iso);

/** A timed event (a decision, a conference): due from `lead_minutes` before its fire_at (cron ticks are 5 minutes apart: the last tick not later than fire_at) until `grace_minutes` after it. */
export function timedDue(event, nowMs) {
  const fire = ms(event.fire_at);
  return nowMs >= fire - (event.lead_minutes || 0) * MIN && nowMs <= fire + (event.grace_minutes || 0) * MIN;
}

/** The BoJ window event: "watch" while the window is open (read the RSS), "fallback" from its end for `grace_minutes` (fire anyway), else "idle". */
export function windowPhase(event, nowMs) {
  const start = ms(event.window_start), end = ms(event.window_end);
  if (nowMs >= start && nowMs < end) return "watch";
  if (event.fallback_at_window_end && nowMs >= end && nowMs <= end + (event.grace_minutes || 0) * MIN) return "fallback";
  return "idle";
}

/** A regular run (minute, hours, dow - all UTC) is due at the first tick that is not earlier than its minute: 37 -> the :40 tick, 5 -> the :05 tick. */
export function regularDue(entry, nowMs, tickMinutes = 5) {
  const d = new Date(nowMs);
  if (!entry.dow.includes(d.getUTCDay()) || !entry.hours.includes(d.getUTCHours())) return false;
  const delta = d.getUTCMinutes() - entry.minute;
  return delta >= 0 && delta < tickMinutes;
}

/** The dedupe key of a regular run: one per workflow and hour (the two entries of a workflow never share a day of the week). */
export function regularKey(entry, nowMs) {
  return `regular:${entry.workflow}:${new Date(nowMs).toISOString().slice(0, 13)}`;
}

/** What is due at `nowMs` without any request: [{key, kind, workflow, inputs, why}] for the timed events and the regular runs; `windows` = the BoJ events that need the RSS or the fallback. */
export function plan(schedule, nowMs) {
  const due = [], windows = [];
  for (const e of schedule.events || []) {
    if (e.kind === "boj_window") {
      const phase = windowPhase(e, nowMs);
      if (phase !== "idle") windows.push({ event: e, phase });
    } else if (timedDue(e, nowMs)) {
      due.push({ key: e.id, kind: e.kind, workflow: e.dispatch.workflow, inputs: e.dispatch.inputs, why: `${e.kind} ${e.bank} ${e.date}` });
    }
  }
  for (const r of schedule.regular || []) {
    if (regularDue(r, nowMs, schedule.tick_minutes || 5)) due.push({ key: regularKey(r, nowMs), kind: "regular", workflow: r.workflow, inputs: undefined, why: `regular ${r.workflow}` });
  }
  return { due, windows };
}

/** The items of an RSS 2.0 feed: [{title, link, pubMs}] (a Worker has no DOM: the feeds are small and regular). */
export function rssItems(xml) {
  const out = [];
  for (const block of String(xml).split(/<item>/i).slice(1)) {
    const get = (tag) => { const m = block.match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`, "i")); return m ? m[1].replace(/<!\[CDATA\[|\]\]>/g, "").trim() : ""; };
    out.push({ title: get("title"), link: get("link"), pubMs: Date.parse(get("pubDate")) });
  }
  return out;
}

/** The first sign of a BoJ decision: an item published since the window opened whose link holds `match` (the decision documents). */
export function firstDecisionItem(items, event, match) {
  const start = ms(event.window_start);
  return items.filter((i) => i.link.includes(match) && i.pubMs >= start).sort((a, b) => a.pubMs - b.pubMs)[0] || null;
}
