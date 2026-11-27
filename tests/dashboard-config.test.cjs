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

function search(overrides = {}) {
  return { id: 'holiday', from: stay.check_in, to: stay.check_out, ...overrides };
}

function trip(overrides = {}) {
  return { ...stay, adults: 3, codes: ['PROMO'], notify: 'any', added: today, ...overrides };
}

function inheritedConfig() {
  return {
    version: 1,
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
  const original = { version: 1, searches: [search()], tracked: [trip()], extra: { nested: null } };
  const parsed = page.readConfig(original);
  assert.deepEqual(plain(parsed), original);
  assert.deepEqual(JSON.parse(page.encodeConfig(parsed)), original);
  assert.notEqual(parsed, original);
});

test('adding a search retains an idless tracked trip and does not enable a digest', () => {
  const page = dashboard();
  const original = inheritedConfig();
  const parsed = page.readConfig(original);
  const added = page.insertSearch(parsed, page.defaultSearch(parsed, 'added-search', today));
  assert.deepEqual(plain(added.tracked), original.tracked);
  assert.deepEqual(plain(added.defaults), original.defaults);
  assert.equal(Object.hasOwn(added, 'digest'), false);
  assert.equal(Object.hasOwn(added.searches[1], 'codes'), false);
  assert.equal(Object.hasOwn(added.searches[1], 'offers'), false);
  assert.equal(Object.hasOwn(added.searches[1], 'rooms'), false);
});

test('idless tracked trips can be patched, deduplicated, and removed by their derived id', () => {
  const page = dashboard();
  const original = inheritedConfig();
  const parsed = page.readConfig(original);
  const patched = page.setTrackedBelow(page.setTrackedNotify(parsed, tripId, 'drop'), tripId, 1200);
  assert.deepEqual(plain(patched.tracked), [{ ...original.tracked[0], notify: 'drop', below: 1200 }]);
  assert.deepEqual(plain(page.insertTracked(patched, { ...trip(), id: tripId }).tracked), plain(patched.tracked));
  assert.deepEqual(plain(page.removeTracked(patched, tripId).tracked), []);
  assert.deepEqual(original, inheritedConfig());
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

test('an unchanged draft preserves explicit empty lists, nulls, and unknown alert fields', () => {
  const page = dashboard();
  const original = inheritedConfig();
  original.digest = { weekday: null, note: 'disabled' };
  original.searches[0] = search({
    name: null,
    top: null,
    codes: [],
    offers: [],
    rooms: [],
    must_include: [],
    alerts: [{ kind: 'below', amount: 1200, note: { origin: 'manual' } }],
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
  const fresh = search({ adults: 5, top: null, codes: ['LATE'], alerts: [{ kind: 'discounted', note: 'new' }], note: 'new' });
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

test('editing one alert retains unknown fields and untouched alerts', () => {
  const page = dashboard();
  const original = search({ alerts: [
    { kind: 'below', amount: 1200, note: 'threshold' },
    { kind: 'discounted', note: 'discount' },
  ] });
  const draft = page.draftFromSearch(original, {});
  draft.alerts[0].value = '1300';
  const saved = page.searchFromDraft(draft, 'holiday', original);
  assert.deepEqual(plain(saved), { ...original, alerts: [
    { kind: 'below', amount: 1300, note: 'threshold' },
    original.alerts[1],
  ] });
});

test('tracking uses effective adults and promo codes when the search inherits them', () => {
  const page = dashboard();
  const original = inheritedConfig();
  const entry = page.trackedEntry(original.searches[0], stay, today, original.defaults);
  assert.equal(entry.adults, 3);
  assert.deepEqual(plain(entry.codes), ['PROMO']);
  assert.equal(entry.id, tripId);
  assert.ok(page.readConfig({ version: 1, tracked: [entry] }));
  assert.equal(page.isTrackedStay(page.readConfig(original), original.searches[0], stay), true);
});

test('editing displays parser-valid values without clamping unedited fields', () => {
  const page = dashboard();
  const original = search({ nights: 30, adults: 9, top: 100, alerts: [{ kind: 'below', amount: 2000000 }] });
  const draft = page.draftFromSearch(original, {});
  assert.equal(draft.nights, 30);
  assert.equal(draft.adults, 9);
  draft.name = 'Still the same search';
  assert.equal(page.checkDraft(draft, today).valid, true);
  assert.deepEqual(plain(page.searchFromDraft(draft, 'holiday', original)), { ...original, name: 'Still the same search' });
});

test('editing an alert preserves a sibling amount above the editor input limit', () => {
  const page = dashboard();
  const original = search({ alerts: [
    { kind: 'below', amount: 1200 },
    { kind: 'below', amount: 2000000, note: 'valid imported amount' },
  ] });
  const draft = page.draftFromSearch(original, {});
  draft.alerts[0].value = '1300';
  assert.equal(page.checkDraft(draft, today).valid, true);
  const saved = page.searchFromDraft(draft, 'holiday', original);
  assert.deepEqual(plain(saved.alerts), [{ kind: 'below', amount: 1300 }, original.alerts[1]]);
  assert.ok(page.readConfig({ version: 1, searches: [saved] }));
});

test('known malformed structures are rejected instead of discarded or defaulted', () => {
  const page = dashboard();
  const valid = { version: 1, searches: [search()], tracked: [trip()] };
  const invalid = [
    { ...valid, version: 2 },
    { ...valid, defaults: null },
    { ...valid, defaults: [] },
    { ...valid, defaults: { codes: null } },
    { ...valid, defaults: { nights: true } },
    { ...valid, digest: [] },
    { ...valid, digest: { weekday: 'someday' } },
    { ...valid, searches: {} },
    { ...valid, searches: [null] },
    { ...valid, searches: [search({ id: undefined })] },
    { ...valid, searches: [search({ from: '2026-13-01' })] },
    { ...valid, searches: [search({ to: stay.check_in })] },
    { ...valid, searches: [search({ offers: [''] })] },
    { ...valid, searches: [search({ must_include: ['someday'] })] },
    { ...valid, searches: [search({ alerts: [{ kind: 'future-rule' }] })] },
    { ...valid, searches: [search({ alerts: [{ kind: 'below', amount: '1200' }] })] },
    { ...valid, searches: [search(), search()] },
    { ...valid, tracked: null },
    { ...valid, tracked: [false] },
    { ...valid, tracked: [trip({ id: 'wrong' })] },
    { ...valid, tracked: [trip({ adults: undefined })] },
    { ...valid, tracked: [trip({ notify: 'never' })] },
    { ...valid, tracked: [trip(), trip({ id: tripId })] },
  ];
  for (const raw of invalid) {
    assert.equal(page.readConfig(raw), null, JSON.stringify(raw));
  }
});

test('serialization rejects a malformed edit before it can be written', () => {
  const page = dashboard();
  assert.throws(() => page.encodeConfig({ version: 1, tracked: [trip({ codes: null })] }), /config/i);
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
  const { page, requests } = savedDashboard({ version: 1, searches: [search()], tracked: [null] });
  await assert.rejects(page.commitChange((fresh) => page.removeSearch(fresh, 'holiday'), 'Delete search'), /config/i);
  assert.equal(requests.length, 1);
});

test('the actual GitHub save performs no PUT when a mutation returns malformed config', async () => {
  const { page, requests } = savedDashboard({ version: 1, searches: [search()] });
  await assert.rejects(page.commitChange((fresh) => ({ ...fresh, tracked: [null] }), 'Bad edit'), /config/i);
  assert.equal(requests.length, 1);
});
