// Run the shipped inline polling code against a minimal DOM and deterministic timers.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../templates/limits_dashboard.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function harness({active = 1, errors = 0, failure = false, redirected = false, pending = false, sequence = []} = {}) {
  const timers = new Map();
  let id = 0, requests = 0, replacements = 0;
  const file = {files: [{name: 'selected.xlsx'}]};
  const message = {textContent: ''};
  const button = {disabled: false, addEventListener(_, fn) {this.click = fn;}};
  const row = (count, errs, text = '', checkpoint) => ({
    textContent: text,
    dataset: {active: String(count), errors: String(errs), checkpoint},
    replaceWith(next) {nodes['limits-files'] = next; replacements++;}
  });
  const nodes = {'limits-files': row(1, 0), 'limits-progress': message, 'limits-check': button, file};
  const context = {
    document: {getElementById: id => nodes[id]},
    window: {location: {href: 'http://test/limit-notifications'}},
    AbortController,
    setTimeout(fn, delay) {timers.set(++id, {fn, delay}); return id;},
    clearTimeout(id) {timers.delete(id);},
    fetch: async (_, options) => {
      requests++;
      if (pending) return new Promise((resolve, reject) =>
        options.signal.addEventListener('abort', () => reject(new Error('timeout'))));
      if (failure) throw new Error('offline');
      return {ok: true, redirected, text: async () => 'response'};
    },
    DOMParser: class {
      parseFromString() {
        const next = sequence.shift() || {active, errors};
        return {getElementById: () => row(next.active, next.errors || 0, next.text, next.checkpoint)};
      }
    }
  };
  vm.runInNewContext(script, context);
  async function tick(delay = 5000) {
    const entry = [...timers].find(([, timer]) => timer.delay === delay);
    if (!entry) return false;
    timers.delete(entry[0]);
    const result = entry[1].fn();
    if (!pending) await result;
    for (let i = 0; i < 8; i++) await Promise.resolve();
    return true;
  }
  return {tick, nodes, file, message, button, timers,
          requests: () => requests, replacements: () => replacements};
}

test('finished status updates only the table; selected file survives', async () => {
  const h = harness({active: 0});
  await h.tick();
  assert.equal(h.replacements(), 1);
  assert.equal(h.nodes.file, h.file);
  assert.equal(h.file.files[0].name, 'selected.xlsx');
  assert.equal(h.timers.size, 0);
  assert.equal(h.button.disabled, false);
});
test('parsing -> transient review -> accepted keeps polling until ready', async () => {
  const h = harness({sequence: [
    {active: 1, text: 'Обработка'},
    {active: 1, text: 'Обработка'}, // committed review, active worker
    {active: 0, text: 'Готово'}
  ]});
  for (let i = 0; i < 2; i++) {
    assert.equal(await h.tick(), true);
    assert.equal(h.nodes['limits-files'].textContent, 'Обработка');
    assert.equal(h.timers.size, 1);
  }
  await h.tick();
  assert.equal(h.nodes['limits-files'].textContent, 'Готово');
  assert.equal(h.requests(), 3);
  assert.equal(h.timers.size, 0);
  assert.equal(h.nodes.file, h.file);
});
test('genuine unresolved review stops and preserves resolution action', async () => {
  const h = harness({sequence: [
    {active: 0, text: 'Требуется проверка данных — Исправить сопоставление'}
  ]});
  await h.tick();
  assert.match(h.nodes['limits-files'].textContent, /Исправить сопоставление/);
  assert.equal(h.timers.size, 0);
  assert.equal(await h.tick(), false);
});
test('unchanged parsing stops after bounded attempts and supports explicit retry', async () => {
  const h = harness();
  for (let i = 0; i < 30; i++) await h.tick();
  assert.equal(h.requests(), 24);
  assert.equal(h.timers.size, 0);
  assert.match(h.message.textContent, /приостановлена/);
  h.button.click();
  for (let i = 0; i < 8; i++) await Promise.resolve();
  assert.equal(h.requests(), 25);
  assert.equal(h.nodes.file, h.file);
});
test('terminal error, network failure, and login redirect stop polling', async () => {
  for (const options of [{errors: 1, active: 0}, {failure: true}, {redirected: true}]) {
    const h = harness(options);
    await h.tick();
    assert.equal(h.timers.size, 0);
    assert.equal(h.button.disabled, false);
  }
});
test('measured checkpoints extend polling, but a subsequent stall remains bounded', async () => {
  const h = harness({sequence: Array.from({length: 35}, (_, i) => ({
    active: 1, errors: 1, checkpoint: String(i)
  }))});
  for (let i = 0; i < 35; i++) assert.equal(await h.tick(), true);
  assert.equal(h.requests(), 35);
  assert.equal(h.timers.size, 1);
  for (let i = 0; i < 30; i++) await h.tick();
  assert.equal(h.timers.size, 0);
  assert.equal(h.nodes.file, h.file);
});
test('hung HTTP request is aborted and does not loop', async () => {
  const h = harness({pending: true});
  await h.tick();
  await h.tick(12000);
  assert.equal(h.timers.size, 0);
  assert.match(h.message.textContent, /Не удалось/);
  assert.equal(h.button.disabled, false);
});