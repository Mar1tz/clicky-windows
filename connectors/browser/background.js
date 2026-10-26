'use strict';
// HeyTora's browser companion (CI-02..CI-05, the Massive Direction Plan).
//
// Talks only to HeyTora on this PC: ws://127.0.0.1:<port> with the token
// HeyTora wrote into config.json when it set this extension up. It says
// three kinds of thing, and nothing else:
//   detection   the service and host of the active tab - only when the host
//               is one HeyTora knows (config.hosts); never the page's text,
//               its full address, or cookies (CI-02)
//   reading     a tab's title, text and its fields and links, read-only,
//               when HeyTora asks for it for a task (CI-04)
//   acting      a click or typing in that tab, when HeyTora asks - each one
//               already approved by the person in HeyTora; password,
//               one-time-code and card fields are never touched (CI-05)
// The service worker may be stopped by the browser at any time; it comes
// back on startup, on an alarm, and on any tab change, and reconnects.

const SECRET = '(password|passcode|pass-code|passwd|pin\\b|otp|one[-_ ]?time|verification[-_ ]?code|' +
  'security[-_ ]?code|cvc|cvv|csc|card[-_ ]?number|cc[-_]?(num|number|exp|csc)|iban|ssn|social[-_ ]?security)';
const SECRET_AUTOCOMPLETE = /(current-password|new-password|one-time-code|cc-number|cc-csc|cc-exp)/i;

function isSecretField(attrs) {
  // attrs: {type, autocomplete, name, id, label} of an input - the same rule
  // the page functions below use, here so it can be tested.
  const a = attrs || {};
  if (String(a.type || '').toLowerCase() === 'password') return true;
  if (SECRET_AUTOCOMPLETE.test(String(a.autocomplete || ''))) return true;
  const words = [a.name, a.id, a.label].filter(Boolean).join(' ');
  return new RegExp(SECRET, 'i').test(words);
}

function hostOf(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== 'https:' && u.protocol !== 'http:') return '';
    if (u.username || u.password) return '';
    return u.hostname.toLowerCase().replace(/\.$/, '');
  } catch (e) {
    return '';
  }
}

function knownHost(url, hosts) {
  const h = hostOf(url);
  return h && (hosts || []).includes(h) ? h : '';
}

// -- the page functions: run inside the tab, self-contained ---------------------------

function heytoraRead(secretPattern) {
  const secret = new RegExp(secretPattern, 'i');
  const auto = /(current-password|new-password|one-time-code|cc-number|cc-csc|cc-exp)/i;
  const labelOf = (el) => {
    if (el.labels && el.labels.length) return el.labels[0].innerText.trim();
    return (el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || el.id || '').trim();
  };
  let n = 0;
  const fields = [];
  for (const el of document.querySelectorAll('input, textarea, select, button, [role=button], a[href]')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const ref = el.getAttribute('data-heytora-ref') || ('h' + (++n));
    el.setAttribute('data-heytora-ref', ref);
    const label = labelOf(el) || (el.innerText || '').trim().slice(0, 80);
    const isSecret = (el.type || '').toLowerCase() === 'password' || auto.test(el.autocomplete || '') ||
      secret.test([el.name, el.id, label].join(' '));
    fields.push({ ref, tag: el.tagName.toLowerCase(), type: el.type || '', label: label.slice(0, 120),
      secret: isSecret, value: isSecret ? '' : String(el.value || '').slice(0, 200) });
    if (fields.length >= 300) break;
  }
  return { title: document.title, text: (document.body ? document.body.innerText : '').slice(0, 20000), fields };
}

function heytoraAct(action, ref, text, secretPattern) {
  const secret = new RegExp(secretPattern, 'i');
  const auto = /(current-password|new-password|one-time-code|cc-number|cc-csc|cc-exp)/i;
  const el = document.querySelector('[data-heytora-ref="' + ref + '"]');
  if (!el) return { ok: false, error: 'that part of the page is gone - read the tab again' };
  const label = (el.labels && el.labels.length ? el.labels[0].innerText : el.getAttribute('aria-label') || '') || '';
  if ((el.type || '').toLowerCase() === 'password' || auto.test(el.autocomplete || '') ||
      secret.test([el.name, el.id, label].join(' '))) {
    return { ok: false, error: 'that is a password, code or card field - it stays yours to fill' };
  }
  el.scrollIntoView({ block: 'center' });
  if (action === 'click') {
    el.click();
    return { ok: true };
  }
  if (action === 'type') {
    el.focus();
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const set = Object.getOwnPropertyDescriptor(proto, 'value');
    if (set && set.set) set.set.call(el, text); else el.value = text;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return { ok: el.value === text, value: el.value === text ? text : el.value };
  }
  return { ok: false, error: 'unknown action ' + action };
}

// -- the connection to HeyTora ---------------------------------------------------------

const MAX = 1000000;
let socket = null;
let config = null;
let lastTab = '';

async function loadConfig(api) {
  if (config) return config;
  const res = await fetch(api.runtime.getURL('config.json'));
  config = await res.json();
  return config;
}

function send(msg) {
  if (!socket || socket.readyState !== 1) return false;
  const s = JSON.stringify(msg);
  if (s.length > MAX) return false;
  socket.send(s);
  return true;
}

async function reportTab(api, tab) {
  const cfg = await loadConfig(api);
  const host = tab ? knownHost(tab.url, cfg.hosts) : '';
  const key = host ? host + '#' + tab.id : '';
  if (key === lastTab) return;
  lastTab = key;
  send(host ? { type: 'tab', tabId: tab.id, host } : { type: 'tab', tabId: null, host: '' });
}

async function handle(api, msg) {
  let tabId = msg.tabId;
  if (tabId === null || tabId === undefined) {           // the tab they are looking at
    const [tab] = await api.tabs.query({ active: true, lastFocusedWindow: true });
    if (!tab) return { id: msg.id, ok: false, error: 'no tab is open' };
    tabId = tab.id;
  }
  try {
    if (msg.type === 'read') {
      const [r] = await api.scripting.executeScript({ target: { tabId }, func: heytoraRead, args: [SECRET] });
      return { id: msg.id, ok: true, ...r.result };
    }
    if (msg.type === 'click' || msg.type === 'type') {
      const [r] = await api.scripting.executeScript({ target: { tabId }, func: heytoraAct,
        args: [msg.type, msg.ref, msg.text || '', SECRET] });
      return { id: msg.id, ...r.result };
    }
    return { id: msg.id, ok: false, error: 'unknown request' };
  } catch (e) {
    return { id: msg.id, ok: false, error: String(e && e.message || e) };
  }
}

async function connect(api, makeSocket) {
  if (socket && (socket.readyState === 0 || socket.readyState === 1)) return;
  const cfg = await loadConfig(api);
  socket = makeSocket('ws://127.0.0.1:' + cfg.port + '/');
  socket.onopen = () => send({ type: 'hello', token: cfg.token, extension: api.runtime.id,
    version: api.runtime.getManifest().version });
  socket.onmessage = async (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === 'welcome') {
      lastTab = '';
      const [tab] = await api.tabs.query({ active: true, lastFocusedWindow: true });
      reportTab(api, tab);
      return;
    }
    if (msg.id !== undefined) send(await handle(api, msg));
  };
  socket.onclose = () => { socket = null; };
  socket.onerror = () => {};
}

function start(api, makeSocket) {
  const go = () => connect(api, makeSocket).catch(() => {});
  api.runtime.onStartup.addListener(go);
  api.runtime.onInstalled.addListener(go);
  api.alarms.create('heytora', { periodInMinutes: 0.5 });
  api.alarms.onAlarm.addListener(() => { go(); send({ type: 'ping' }); });
  api.tabs.onActivated.addListener(async ({ tabId }) => {
    go();
    try { reportTab(api, await api.tabs.get(tabId)); } catch (e) {}
  });
  api.tabs.onUpdated.addListener((tabId, info, tab) => {
    if (info.status === 'complete' && tab.active) reportTab(api, tab);
  });
  go();
}

if (typeof module !== 'undefined') {
  module.exports = { isSecretField, hostOf, knownHost, start, SECRET, _reset: () => { socket = null; config = null; lastTab = ''; } };
} else {
  start(chrome, (url) => new WebSocket(url));
}
