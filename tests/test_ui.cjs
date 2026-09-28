// Run with: node --test tests/test_ui.cjs (no browser or dependencies required).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');

function setup() {
  const nodes = new Map();
  class Element {
    constructor() { this.children = []; this.dataset = {}; this.handlers = {}; this.value = ''; }
    set innerHTML(value) { this.children = []; }
    append(...nodes) { this.children.push(...nodes); }
    setAttribute(key, value) { this[key] = value; }
    removeAttribute(key) { delete this[key]; }
    addEventListener(key, fn) { this.handlers[key] = fn; }
    focus() { this.focused = true; }
    querySelector() { return this.children.find(n => !n.disabled && n.dataset.act); }
  }
  const get = (id) => {
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  };
  const context = vm.createContext({
    document: { querySelector: get, createElement: () => new Element(), documentElement: new Element() },
    localStorage: {}, matchMedia: () => ({ matches: false }), location: { hash: '' },
    window: { addEventListener() {} }, URL, FormData, setTimeout,
    fetch: async () => ({ json: async () => [] }),
  });
  vm.runInContext(readFileSync('src/audio_md/static/app.js', 'utf8'), context);
  return { context, get, run: code => vm.runInContext(code, context) };
}

test('YouTube input matches supported forms and rejects other links', () => {
  const { run } = setup();
  for (const value of ['OKKSUpDTfXQ', 'youtu.be/OKKSUpDTfXQ',
    'https://www.youtube.com/watch?v=OKKSUpDTfXQ&list=x',
    'https://m.youtube.com/shorts/OKKSUpDTfXQ',
    'https://youtube.com/live/OKKSUpDTfXQ', 'https://youtube.com/embed/OKKSUpDTfXQ']) {
    assert.equal(run(`youtubeId(${JSON.stringify(value)})`), 'OKKSUpDTfXQ');
  }
  for (const value of ['https://example.com', 'https://youtube.com/playlist?list=x', 'invalid']) {
    assert.equal(run(`youtubeId(${JSON.stringify(value)})`), null);
  }
});

test('invalid batch keeps draft and adds no links; accepted files survive rejected ones', () => {
  const { run, get } = setup();
  run('addFiles([{name:"voice.ogg",type:"audio/ogg",size:12},{name:"notes.txt",type:"text/plain",size:2}])');
  assert.equal(run('files.length'), 1);
  assert.match(get('#file-error').textContent, /notes.txt/);
  get('#url-input').value = 'OKKSUpDTfXQ\n\nhttps://example.com';
  assert.equal(run('submitUrl()'), false);
  assert.equal(run('files.length'), 1);
  assert.match(get('#url-error').textContent, /3/);
  assert.ok(get('#url-input').value);
});

test('pending links join ordered multipart; upload locks editing and failure preserves list', async () => {
  const { run, get, context } = setup();
  const file = new Blob(['voice'], { type: 'audio/ogg' });
  file.name = 'voice.ogg';
  context.sampleFile = file;
  run('addFiles([sampleFile])');
  get('#url-input').value = 'OKKSUpDTfXQ';
  let resolve;
  let submitted;
  context.fetch = async (url, options) => {
    submitted = options.body;
    return await new Promise(r => { resolve = r; });
  };
  const pending = get('#submit').handlers.click();
  assert.deepEqual(JSON.parse(submitted.get('items')), [
    { type: 'file', file_index: 0 }, { type: 'youtube', url: 'OKKSUpDTfXQ' },
  ]);
  assert.equal(submitted.getAll('files').length, 1);
  assert.equal(get('#select-files').disabled, true);
  run('addFiles([sampleFile])');
  assert.equal(run('files.length'), 2);
  resolve({ ok: false, status: 413, json: async () => { throw Error('HTML'); } });
  await pending;
  assert.match(get('#send-error').textContent, /1 GiB/);
  assert.equal(run('files.length'), 2);
  assert.equal(get('#select-files').disabled, false);
  assert.equal(get('#submit').disabled, false);
  context.fetch = async () => ({ ok: true, json: async () => ({ id: 'abc123' }) });
  await get('#submit').handlers.click();
  assert.equal(context.location.hash, 'g/abc123');
  assert.equal(run('files.length'), 2);
});

test('size limit, reorder, removal and clear update the queue', () => {
  const { run, get } = setup();
  assert.equal(get('#submit').disabled, true);
  run('addFiles([{name:"big.mp4",type:"video/mp4",size:1073741825},{name:"voice.ogg",type:"audio/ogg",size:5}])');
  assert.equal(get('#submit').disabled, true);
  const action = (i, act) => get('#file-list').handlers.click({ target: {
    closest: () => ({ dataset: { i: String(i), act }, disabled: false }),
  } });
  action(1, 'up');
  assert.equal(run('files[0].name'), 'voice.ogg');
  action(1, 'rm');
  assert.equal(get('#submit').disabled, false);
  get('#url-input').value = 'draft';
  get('#clear-queue').handlers.click();
  assert.equal(run('files.length'), 0);
  assert.equal(get('#url-input').value, '');
  assert.equal(get('#submit').disabled, true);
});
