const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../docs/index.html'), 'utf8');
const pure = html.match(/<script id="pure">([\s\S]*?)<\/script>/)[1];
const dashboard = vm.createContext({});
vm.runInContext(pure, dashboard);

test('an older configuration scan cannot complete a pending edit', () => {
  assert.equal(dashboard.pollDecision(1000, 21000, 'saved-config', 'previous-config'), 'waiting');
});

test('only the saved configuration revision completes its pending edit', () => {
  assert.equal(dashboard.pollDecision(1000, 21000, 'saved-config', 'saved-config'), 'done');
});

test('a legacy scan without a config revision does not complete an edit', () => {
  assert.equal(dashboard.pollDecision(1000, 21000, 'saved-config', null), 'waiting');
});

test('a missing save receipt cannot complete against an unrelated scan', () => {
  assert.equal(dashboard.pollDecision(1000, 21000, null, 'previous-config'), 'waiting');
});

test('a pending edit times out if its revision never appears', () => {
  assert.equal(dashboard.pollDecision(1000, 601000, 'saved-config', 'previous-config'), 'gave-up');
});

test('the save and polling flow waits for the saved config before publishing results', async () => {
  let nextScan = { scanned_at: '2026-11-11T12:00:00Z', config_revision: 'previous-config', searches: [] };
  let scheduled = 0;
  let historyRefreshes = 0;
  const page = vm.createContext({
    sampleName: null,
    state: { busy: false, held: {}, latest: { scanned_at: '2026-11-11T09:00:00Z' } },
    requireConnected: () => true,
    renderApp: () => {},
    commitChange: async () => ({ config: { version: 1 }, changed: true, revision: 'saved-config' }),
    schedulePoll: () => { scheduled += 1; },
    readCopy: async () => ({ missing: false, body: nextScan }),
    refreshHistory: async () => { historyRefreshes += 1; },
  });
  vm.runInContext(pure, page);
  for (const name of ['runEdit', 'startPending', 'pollTick']) {
    const source = html.match(new RegExp(`^  (?:async )?function ${name}\\([^)]*\\) \\{[\\s\\S]*?^  \\}`, 'm'));
    assert.ok(source, `shipped ${name} function is available`);
    vm.runInContext(source[0], page);
  }

  await page.runEdit('search|holiday', () => {}, 'Edit search');
  assert.equal(page.state.pending.revision, 'saved-config');
  await page.pollTick();
  assert.equal(page.state.latest.scanned_at, '2026-11-11T09:00:00Z');
  assert.equal(page.state.pending.revision, 'saved-config');
  assert.equal(scheduled, 2);

  nextScan = { ...nextScan, config_revision: 'saved-config' };
  await page.pollTick();
  assert.equal(page.state.pending, null);
  assert.equal(page.state.latest.config_revision, 'saved-config');
  assert.equal(historyRefreshes, 1);
});
