'use strict';
// Runs extension.js against the mock `vscode`, driven over stdin one JSON
// command per line, saying what happened on stdout one JSON line each.
// The Python tests start HeyTora's real DevLink hub and drive this, so
// both halves are tested speaking to each other.
//
//   node harness.js <project folder>      (HEYTORA_DEVLINK_DISCOVERY says where devlink.json is)
const Module = require('module');
const path = require('path');
const readline = require('readline');
const mock = require('./mock-vscode');

const resolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  if (request === 'vscode') return require.resolve('./mock-vscode');
  return resolve.call(this, request, ...rest);
};

const project = path.resolve(process.argv[2]);
mock.workspace.workspaceFolders = [{ uri: mock.Uri.file(project), name: path.basename(project), index: 0 }];
mock.workspace.name = path.basename(project);
if (process.env.PROMPT_ANSWER) mock._.answerPrompts(process.env.PROMPT_ANSWER === 'none' ? undefined : process.env.PROMPT_ANSWER);

const say = (what) => process.stdout.write(JSON.stringify(what) + '\n');
const state = new Map();
const context = {
  subscriptions: [],
  workspaceState: { get: (k) => state.get(k), update: async (k, v) => { state.set(k, v); } },
};
const ext = require('../extension.js');
const { link } = ext.activate(context);

const helloWas = link.hello.bind(link);
link.hello = async (token) => {
  await helloWas(token);
  say({ connected: link.connected, grants: link.grants, workspaceId: link.workspaceId, status: mock.window.statusBar.text });
};

function editorFor(file, line, character, selectTo) {
  const doc = mock._.documents.get(file) || mock._.document(file, require('fs').readFileSync(file, 'utf8'));
  const active = new mock.Position(line, character);
  const anchor = selectTo ? new mock.Position(selectTo[0], selectTo[1]) : active;
  return { document: doc, selection: new mock.Selection(anchor, active), visibleRanges: [] };
}

const actions = {
  focus() { mock.window.onDidChangeWindowState.fire({ focused: true }); },
  open({ file, line = 0, character = 0 }) {
    mock.window.activeTextEditor = editorFor(path.join(project, file), line, character);
    mock.window.onDidChangeActiveTextEditor.fire(mock.window.activeTextEditor);
  },
  select({ file, line, character, to }) {
    mock.window.activeTextEditor = editorFor(path.join(project, file), line, character, to);
    mock.window.onDidChangeTextEditorSelection.fire({ textEditor: mock.window.activeTextEditor });
  },
  diagnostics({ file, items }) {
    const p = path.join(project, file);
    mock._.diagnostics.set(mock.Uri.file(p).fsPath, items.map((d) => ({
      severity: d.severity, message: d.message, source: d.source, code: d.code,
      range: new mock.Range(d.line, d.character || 0, d.line, (d.character || 0) + 1),
    })));
    mock.languages.onDidChangeDiagnostics.fire({ uris: [mock.Uri.file(p)] });
  },
  async terminal({ command, output, exitCode }) {
    const terminal = { name: 'pwsh' };
    const execution = {
      commandLine: { value: command }, cwd: mock.Uri.file(project),
      async *read() { for (const chunk of output) { yield chunk; await new Promise((r) => setTimeout(r, 20)); } },
    };
    mock.window.onDidStartTerminalShellExecution.fire({ terminal, execution });
    await new Promise((r) => setTimeout(r, 30 * output.length + 50));
    mock.window.onDidEndTerminalShellExecution.fire({ terminal, execution, exitCode });
  },
  debugStop({ file, line, reason }) {
    const session = { id: 's1', name: 'Launch app', type: 'node' };
    mock.debug.onDidStartDebugSession.fire(session);
    const tracker = mock.debug.trackers[0].createDebugAdapterTracker(session);
    tracker.onDidSendMessage({ type: 'event', event: 'stopped', body: { reason, description: 'Paused on exception' } });
    tracker.onDidSendMessage({ type: 'response', command: 'stackTrace', success: true,
      body: { stackFrames: [{ name: 'total', line: line + 1, source: { path: path.join(project, file) } }] } });
  },
  canned({ symbols, references }) {
    const loc = (x) => ({ uri: mock.Uri.file(path.join(project, x.file)), range: new mock.Range(x.line, x.character || 0, x.line, (x.character || 0) + 1) });
    mock._.canned.symbols = (symbols || []).map((s) => ({ name: s.name, kind: 11, containerName: '', location: loc(s) }));
    mock._.canned.references = (references || []).map(loc);
  },
  async command({ id }) { await mock._.commands.get(id)(); },
  status() { say({ status: mock.window.statusBar.text, tooltip: mock.window.statusBar.tooltip, grants: link.grants, paused: link.paused, prompts: mock._.prompts }); },
  quit() { link.dispose(); setTimeout(() => process.exit(0), 50); },
};

readline.createInterface({ input: process.stdin }).on('line', async (line) => {
  if (!line.trim()) return;
  const cmd = JSON.parse(line);
  try {
    await actions[cmd.do](cmd);
    link.flush();
    say({ done: cmd.do });
  } catch (e) {
    say({ failed: cmd.do, error: String(e && e.stack || e) });
  }
});
