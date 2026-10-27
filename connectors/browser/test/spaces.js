'use strict';
// BR-03: job tabs in the real background.js, against a mock `chrome` with
// tabs and storage. stdout: one JSON object with what happened.
const ext = require('../background.js');

const tabs = { 1: { id: 1, url: 'https://mail.example.com/', active: true } };   // theirs
let next = 50;
const store = {};
const api = {
  tabs: {
    create: async ({ url }) => { const t = { id: next++, url, active: false }; tabs[t.id] = t; return t; },
    remove: async (id) => { if (!tabs[id]) throw new Error('No tab with id: ' + id); delete tabs[id]; },
    get: async (id) => { if (!tabs[id]) throw new Error('No tab with id: ' + id); return tabs[id]; },
    query: async () => [tabs[1]],
  },
  storage: { local: { get: async (k) => ({ [k]: store[k] }), set: async (o) => Object.assign(store, o) } },
  scripting: { executeScript: async ({ func }) => [{ result: func.name === 'heytoraRead'
    ? { title: 'page', text: '', fields: [] } : { ok: true } }] },
};

(async () => {
  const out = {};
  const opened = await ext.handle(api, { id: 1, type: 'open', url: 'https://shop.example.com/orders', job: 'j1' });
  out.opened = opened.ok && opened.tabId === 50 && tabs[50] && tabs[50].active === false;
  out.badUrl = (await ext.handle(api, { id: 2, type: 'open', url: 'file:///C:/secret', job: 'j1' })).ok;
  out.ownTab = (await ext.handle(api, { id: 3, type: 'read', tabId: 50, job: 'j1' })).ok;
  out.otherJob = (await ext.handle(api, { id: 4, type: 'click', tabId: 50, job: 'j2', ref: 'h1' })).error || '';
  out.theirTab = (await ext.handle(api, { id: 5, type: 'click', tabId: 1, job: 'j1', ref: 'h1' })).error || '';
  out.noJob = (await ext.handle(api, { id: 6, type: 'read', tabId: 1 })).error || '';
  out.activeTab = (await ext.handle(api, { id: 7, type: 'read', tabId: null, job: 'j1' })).ok;
  await ext.handle(api, { id: 8, type: 'open', url: 'https://shop.example.com/invoice', job: 'j1' });
  delete tabs[51];                                        // a browser restart: that id is gone
  out.spaces = (await ext.handle(api, { id: 9, type: 'spaces' })).spaces;
  out.closed = (await ext.handle(api, { id: 10, type: 'close_job', job: 'j1' })).closed;
  out.theirsStill = !!tabs[1];
  out.left = (await ext.handle(api, { id: 11, type: 'spaces' })).spaces.length;
  process.stdout.write(JSON.stringify(out));
})();
