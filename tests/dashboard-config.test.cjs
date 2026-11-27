const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../docs/index.html'), 'utf8');
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map((match) => match[1]);
scripts.forEach((source) => new vm.Script(source));

function dashboard(extra = {}) {
  const context = vm.createContext({ Intl, Date, TextEncoder, TextDecoder, btoa, atob, ...extra });
  vm.runInContext(scripts[0], context);
  for (const name of ['ghFileUrl', 'ghHeaders', 'ghReadConfig', 'commitChange']) {
    const source = scripts[1].match(new RegExp(`^  (?:async )?function ${name}\\([^)]*\\) \\{[\\s\\S]*?^  \\}`, 'm'));
    assert.ok(source, `shipped ${name} function is available`);
    vm.runInContext(source[0], context);
  }
  return context;
}

const plain = (value) => JSON.parse(JSON.stringify(value));
const stay = { check_in: '2026-12-04', check_out: '2026-12-06' };
const today = '2026-11-11';
const tripId = '2026-12-04_2026-12-06_a3_cPROMO';
const NNBSP = ' ';
const NBSP = ' ';

function search(overrides = {}) {
  return { id: 'holiday', from: stay.check_in, to: stay.check_out, ...overrides };
}

function trip(overrides = {}) {
  return { ...stay, adults: 3, codes: ['PROMO'], added: today, ...overrides };
}

function inheritedConfig() {
  return {
    version: 2,
    defaults: {
      adults: 3,
      nights: 2,
      must_include: ['sat'],
      top: 5,
      codes: ['PROMO'],
      offers: ['Breakfast'],
      rooms: ['Suite'],
      note: { source: 'manual' },
    },
    searches: [search({ note: 'keep this' })],
    tracked: [trip({ color: 'blue' })],
    extra: { nested: ['keep', null] },
  };
}

test('reading and encoding a valid config preserves its raw document', () => {
  const page = dashboard();
  const original = {
    version: 2,
    searches: [search({ alert: { under: 1200, again_cheaper_by: 50, require: { recent_low: true, discounted: false, drop_pct: null }, note: 'kept' } })],
    tracked: [trip()],
    extra: { nested: null },
  };
  const parsed = page.readConfig(original);
  assert.deepEqual(plain(parsed), original);
  assert.deepEqual(JSON.parse(page.encodeConfig(parsed)), original);
  assert.notEqual(parsed, original);
});

test('version 1 configs and searches with an alerts list are rejected', () => {
  const page = dashboard();
  assert.equal(page.readConfig({ version: 1, searches: [search()], tracked: [] }), null);
  assert.equal(page.readConfig({ version: 2, searches: [search({ alerts: [{ kind: 'below', amount: 1200 }] })] }), null);
  assert.equal(page.readConfig({ version: 2, searches: [search({ alerts: [] })] }), null);
  assert.ok(page.readConfig({ version: 2, searches: [search({ alert: { under: 1200 } })] }));
});

test('adding a search retains an idless tracked trip and starts with no alert', () => {
  const page = dashboard();
  const original = inheritedConfig();
  const parsed = page.readConfig(original);
  const added = page.insertSearch(parsed, page.defaultSearch(parsed, 'added-search', today));
  assert.deepEqual(plain(added.tracked), original.tracked);
  assert.deepEqual(plain(added.defaults), original.defaults);
  assert.equal(Object.hasOwn(added.searches[1], 'alert'), false);
  assert.equal(Object.hasOwn(added.searches[1], 'codes'), false);
  assert.equal(Object.hasOwn(added.searches[1], 'offers'), false);
  assert.equal(Object.hasOwn(added.searches[1], 'rooms'), false);
});

test('idless tracked trips can be deduplicated and removed by their derived id', () => {
  const page = dashboard();
  const original = inheritedConfig();
  const parsed = page.readConfig(original);
  assert.deepEqual(plain(page.insertTracked(parsed, { ...trip(), id: tripId }).tracked), plain(parsed.tracked));
  assert.deepEqual(plain(page.removeTracked(parsed, tripId).tracked), []);
  assert.deepEqual(original, inheritedConfig());
});

test('a new tracked entry carries no notify mode and no alert-below field', () => {
  const page = dashboard();
  const original = inheritedConfig();
  const entry = page.trackedEntry(original.searches[0], stay, today, original.defaults);
  assert.deepEqual(Object.keys(entry), ['id', 'check_in', 'check_out', 'adults', 'codes', 'added']);
  assert.ok(page.readConfig({ version: 2, tracked: [entry] }));
});

test('renaming a search preserves every unedited omission and inherited filter', () => {
  const page = dashboard();
  const original = inheritedConfig();
  const parsed = page.readConfig(original);
  const draft = page.draftFromSearch(parsed.searches[0], parsed.defaults);
  draft.name = 'Renamed only';
  const savedSearch = page.searchFromDraft(draft, 'holiday', parsed.searches[0]);
  const saved = page.replaceSearch(parsed, savedSearch);
  assert.deepEqual(plain(saved), { ...original, searches: [{ ...original.searches[0], name: 'Renamed only' }] });
});

test('an unchanged draft keeps the raw alert, unknown alert keys included', () => {
  const page = dashboard();
  const original = inheritedConfig();
  original.searches[0] = search({
    name: null,
    top: null,
    codes: [],
    offers: [],
    rooms: [],
    must_include: [],
    alert: { under: 1200, note: { origin: 'manual' }, require: { recent_low: true, note: 'kept' } },
    unknown: null,
  });
  const parsed = page.readConfig(original);
  const draft = page.draftFromSearch(parsed.searches[0], parsed.defaults);
  assert.equal(draft.topAll, true);
  assert.deepEqual(plain(draft.mustInclude), []);
  const saved = page.replaceSearch(parsed, page.searchFromDraft(draft, 'holiday', parsed.searches[0]));
  assert.deepEqual(plain(saved), original);
});

test('the fresh server document wins for fields the user did not edit', () => {
  const page = dashboard();
  const opened = inheritedConfig();
  const draft = page.draftFromSearch(opened.searches[0], opened.defaults);
  draft.name = 'My edit';
  const fresh = search({ adults: 5, top: null, codes: ['LATE'], alert: { under: 900, note: 'new' }, note: 'new' });
  const saved = page.searchFromDraft(draft, 'holiday', fresh);
  assert.deepEqual(plain(saved), { ...fresh, name: 'My edit' });
});

test('editing an inherited field writes only that override', () => {
  const page = dashboard();
  const original = inheritedConfig();
  const draft = page.draftFromSearch(original.searches[0], original.defaults);
  draft.adults = 4;
  draft.topAll = true;
  const saved = page.searchFromDraft(draft, 'holiday', original.searches[0]);
  assert.deepEqual(plain(saved), { ...original.searches[0], adults: 4, top: null });
});

test('editing only the price writes alert.under and keeps everything else raw', () => {
  const page = dashboard();
  const original = search({ alert: { under: 1200, note: 'threshold', require: { discounted: true, note: 'kept' } } });
  const draft = page.draftFromSearch(original, {});
  draft.alert.under = '1300';
  assert.equal(page.checkDraft(draft, today).valid, true);
  const saved = page.searchFromDraft(draft, 'holiday', original);
  assert.deepEqual(plain(saved), { ...original, alert: { under: 1300, note: 'threshold', require: { discounted: true, note: 'kept' } } });
  assert.equal(Object.hasOwn(saved.alert, 'again_cheaper_by'), false);
  assert.ok(page.readConfig({ version: 2, searches: [saved] }));
});

test('clearing the price writes no alert', () => {
  const page = dashboard();
  const original = search({ alert: { under: 1200, again_cheaper_by: 50 } });
  const draft = page.draftFromSearch(original, {});
  draft.alert.under = '';
  assert.equal(page.checkDraft(draft, today).valid, true);
  const saved = page.searchFromDraft(draft, 'holiday', original);
  assert.equal(Object.hasOwn(saved, 'alert'), false);
  assert.ok(page.readConfig({ version: 2, searches: [saved] }));
});

test('toggling a filter writes it and leaves the price alone', () => {
  const page = dashboard();
  const original = search({ alert: { under: 1200 } });
  const draft = page.draftFromSearch(original, {});
  draft.alert.discounted = true;
  assert.deepEqual(plain(page.searchFromDraft(draft, 'holiday', original).alert), { under: 1200, require: { discounted: true } });
});

test('toggling the drop filter writes its percent', () => {
  const page = dashboard();
  const original = search({ alert: { under: 1200 } });
  const draft = page.draftFromSearch(original, {});
  draft.alert.dropOn = true;
  draft.alert.dropPct = '15';
  assert.equal(page.checkDraft(draft, today).valid, true);
  assert.deepEqual(plain(page.searchFromDraft(draft, 'holiday', original).alert), { under: 1200, require: { drop_pct: 15 } });
});

test('a new alert writes every field, with the default again-limit of 50', () => {
  const page = dashboard();
  const original = search();
  const draft = page.draftFromSearch(original, {});
  assert.deepEqual(plain(draft.alert), { under: '', again: '50', recentLow: false, discounted: false, dropOn: false, dropPct: '' });
  draft.alert.under = '900';
  assert.equal(page.checkDraft(draft, today).valid, true);
  const saved = page.searchFromDraft(draft, 'holiday', original);
  assert.deepEqual(plain(saved.alert), {
    under: 900,
    again_cheaper_by: 50,
    require: { recent_low: false, discounted: false, drop_pct: null },
  });
});

test('an again-limit of 0 is allowed and is written when edited', () => {
  const page = dashboard();
  const original = search({ alert: { under: 1200, again_cheaper_by: 50 } });
  const draft = page.draftFromSearch(original, {});
  draft.alert.again = '0';
  assert.equal(page.checkDraft(draft, today).valid, true);
  assert.equal(page.searchFromDraft(draft, 'holiday', original).alert.again_cheaper_by, 0);
});

test('editing displays parser-valid values without clamping unedited fields', () => {
  const page = dashboard();
  const original = search({ nights: 30, adults: 9, top: 100, alert: { under: 2000000 } });
  const draft = page.draftFromSearch(original, {});
  assert.equal(draft.nights, 30);
  assert.equal(draft.adults, 9);
  draft.name = 'Still the same search';
  assert.equal(page.checkDraft(draft, today).valid, true);
  assert.deepEqual(plain(page.searchFromDraft(draft, 'holiday', original)), { ...original, name: 'Still the same search' });
});

test('editing the price keeps a stored again-limit above the editor input limit', () => {
  const page = dashboard();
  const original = search({ alert: { under: 1200, again_cheaper_by: 2000000 } });
  const draft = page.draftFromSearch(original, {});
  draft.alert.under = '1300';
  assert.equal(page.checkDraft(draft, today).valid, true);
  assert.deepEqual(plain(page.searchFromDraft(draft, 'holiday', original).alert), { under: 1300, again_cheaper_by: 2000000 });
});

test('alert fields are checked only while a price is set and only when changed', () => {
  const page = dashboard();
  const fresh = page.draftFromSearch(search(), {});
  fresh.alert.under = '0';
  assert.equal(page.checkDraft(fresh, today).errors.alertUnder, 'Whole złoty above 0.');
  fresh.alert.under = '12.5';
  assert.equal(page.checkDraft(fresh, today).valid, false);
  fresh.alert.under = '1200';
  fresh.alert.again = '-1';
  assert.equal(page.checkDraft(fresh, today).errors.alertAgain, 'Whole złoty, 0 or more.');
  fresh.alert.again = '50';
  fresh.alert.dropOn = true;
  fresh.alert.dropPct = '101';
  assert.equal(page.checkDraft(fresh, today).errors.alertDrop, 'Whole percent, 1 to 100.');
  fresh.alert.dropPct = '100';
  assert.equal(page.checkDraft(fresh, today).valid, true);
  fresh.alert.under = '';
  fresh.alert.again = 'abc';
  fresh.alert.dropPct = '';
  assert.equal(page.checkDraft(fresh, today).valid, true);
});

test('effective alerts fill the defaults and a missing alert stays null', () => {
  const page = dashboard();
  assert.equal(page.effectiveAlert(null), null);
  assert.equal(page.effectiveAlert(undefined), null);
  assert.deepEqual(plain(page.effectiveAlert({ under: 900 })), {
    under: 900,
    again_cheaper_by: 0,
    require: { recent_low: false, discounted: false, drop_pct: null },
  });
});

test('the card summary reads the alert in plain words', () => {
  const page = dashboard();
  assert.equal(page.alertSummary(null), 'No email alerts');
  assert.equal(
    page.alertSummary({ under: 1200, again_cheaper_by: 50, require: { recent_low: false, discounted: false, drop_pct: null } }),
    `Emails stays under 1${NNBSP}200${NBSP}zł, again if 50${NBSP}zł cheaper`,
  );
  assert.equal(
    page.alertSummary({ under: 1200, again_cheaper_by: 0, require: { recent_low: true, discounted: true, drop_pct: 10 } }),
    `Emails stays under 1${NNBSP}200${NBSP}zł, again at any lower price, only if 30-day low, discounted and dropped at least 10%`,
  );
  assert.equal(
    page.alertSummary({ under: 900, require: { recent_low: false, discounted: false, drop_pct: 5 } }),
    `Emails stays under 900${NBSP}zł, again at any lower price, only if dropped at least 5%`,
  );
});

test('a stay carries the badge from alert_match, or from the price when no match is stored', () => {
  const page = dashboard();
  assert.equal(page.isUnder({ price: 99300, alert_match: false }, 1200), false);
  assert.equal(page.isUnder({ price: 99300 }, 1200), true);
  assert.equal(page.isUnder({ price: 120000 }, 1200), false);
  assert.equal(page.isUnder({ price: 99300, alert_match: true }, null), true);
  assert.equal(page.isUnder({ price: null, sold_out: true, alert_match: true }, 1200), false);
  assert.ok(page.stayFlags({ price: 99300, alert_match: true }, 1200).some((flag) => flag.kind === 'under'));
  assert.equal(page.stayFlags({ price: 99300 }).some((flag) => flag.kind === 'under'), false);
});

test('the summary compares the cheapest stay with the alert limit in grosze', () => {
  const page = dashboard();
  const summary = page.summarize({
    alert: { under: 1200, again_cheaper_by: 0, require: { recent_low: false, discounted: false, drop_pct: null } },
    stays: [
      { check_in: '2026-12-04', check_out: '2026-12-06', price: 99300, per_night: 49650, sold_out: false, alert_match: true },
      { check_in: '2026-12-11', check_out: '2026-12-13', price: 130000, per_night: 65000, sold_out: false, alert_match: false },
    ],
  });
  assert.equal(summary.threshold, 120000);
  assert.equal(summary.underCount, 1);
  assert.ok(Math.abs(summary.vsAlertPct - ((99300 - 120000) / 120000 * 100)) < 1e-9);
  assert.equal(page.summarize({ stays: [] }).threshold, null);
});

test('known malformed structures are rejected instead of discarded or defaulted', () => {
  const page = dashboard();
  const valid = { version: 2, searches: [search()], tracked: [trip()] };
  const invalid = [
    { ...valid, version: 1 },
    { ...valid, version: 3 },
    { ...valid, defaults: null },
    { ...valid, defaults: [] },
    { ...valid, defaults: { codes: null } },
    { ...valid, defaults: { nights: true } },
    { ...valid, searches: {} },
    { ...valid, searches: [null] },
    { ...valid, searches: [search({ id: undefined })] },
    { ...valid, searches: [search({ from: '2026-13-01' })] },
    { ...valid, searches: [search({ to: stay.check_in })] },
    { ...valid, searches: [search({ offers: [''] })] },
    { ...valid, searches: [search({ must_include: ['someday'] })] },
    { ...valid, searches: [search({ alerts: [] })] },
    { ...valid, searches: [search({ alerts: [{ kind: 'below', amount: 1200 }] })] },
    { ...valid, searches: [search({ alert: 'yes' })] },
    { ...valid, searches: [search({ alert: [] })] },
    { ...valid, searches: [search({ alert: {} })] },
    { ...valid, searches: [search({ alert: { under: '1200' } })] },
    { ...valid, searches: [search({ alert: { under: 0 } })] },
    { ...valid, searches: [search({ alert: { under: 1.5 } })] },
    { ...valid, searches: [search({ alert: { under: 1200, again_cheaper_by: -1 } })] },
    { ...valid, searches: [search({ alert: { under: 1200, again_cheaper_by: null } })] },
    { ...valid, searches: [search({ alert: { under: 1200, require: [] } })] },
    { ...valid, searches: [search({ alert: { under: 1200, require: null } })] },
    { ...valid, searches: [search({ alert: { under: 1200, require: { recent_low: 'yes' } } })] },
    { ...valid, searches: [search({ alert: { under: 1200, require: { discounted: 1 } } })] },
    { ...valid, searches: [search({ alert: { under: 1200, require: { drop_pct: 101 } } })] },
    { ...valid, searches: [search({ alert: { under: 1200, require: { drop_pct: 0 } } })] },
    { ...valid, searches: [search({ alert: { under: 1200, require: { drop_pct: 'ten' } } })] },
    { ...valid, searches: [search(), search()] },
    { ...valid, tracked: null },
    { ...valid, tracked: [false] },
    { ...valid, tracked: [trip({ id: 'wrong' })] },
    { ...valid, tracked: [trip({ adults: undefined })] },
    { ...valid, tracked: [trip(), trip({ id: tripId })] },
  ];
  for (const raw of invalid) {
    assert.equal(page.readConfig(raw), null, JSON.stringify(raw));
  }
});

test('leftover tracked notify and below keys are kept, not rejected', () => {
  const page = dashboard();
  const raw = { version: 2, searches: [], tracked: [trip({ notify: 'drop', below: 950 })] };
  assert.deepEqual(plain(page.readConfig(raw)), raw);
});

test('serialization rejects a malformed edit before it can be written', () => {
  const page = dashboard();
  assert.throws(() => page.encodeConfig({ version: 2, tracked: [trip({ codes: null })] }), /config/i);
});

function savedDashboard(original) {
  const requests = [];
  const page = dashboard({
    sampleName: null,
    state: { repo: 'owner/watch', token: 'test-token', config: original },
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      if (options.method === 'PUT') return { ok: true, status: 200, json: async () => ({ content: { sha: 'saved-sha' } }) };
      return { ok: true, status: 200, json: async () => ({ sha: 'fresh-sha', content: btoa(JSON.stringify(original)) }) };
    },
  });
  return { page, requests };
}

test('the actual GitHub save writes the fresh raw document with only the edited name', async () => {
  const original = inheritedConfig();
  const { page, requests } = savedDashboard(original);
  const draft = page.draftFromSearch(original.searches[0], original.defaults);
  draft.name = 'Saved name';
  const result = await page.commitChange((fresh) => page.replaceSearch(fresh, page.searchFromDraft(draft, 'holiday', fresh.searches[0])), 'Edit search');
  assert.equal(result.revision, 'saved-sha');
  assert.equal(requests.length, 2);
  const body = JSON.parse(requests[1].body);
  assert.equal(body.sha, 'fresh-sha');
  assert.deepEqual(JSON.parse(atob(body.content)), { ...original, searches: [{ ...original.searches[0], name: 'Saved name' }] });
});

test('the actual GitHub save performs no PUT when the server config is malformed', async () => {
  const { page, requests } = savedDashboard({ version: 2, searches: [search()], tracked: [null] });
  await assert.rejects(page.commitChange((fresh) => page.removeSearch(fresh, 'holiday'), 'Delete search'), /config/i);
  assert.equal(requests.length, 1);
});

test('the actual GitHub save performs no PUT when a mutation returns malformed config', async () => {
  const { page, requests } = savedDashboard({ version: 2, searches: [search()] });
  await assert.rejects(page.commitChange((fresh) => ({ ...fresh, tracked: [null] }), 'Bad edit'), /config/i);
  assert.equal(requests.length, 1);
});
