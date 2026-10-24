'use strict';
/*
 * HeyTora DevLink - the VS Code connector.
 *
 * HeyTora used to learn what you were doing in your editor from
 * screenshots: it misread code, could not see a scrolled-away terminal or
 * a collapsed Problems panel, and every look cost a vision call. This
 * extension tells HeyTora the same things as data, through VS Code's own
 * API: the project and file you are in, your errors and warnings, git,
 * what your terminal commands printed and how they exited, where the
 * debugger stopped. It is a translator only - it holds no AI and decides
 * nothing; HeyTora's side keeps the permissions, the redaction and the
 * audit log.
 *
 * Read-only. Nothing is sent until you allow it for this workspace, and
 * then only what you allowed ("Code and problems", "Terminals and tasks",
 * "Debugging"). The HeyTora item in the status bar shows what HeyTora can
 * see and pauses it in one click.
 *
 * It talks to HeyTora on this computer only: 127.0.0.1, the port and the
 * per-install token from HeyTora's devlink.json, one JSON-RPC message per
 * line. No dependencies - Node's `net` is in every VS Code fork.
 */
const vscode = require('vscode');
const net = require('net');
const fs = require('fs');
const os = require('os');
const path = require('path');

const VERSION = require('./package.json').version;
const PROTOCOL = 1;
const CAPABILITIES = {
  read_code: 'Code and problems',
  read_terminals: 'Terminals and tasks',
  read_debug: 'Debugging',
};
// Event type -> the capability it needs (null: carries nothing of yours).
const EVENT_NEEDS = {
  'window.focused': null, 'workspace.folders': null,
  'editor.active': 'read_code', 'editor.selection': 'read_code', 'editor.visible': 'read_code',
  'tabs.changed': 'read_code', 'document.changed': 'read_code', 'document.saved': 'read_code',
  'diagnostics.changed': 'read_code', 'git.changed': 'read_code',
  'terminal.started': 'read_terminals', 'terminal.output': 'read_terminals', 'terminal.ended': 'read_terminals',
  'task.started': 'read_terminals', 'task.ended': 'read_terminals',
  'debug.started': 'read_debug', 'debug.output': 'read_debug', 'debug.stopped': 'read_debug',
  'debug.ended': 'read_debug',
};
const REQUEST_NEEDS = {
  'editor.window': 'read_code', 'file.read': 'read_code', 'symbols.workspace': 'read_code',
  'symbols.document': 'read_code', 'definition': 'read_code', 'references': 'read_code',
  'diagnostics.all': 'read_code',
};
// Files that are secrets by nature: never read, whatever is allowed.
const SECRET = [
  /(^|[\\/])\.env(\.[^\\/]*)?$/i, /\.env$/i, /\.(pem|key|pfx|p12|jks|keystore|kdbx|ppk|secret|secrets)$/i,
  /(^|[\\/])id_(rsa|dsa|ecdsa|ed25519)[^\\/]*$/i, /(^|[\\/])\.(ssh|gnupg|aws|azure|kube)[\\/]/i,
  /(^|[\\/])(credentials(\.[^\\/]*)?|\.npmrc|\.pypirc|\.netrc|_netrc)$/i, /(^|[\\/])secrets\.[^\\/]*$/i,
];
const isSecret = (p) => !!p && SECRET.some((re) => re.test(p));
const MAX_SELECTION = 4000;
const MAX_DIAGNOSTICS = 2000;
const SEVERITY = ['error', 'warning', 'info', 'hint'];

function discoveryPath() {
  if (process.env.HEYTORA_DEVLINK_DISCOVERY) return process.env.HEYTORA_DEVLINK_DISCOVERY;
  const base = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
  return path.join(base, 'HeyTora', 'devlink.json');
}

function enabled() {
  return vscode.workspace.getConfiguration('heytora.devlink').get('enabled', true) !== false;
}

function fileOf(uri) {
  return uri && ['file', 'vscode-remote', 'untitled'].includes(uri.scheme) ? uri.fsPath : '';
}

// ── the link to HeyTora ─────────────────────────────────────────────────────

class Link {
  constructor(context) {
    this.context = context;
    this.sock = null;
    this.buffer = '';
    this.nextId = 1;
    this.pending = new Map();
    this.connected = false;
    this.grants = {};
    this.paused = false;
    this.workspaceId = '';
    this.capabilityWords = CAPABILITIES;
    this.backoff = 2000;
    this.queue = [];
    this.flushTimer = null;
    this.disposed = false;
    this.handlers = {};          // request method -> async (params) => result
    this.onReady = () => {};     // after each hello: send what is true now
    this.status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 90);
    this.status.command = 'heytora.devlink.menu';
    this.render();
    this.status.show();
  }

  allows(capability) {
    return this.connected && !this.paused && (capability === null || !!this.grants[capability]);
  }

  connect() {
    if (this.sock || this.disposed || !enabled()) return;
    let info;
    try {
      info = JSON.parse(fs.readFileSync(discoveryPath(), 'utf8'));
    } catch (e) {
      return this.retry();                     // HeyTora not installed, or never started
    }
    if (!info.port || !info.token) return this.retry();
    const sock = net.createConnection({ host: '127.0.0.1', port: info.port });
    this.sock = sock;
    sock.setEncoding('utf8');
    sock.setNoDelay(true);
    sock.on('connect', () => this.hello(info.token));
    sock.on('data', (chunk) => this.receive(chunk));
    sock.on('error', () => {});
    sock.on('close', () => {
      this.sock = null;
      this.connected = false;
      for (const [, p] of this.pending) p.reject(new Error('HeyTora went away'));
      this.pending.clear();
      this.render();
      this.retry();
    });
  }

  async hello(token) {
    const folders = (vscode.workspace.workspaceFolders || []).map((f) => fileOf(f.uri)).filter(Boolean);
    try {
      const r = await this.request('hello', {
        token, protocol: PROTOCOL, connector: 'vscode', version: VERSION,
        app: vscode.env.appName, appVersion: vscode.version, remote: vscode.env.remoteName || '',
        trusted: vscode.workspace.isTrusted !== false,
        workspace: { name: vscode.workspace.name || '', folders },
      });
      this.connected = true;
      this.backoff = 2000;
      this.workspaceId = r.workspaceId;
      this.grants = r.grants || {};
      this.paused = !!r.paused;
      if (r.capabilities) {
        this.capabilityWords = Object.fromEntries(Object.entries(r.capabilities).map(([k, v]) => [k, v.title || k]));
      }
      this.render();
      this.onReady();
      if (!Object.keys(this.grants).length && folders.length) this.askOnce();
    } catch (e) {
      if (this.sock) this.sock.destroy();
    }
  }

  retry() {
    if (this.disposed) return;
    clearTimeout(this.retryTimer);
    this.retryTimer = setTimeout(() => this.connect(), this.backoff);
    this.backoff = Math.min(this.backoff * 2, 30000);
  }

  reconnect() {
    this.backoff = 2000;
    if (this.sock) this.sock.destroy();
    else this.connect();
  }

  write(message) {
    if (this.sock && !this.sock.destroyed) this.sock.write(JSON.stringify(message) + '\n');
  }

  request(method, params, timeoutMs = 5000) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`${method} timed out`)); }, timeoutMs);
      this.pending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      this.write({ jsonrpc: '2.0', id, method, params });
    });
  }

  receive(chunk) {
    this.buffer += chunk;
    let at;
    while ((at = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, at);
      this.buffer = this.buffer.slice(at + 1);
      if (!line.trim()) continue;
      let msg;
      try { msg = JSON.parse(line); } catch (e) { continue; }
      if (msg.method && msg.id !== undefined) this.answer(msg);
      else if (msg.method) this.notified(msg.method, msg.params || {});
      else if (this.pending.has(msg.id)) {
        const p = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) p.reject(new Error(msg.error.message || 'error'));
        else p.resolve(msg.result);
      }
    }
  }

  notified(method, params) {
    if (method === 'policy.changed') {
      const had = JSON.stringify(this.grants);
      this.grants = params.grants || {};
      this.paused = !!params.paused;
      this.render();
      if (had !== JSON.stringify(this.grants) || !this.paused) this.onReady();
    }
  }

  async answer(msg) {
    const needs = REQUEST_NEEDS[msg.method];
    const handler = this.handlers[msg.method];
    let reply;
    if (!handler || needs === undefined) {
      reply = { jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: `no method ${msg.method}` } };
    } else if (!this.allows(needs)) {
      reply = { jsonrpc: '2.0', id: msg.id, error: { code: -32002, message: `${needs} is not allowed here` } };
    } else {
      try {
        reply = { jsonrpc: '2.0', id: msg.id, result: await handler(msg.params || {}) };
      } catch (e) {
        reply = { jsonrpc: '2.0', id: msg.id, error: { code: -32000, message: String((e && e.message) || e) } };
      }
    }
    this.write(reply);
  }

  // Events go out in small batches: a burst of keystrokes is one message.
  emit(type, payload) {
    if (!(type in EVENT_NEEDS) || !this.allows(EVENT_NEEDS[type])) return;
    this.queue.push({ type, payload, ts: Date.now() });
    if (this.queue.length >= 200) this.flush();
    else if (!this.flushTimer) this.flushTimer = setTimeout(() => this.flush(), 100);
  }

  flush() {
    clearTimeout(this.flushTimer);
    this.flushTimer = null;
    if (!this.queue.length) return;
    const events = this.queue.splice(0, this.queue.length);
    this.write({ jsonrpc: '2.0', method: 'events', params: { events } });
  }

  // ── what you see of it ───────────────────────────────────────────────────

  render() {
    const allowed = Object.keys(this.grants).map((k) => this.capabilityWords[k] || k);
    if (!this.connected) {
      this.status.text = '$(debug-disconnect) HeyTora';
      this.status.tooltip = enabled()
        ? 'HeyTora is not running on this computer (or DevLink is off in its Settings). Click for options.'
        : 'HeyTora DevLink is turned off in your settings.';
    } else if (this.paused) {
      this.status.text = '$(debug-pause) HeyTora';
      this.status.tooltip = 'Paused - HeyTora is not reading anything. Click to resume.';
    } else if (!allowed.length) {
      this.status.text = '$(eye-closed) HeyTora';
      this.status.tooltip = 'HeyTora cannot see this workspace. Click to choose what it may read.';
    } else {
      this.status.text = '$(eye) HeyTora';
      this.status.tooltip = `HeyTora can read: ${allowed.join(', ')}. Read-only. Click to change or pause.`;
    }
  }

  async askOnce() {
    if (this.asked || this.context.workspaceState.get('heytora.devlink.notNow')) return;
    this.asked = true;
    const pick = await vscode.window.showInformationMessage(
      'HeyTora can read this workspace to help you - your code and problems, terminal output and debug ' +
      'output, as data instead of screenshots. It never changes anything, and you can pause it any time.',
      'Allow', 'Choose…', 'Not now');
    if (pick === 'Allow') await this.grant(Object.fromEntries(Object.keys(CAPABILITIES).map((k) => [k, 'always'])));
    else if (pick === 'Choose…') await choosePermissions(this);
    else if (pick === 'Not now') await this.context.workspaceState.update('heytora.devlink.notNow', true);
  }

  async grant(capabilities) {
    if (!this.connected) {
      vscode.window.showWarningMessage('HeyTora is not running on this computer.');
      return;
    }
    const r = await this.request('policy.grant', { capabilities });
    this.grants = r.grants || {};
    await this.context.workspaceState.update('heytora.devlink.notNow', undefined);
    this.render();
    this.onReady();
  }

  async setPaused(paused) {
    if (!this.connected) return;
    const r = await this.request('policy.pause', { paused });
    this.paused = !!r.paused;
    this.render();
    if (!this.paused) this.onReady();
  }

  dispose() {
    this.disposed = true;
    clearTimeout(this.retryTimer);
    this.flush();
    if (this.sock) this.sock.destroy();
    this.status.dispose();
  }
}

async function choosePermissions(link) {
  const items = Object.entries(CAPABILITIES).map(([key, label]) => ({
    key, label: link.capabilityWords[key] || label, picked: !!link.grants[key] || !Object.keys(link.grants).length,
  }));
  const picked = await vscode.window.showQuickPick(items, {
    canPickMany: true, title: 'What may HeyTora read in this workspace?', placeHolder: 'Read-only - nothing is ever changed',
  });
  if (!picked) return;
  const scope = await vscode.window.showQuickPick([
    { label: 'Always, for this workspace', value: 'always' },
    { label: 'Until HeyTora restarts', value: 'session' },
    { label: 'Just for the next answer', value: 'once' },
  ], { title: 'For how long?' });
  if (!scope) return;
  const wanted = new Set(picked.map((p) => p.key));
  await link.grant(Object.fromEntries(Object.keys(CAPABILITIES).map((k) => [k, wanted.has(k) ? scope.value : null])));
}

// Exactly what Tora would be given for a question here - shown, never sent.
async function showWhatHeyToraSees(link) {
  if (!link.connected) {
    vscode.window.showWarningMessage('HeyTora is not running on this computer.');
    return;
  }
  let text;
  try {
    text = (await link.request('context.preview', {}, 10000)).text;
  } catch (e) {
    text = `HeyTora could not say: ${e.message}`;
  }
  const content = `# What HeyTora sees\n\n${text}\n`;
  const doc = await vscode.workspace.openTextDocument({ language: 'markdown', content });
  await vscode.window.showTextDocument(doc, { preview: true });
}

async function showMenu(link) {
  const items = [];
  if (!link.connected) {
    items.push({ label: '$(refresh) Reconnect to HeyTora', run: () => link.reconnect() });
  } else {
    items.push({ label: '$(eye) Choose what HeyTora can read…', run: () => choosePermissions(link) });
    items.push({ label: '$(preview) What can HeyTora see?', run: () => showWhatHeyToraSees(link) });
    items.push(link.paused
      ? { label: '$(debug-start) Resume', run: () => link.setPaused(false) }
      : { label: '$(debug-pause) Pause - stop reading everything', run: () => link.setPaused(true) });
    if (Object.keys(link.grants).length) {
      items.push({ label: '$(circle-slash) Stop reading this workspace', run: () => link.grant(Object.fromEntries(Object.keys(CAPABILITIES).map((k) => [k, null]))) });
    }
  }
  const pick = await vscode.window.showQuickPick(items, { title: 'HeyTora DevLink' });
  if (pick) await pick.run();
}

// ── what the editor says ────────────────────────────────────────────────────

function editorInfo(ed) {
  if (!ed) return { path: '' };
  const doc = ed.document;
  const at = ed.selection ? ed.selection.active : { line: 0, character: 0 };
  return {
    path: fileOf(doc.uri), language: doc.languageId, line: at.line, character: at.character,
    dirty: doc.isDirty, line_count: doc.lineCount,
  };
}

function selectionInfo(ed) {
  const info = editorInfo(ed);
  if (!ed || !info.path || isSecret(info.path) || !ed.selection || ed.selection.isEmpty) return { ...info, text: '' };
  return { ...info, text: ed.document.getText(ed.selection).slice(0, MAX_SELECTION) };
}

function diagnosticsOf(entries) {
  const files = [];
  let total = 0;
  for (const [uri, items] of entries) {
    const p = fileOf(uri);
    if (!p || isSecret(p)) continue;
    const list = [];
    for (const d of items) {
      if (total >= MAX_DIAGNOSTICS) break;
      total += 1;
      list.push({
        severity: SEVERITY[d.severity] || 'info', message: String(d.message).slice(0, 500),
        line: d.range.start.line, character: d.range.start.character, endLine: d.range.end.line,
        source: d.source || '', code: typeof d.code === 'object' && d.code ? String(d.code.value) : (d.code ?? ''),
      });
    }
    files.push({ path: p, items: list });
  }
  return files;
}

function tabsOf() {
  const out = [];
  for (const group of vscode.window.tabGroups.all) {
    for (const tab of group.tabs) {
      const uri = tab.input && tab.input.uri;
      const p = fileOf(uri);
      if (p) out.push({ path: p, label: tab.label, dirty: tab.isDirty, active: tab.isActive, group: group.viewColumn });
    }
  }
  return out.slice(0, 80);
}

function gitStateOf(repo) {
  const head = repo.state.HEAD || {};
  const change = (c) => ({ path: c.uri.fsPath, status: c.status });
  return {
    root: repo.rootUri.fsPath, branch: head.name || (head.commit ? head.commit.slice(0, 8) : ''),
    commit: (head.commit || '').slice(0, 8), ahead: head.ahead || 0, behind: head.behind || 0,
    changes: repo.state.workingTreeChanges.slice(0, 200).map(change),
    staged: repo.state.indexChanges.slice(0, 200).map(change),
    conflicts: repo.state.mergeChanges.slice(0, 50).map(change),
  };
}

function location(loc) {
  const uri = loc.targetUri || loc.uri;
  const range = loc.targetSelectionRange || loc.targetRange || loc.range;
  return { path: fileOf(uri), line: range.start.line, character: range.start.character };
}

async function withText(locations) {
  const out = [];
  for (const loc of locations.slice(0, 50)) {
    const where = location(loc);
    if (!where.path || isSecret(where.path)) continue;
    try {
      const doc = await vscode.workspace.openTextDocument(loc.targetUri || loc.uri);
      where.text = doc.lineAt(where.line).text.trim().slice(0, 200);
    } catch (e) { /* a location without its text is still a location */ }
    out.push(where);
  }
  return out;
}

function inWorkspace(p) {
  if (!p || isSecret(p)) throw new Error('not a file HeyTora may read');
  const uri = vscode.Uri.file(p);
  if (!vscode.workspace.getWorkspaceFolder(uri)) throw new Error('outside this workspace');
  return uri;
}

const SYMBOL_KINDS = ['file', 'module', 'namespace', 'package', 'class', 'method', 'property', 'field',
  'constructor', 'enum', 'interface', 'function', 'variable', 'constant', 'string', 'number', 'boolean',
  'array', 'object', 'key', 'null', 'enum member', 'struct', 'event', 'operator', 'type parameter'];

function requestHandlers() {
  return {
    'editor.window': async ({ before = 25, after = 25 }) => {
      const ed = vscode.window.activeTextEditor;
      const p = ed ? fileOf(ed.document.uri) : '';
      if (!p) return null;
      if (isSecret(p)) return { path: p, sensitive: true };
      const doc = ed.document;
      const line = ed.selection.active.line;
      const start = Math.max(0, line - Math.min(before, 200));
      const end = Math.min(doc.lineCount - 1, line + Math.min(after, 200));
      const text = doc.getText(new vscode.Range(start, 0, end, doc.lineAt(end).text.length));
      return { path: p, language: doc.languageId, startLine: start, line, character: ed.selection.active.character, text };
    },
    'file.read': async ({ path: p, startLine = 0, endLine }) => {
      const doc = await vscode.workspace.openTextDocument(inWorkspace(p));   // unsaved edits included
      const last = Math.min(doc.lineCount - 1, endLine === undefined ? doc.lineCount - 1 : endLine);
      const first = Math.max(0, Math.min(startLine, last));
      const text = doc.getText(new vscode.Range(first, 0, last, doc.lineAt(last).text.length));
      return { text, lineCount: doc.lineCount, startLine: first, dirty: doc.isDirty };
    },
    'symbols.workspace': async ({ query = '' }) => {
      const found = (await vscode.commands.executeCommand('vscode.executeWorkspaceSymbolProvider', String(query))) || [];
      return found.slice(0, 100).map((s) => ({
        name: s.name, kind: SYMBOL_KINDS[s.kind] || '', container: s.containerName || '', ...location(s.location),
      })).filter((s) => s.path && !isSecret(s.path));
    },
    'symbols.document': async ({ path: p }) => {
      const found = (await vscode.commands.executeCommand('vscode.executeDocumentSymbolProvider', inWorkspace(p))) || [];
      const out = [];
      const walk = (items, container) => {
        for (const s of items) {
          if (out.length >= 300) return;
          const range = s.selectionRange || s.range || (s.location && s.location.range);
          out.push({ name: s.name, kind: SYMBOL_KINDS[s.kind] || '', container, line: range ? range.start.line : 0 });
          if (s.children) walk(s.children, s.name);
        }
      };
      walk(found, '');
      return out;
    },
    'definition': async ({ path: p, line = 0, character = 0 }) => {
      const found = (await vscode.commands.executeCommand('vscode.executeDefinitionProvider', inWorkspace(p),
        new vscode.Position(line, character))) || [];
      return withText(found);
    },
    'references': async ({ path: p, line = 0, character = 0 }) => {
      const found = (await vscode.commands.executeCommand('vscode.executeReferenceProvider', inWorkspace(p),
        new vscode.Position(line, character))) || [];
      return withText(found);
    },
    'diagnostics.all': async () => ({ files: diagnosticsOf(vscode.languages.getDiagnostics()) }),
  };
}

// ── wiring ──────────────────────────────────────────────────────────────────

function activate(context) {
  const link = new Link(context);
  link.handlers = requestHandlers();
  const subs = context.subscriptions;
  subs.push(link);
  const timers = new Map();
  const later = (key, ms, fn) => {
    clearTimeout(timers.get(key));
    timers.set(key, setTimeout(() => { timers.delete(key); fn(); }, ms));
  };
  const on = (event, fn) => { if (typeof event === 'function') subs.push(event(fn)); };

  // Everything that is true right now, sent after each hello and each new grant.
  let gitSnapshot = () => {};
  link.onReady = () => {
    if (vscode.window.state && vscode.window.state.focused) link.emit('window.focused', {});
    link.emit('editor.active', editorInfo(vscode.window.activeTextEditor));
    link.emit('tabs.changed', { tabs: tabsOf() });
    link.emit('diagnostics.changed', { full: true, files: diagnosticsOf(vscode.languages.getDiagnostics()) });
    gitSnapshot();
  };

  on(vscode.window.onDidChangeWindowState, (s) => { if (s.focused) link.emit('window.focused', {}); });
  on(vscode.window.onDidChangeActiveTextEditor, (ed) => link.emit('editor.active', editorInfo(ed)));
  on(vscode.window.onDidChangeTextEditorSelection, (e) =>
    later('selection', 150, () => link.emit('editor.selection', selectionInfo(e.textEditor))));
  on(vscode.window.onDidChangeTextEditorVisibleRanges, (e) => later('visible', 300, () => link.emit('editor.visible', {
    path: fileOf(e.textEditor.document.uri), ranges: e.visibleRanges.map((r) => [r.start.line, r.end.line]),
  })));
  if (vscode.window.tabGroups) on(vscode.window.tabGroups.onDidChangeTabs, () => later('tabs', 300, () => link.emit('tabs.changed', { tabs: tabsOf() })));
  on(vscode.workspace.onDidChangeTextDocument, (e) => {
    const p = fileOf(e.document.uri);
    if (p && e.contentChanges.length) later(`doc:${p}`, 500, () => link.emit('document.changed', { path: p, dirty: e.document.isDirty }));
  });
  on(vscode.workspace.onDidSaveTextDocument, (doc) => { const p = fileOf(doc.uri); if (p) link.emit('document.saved', { path: p }); });
  let changedUris = new Map();
  on(vscode.languages.onDidChangeDiagnostics, (e) => {
    for (const uri of e.uris) changedUris.set(uri.toString(), uri);
    later('diagnostics', 300, () => {
      const uris = [...changedUris.values()];
      changedUris = new Map();
      link.emit('diagnostics.changed', { files: diagnosticsOf(uris.map((u) => [u, vscode.languages.getDiagnostics(u)])) });
    });
  });
  on(vscode.workspace.onDidChangeWorkspaceFolders, () => link.reconnect());
  on(vscode.workspace.onDidChangeConfiguration, (e) => {
    if (e.affectsConfiguration('heytora.devlink')) { link.render(); if (enabled()) link.reconnect(); else if (link.sock) link.sock.destroy(); }
  });

  // Git, through the built-in Git extension's API.
  const gitExt = vscode.extensions.getExtension('vscode.git');
  if (gitExt) {
    Promise.resolve(gitExt.isActive ? gitExt.exports : gitExt.activate()).then((exp) => {
      const api = exp.getAPI(1);
      const watch = (repo) => {
        const send = () => later(`git:${repo.rootUri.fsPath}`, 400, () => link.emit('git.changed', gitStateOf(repo)));
        subs.push(repo.state.onDidChange(send));
        send();
      };
      api.repositories.forEach(watch);
      subs.push(api.onDidOpenRepository(watch));
      gitSnapshot = () => api.repositories.forEach((r) => link.emit('git.changed', gitStateOf(r)));
    }, () => {});
  }

  // Terminals: shell integration says each command, its output and its exit code.
  const terminalIds = new WeakMap();
  let terminalCount = 0;
  let commandCount = 0;
  const terminalId = (t) => { if (!terminalIds.has(t)) terminalIds.set(t, String(++terminalCount)); return terminalIds.get(t); };
  const running = new WeakMap();
  on(vscode.window.onDidStartTerminalShellExecution, (e) => {
    const tid = terminalId(e.terminal);
    const cid = `${tid}.${++commandCount}`;
    const command = (e.execution.commandLine && e.execution.commandLine.value) || '';
    link.emit('terminal.started', { terminal: tid, name: e.terminal.name, command, cwd: e.execution.cwd ? e.execution.cwd.fsPath : '', commandId: cid });
    const reading = (async () => {
      if (!link.allows('read_terminals')) return;
      let buf = '';
      let timer = null;
      const flush = () => {
        clearTimeout(timer);
        timer = null;
        if (buf) link.emit('terminal.output', { terminal: tid, name: e.terminal.name, text: buf, commandId: cid });
        buf = '';
      };
      try {
        for await (const data of e.execution.read()) {
          buf += data;
          if (buf.length > 16000) flush();
          else if (!timer) timer = setTimeout(flush, 250);
        }
      } catch (err) { /* the terminal closed */ }
      flush();
    })();
    running.set(e.execution, { cid, reading });
  });
  on(vscode.window.onDidEndTerminalShellExecution, async (e) => {
    const run = running.get(e.execution) || {};
    if (run.reading) await Promise.race([run.reading, new Promise((r) => setTimeout(r, 500))]);
    link.emit('terminal.ended', {
      terminal: terminalId(e.terminal), name: e.terminal.name, exitCode: e.exitCode === undefined ? null : e.exitCode,
      command: (e.execution.commandLine && e.execution.commandLine.value) || '', commandId: run.cid || '',
    });
  });

  // Tasks: start and end with the exit code (their output is not open to extensions).
  on(vscode.tasks.onDidStartTaskProcess, (e) => link.emit('task.started', { name: e.execution.task.name, source: e.execution.task.source }));
  on(vscode.tasks.onDidEndTaskProcess, (e) => link.emit('task.ended', { name: e.execution.task.name, exitCode: e.exitCode === undefined ? null : e.exitCode }));

  // Debugging: the adapter's own messages - output, and where it stopped.
  on(vscode.debug.onDidStartDebugSession, (s) => link.emit('debug.started', { session: s.id, name: s.name, type: s.type }));
  on(vscode.debug.onDidTerminateDebugSession, (s) => link.emit('debug.ended', { session: s.id, name: s.name }));
  if (vscode.debug.registerDebugAdapterTrackerFactory) {
    subs.push(vscode.debug.registerDebugAdapterTrackerFactory('*', {
      createDebugAdapterTracker(session) {
        let stopped = null;
        let out = '';
        let timer = null;
        const flush = () => {
          clearTimeout(timer);
          timer = null;
          if (out) link.emit('debug.output', { session: session.id, name: session.name, text: out });
          out = '';
        };
        return {
          onDidSendMessage(m) {
            if (m.type === 'event' && m.event === 'output' && m.body && m.body.category !== 'telemetry') {
              out += m.body.output || '';
              if (out.length > 16000) flush();
              else if (!timer) timer = setTimeout(flush, 250);
            } else if (m.type === 'event' && m.event === 'stopped' && m.body) {
              stopped = { reason: m.body.reason, description: m.body.description || m.body.text || '' };
            } else if (m.type === 'response' && m.command === 'stackTrace' && m.success && stopped) {
              const frame = ((m.body && m.body.stackFrames) || [])[0];
              if (frame) {
                link.emit('debug.stopped', {
                  session: session.id, name: session.name, ...stopped, frame: frame.name,
                  path: (frame.source && frame.source.path) || '', line: Math.max(0, (frame.line || 1) - 1),
                });
              }
              stopped = null;
            }
          },
          onWillStopSession() { flush(); },
        };
      },
    }));
  }

  subs.push(vscode.commands.registerCommand('heytora.devlink.menu', () => showMenu(link)));
  subs.push(vscode.commands.registerCommand('heytora.devlink.permissions', () => choosePermissions(link)));
  subs.push(vscode.commands.registerCommand('heytora.devlink.show', () => showWhatHeyToraSees(link)));
  subs.push(vscode.commands.registerCommand('heytora.devlink.pause', () => link.setPaused(true)));
  subs.push(vscode.commands.registerCommand('heytora.devlink.resume', () => link.setPaused(false)));
  subs.push(vscode.commands.registerCommand('heytora.devlink.reconnect', () => link.reconnect()));

  link.connect();
  return { link };
}

function deactivate() {}

module.exports = { activate, deactivate, isSecret };
