"use strict";
const assert = require("node:assert/strict");
const { validDate, shiftDay, dateRange, summarize, summarizeIntraday, seriesFor, csvFor } = require("../dist/app.js");
assert.equal(validDate("2026-02-29"), false);
assert.equal(validDate("2024-02-29"), true);
assert.equal(shiftDay("2026-09-01", -1), "2026-08-31");
assert.deepEqual(dateRange("2026-08-31", "2026-09-02"), ["2026-08-31", "2026-09-01", "2026-09-02"]);
assert.throws(() => dateRange("2026-09-02", "2026-09-01"));
const base = { inputTokens: 10, outputTokens: 20, cacheReadTokens: 30, cacheCreationTokens: 40, tokens: 100, models: [{ name: "test", tokens: 100 }] };
const rows = [{ ...base, date: "2026-09-01", agent: "codex", cost: 1 }, { ...base, date: "2026-09-03", agent: "claude", cost: 2 }];
const result = summarize(rows, "2026-09-01", "2026-09-03");
assert.equal(result.totals.codex.tokens + result.totals.claude.tokens, 200);
assert.equal(Object.values(result.mix).reduce((sum, n) => sum + n, 0), 200);
assert.equal(result.models.reduce((sum, row) => sum + row.tokens, 0), 200);
assert.equal(result.days[1].codex.cost, 0);
assert.equal(seriesFor(result.days, "cost", true)[2].codex, 1);
assert.equal(seriesFor(result.days, "cost", true)[2].claude, 2);
assert.equal(seriesFor(result.days, "tokens", false)[1].claude, 0);
assert.equal(summarize(rows, "2026-09-01", "2026-09-01").totals.claude.cost, 0);
assert.equal(csvFor(result.days).split("\r\n").length, 4);
assert.ok(csvFor(result.days).includes("2026-09-03,0.000000,2.000000,2.000000,0,100,100"));
const intraday = summarizeIntraday({
  start: "2026-09-18T16:07:42+00:00", end: "2026-09-19T16:07:42+00:00", bucketSeconds: 900,
  rows: [
    { ...base, agent: "codex", cost: 1, start: "2026-09-18T16:07:42.000+00:00" },
    { ...base, agent: "claude", cost: 2, start: "2026-09-19T15:45:00.000+00:00" },
    { ...base, agent: "codex", cost: 3, start: "2026-09-19T16:00:00.000+00:00" }
  ]
});
assert.equal(intraday.days.length, 97); // Partial edges and 95 full quarter-hours.
assert.equal(intraday.days[0].date, "2026-09-18T16:07:42.000Z");
assert.equal(intraday.days[0].end, "2026-09-18T16:15:00.000Z");
assert.equal(intraday.days.at(-1).end, "2026-09-19T16:07:42.000Z");
assert.equal(intraday.totals.codex.cost, 4);
assert.equal(intraday.totals.claude.tokens, 100);
assert.equal(Object.values(intraday.mix).reduce((a, b) => a + b, 0), 300);
assert.equal(intraday.models.reduce((a, b) => a + b.tokens, 0), 300);
assert.equal(intraday.days[1].codex.tokens, 0);
assert.equal(seriesFor(intraday.days, "cost", true).at(-1).codex, 4);
assert.equal(seriesFor(intraday.days, "tokens", true).at(-1).claude, 100);
assert.equal(csvFor(intraday.days).split("\r\n").length, 98);
assert.ok(csvFor(intraday.days).startsWith("start_utc,end_utc,"));
assert.throws(() => summarizeIntraday(undefined), /尚未就绪/);
assert.throws(() => summarizeIntraday({ start: "2026-09-19", end: "2026-09-19", rows: [], bucketSeconds: 900 }), /不完整/);
assert.equal(summarizeIntraday({ start: "2026-09-18T16:00:00Z", end: "2026-09-19T16:00:00Z", rows: [], bucketSeconds: 900 }).days.length, 96);
console.log("Frontend data tests passed (dates, filtering, totals, components, cumulative series, CSV).");
