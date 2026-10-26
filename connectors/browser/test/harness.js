'use strict';
// Runs background.js against a mock `chrome`, connected to HeyTora's real
// browser bridge (the Python test starts it). stdout: one JSON line per
// thing that happened. argv: <config.json path> <tab url> <extension id>
const fs = require('fs');

const [configPath, tabUrl, extId] = process.argv.slice(2);
const say = (what) => process.stdout.write(JSON.stringify(what) + '\n');
const listeners = (name) => ({ addListener: (fn) => { listeners[name] = fn; } });
const page = {
  title: 'Inbox (3) - sam@example.com - Gmail',
  fields: [{ ref: 'h1', tag: 'input', type: 'text', label: 'Search mail', secret: false, value: '' }],
};

const chrome = {
  runtime: {
    id: extId,
    getURL: (p) => 'file:' + configPath,
    getManifest: () => ({ version: '0.1.0' }),
    onStartup: listeners('startup'),
    onInstalled: listeners('installed'),
  },
  alarms: { create: () => {}, onAlarm: listeners('alarm') },
  tabs: {
    query: async () => [{ id: 7, url: tabUrl, active: true }],
    get: async (id) => ({ id, url: tabUrl, active: true }),
    onActivated: listeners('activated'),
    onUpdated: listeners('updated'),
  },
  scripting: {
    executeScript: async ({ func, args }) => {
      say({ executed: func.name, args: args.slice(0, -1) });
      if (func.name === 'heytoraRead') return [{ result: { title: page.title, text: 'Primary', fields: page.fields } }];
      return [{ result: { ok: true } }];
    },
  },
};

global.fetch = async () => ({ json: async () => JSON.parse(fs.readFileSync(configPath, 'utf8')) });
const ext = require('../background.js');
ext.start(chrome, (url) => {
  const ws = new WebSocket(url, { headers: { Origin: 'chrome-extension://' + extId } });
  ws.addEventListener('open', () => say({ open: true }));
  ws.addEventListener('close', (e) => say({ closed: e.code }));
  // background.js uses the on* properties, as a browser WebSocket has them.
  return ws;
});
setTimeout(() => process.exit(0), Number(process.env.HARNESS_S || 4) * 1000);
