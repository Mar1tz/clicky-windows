'use strict';
// Enough of the `vscode` module for extension.js to run under plain Node:
// tests drive it (open a file, fire diagnostics, run a terminal command)
// and the real extension code reacts as it would in VS Code.
const path = require('path');
const fs = require('fs');

function emitter() {
  const listeners = new Set();
  const event = (fn) => { listeners.add(fn); return { dispose: () => listeners.delete(fn) }; };
  event.fire = (data) => { for (const fn of [...listeners]) fn(data); };
  return event;
}

class Position { constructor(line, character) { this.line = line; this.character = character; } }
class Range {
  constructor(a, b, c, d) {
    if (a instanceof Position) { this.start = a; this.end = b; } else { this.start = new Position(a, b); this.end = new Position(c, d); }
  }
  get isEmpty() { return this.start.line === this.end.line && this.start.character === this.end.character; }
}
class Selection extends Range {
  constructor(anchor, active) { super(anchor, active); this.anchor = anchor; this.active = active; }
}
const Uri = {
  file: (p) => ({ scheme: 'file', fsPath: path.normalize(p), toString: () => 'file:///' + path.normalize(p).replace(/\\/g, '/') }),
};

const documents = new Map();     // fsPath -> document
function document(p, text, languageId = 'typescript') {
  const lines = () => doc.text.split('\n');
  const doc = {
    uri: Uri.file(p), languageId, isDirty: false, text,
    get lineCount() { return lines().length; },
    lineAt: (i) => ({ text: lines()[i] || '' }),
    getText: (range) => {
      if (!range) return doc.text;
      const ls = lines();
      if (range.start.line === range.end.line) return ls[range.start.line].slice(range.start.character, range.end.character);
      const out = [ls[range.start.line].slice(range.start.character)];
      for (let i = range.start.line + 1; i < range.end.line; i++) out.push(ls[i]);
      out.push(ls[range.end.line].slice(0, range.end.character));
      return out.join('\n');
    },
  };
  documents.set(doc.uri.fsPath, doc);
  return doc;
}

const config = { enabled: true };
const diagnostics = new Map();   // fsPath -> [diagnostic]
const commands = new Map();
const canned = { symbols: [], references: [] };
const prompts = [];
const opened = [];               // what untitled documents were opened with
let promptAnswer = 'Allow';

const vscode = {
  version: '1.143.1',
  env: { appName: 'Visual Studio Code', remoteName: undefined },
  StatusBarAlignment: { Left: 1, Right: 2 },
  Position, Range, Selection, Uri,
  window: {
    state: { focused: true },
    activeTextEditor: undefined,
    onDidChangeWindowState: emitter(),
    onDidChangeActiveTextEditor: emitter(),
    onDidChangeTextEditorSelection: emitter(),
    onDidChangeTextEditorVisibleRanges: emitter(),
    onDidStartTerminalShellExecution: emitter(),
    onDidEndTerminalShellExecution: emitter(),
    tabGroups: { all: [], onDidChangeTabs: emitter() },
    statusBar: null,
    createStatusBarItem() {
      vscode.window.statusBar = { text: '', tooltip: '', command: '', show() {}, dispose() {} };
      return vscode.window.statusBar;
    },
    async showInformationMessage(message, ...items) { prompts.push(message); return promptAnswer; },
    async showWarningMessage(message) { prompts.push(message); },
    async showQuickPick(items) { return items; },
    async showTextDocument(doc) { return { document: doc }; },
  },
  workspace: {
    name: 'proj', isTrusted: true, workspaceFolders: [],
    getConfiguration: () => ({ get: (key, def) => (key in config ? config[key] : def) }),
    onDidChangeTextDocument: emitter(), onDidSaveTextDocument: emitter(),
    onDidChangeWorkspaceFolders: emitter(), onDidChangeConfiguration: emitter(),
    async openTextDocument(uri) {
      if (uri && uri.content !== undefined) {          // a new untitled document
        opened.push(uri.content);
        return document(`untitled:${opened.length}`, uri.content, uri.language || 'plaintext');
      }
      if (documents.has(uri.fsPath)) return documents.get(uri.fsPath);
      return document(uri.fsPath, fs.readFileSync(uri.fsPath, 'utf8'));
    },
    getWorkspaceFolder(uri) {
      return vscode.workspace.workspaceFolders.find((f) => uri.fsPath.toLowerCase().startsWith(f.uri.fsPath.toLowerCase() + path.sep));
    },
  },
  languages: {
    onDidChangeDiagnostics: emitter(),
    getDiagnostics(uri) {
      if (uri) return diagnostics.get(uri.fsPath) || [];
      return [...diagnostics.entries()].map(([p, items]) => [Uri.file(p), items]);
    },
  },
  extensions: { getExtension: () => undefined },
  tasks: { onDidStartTaskProcess: emitter(), onDidEndTaskProcess: emitter() },
  debug: {
    onDidStartDebugSession: emitter(), onDidTerminateDebugSession: emitter(), trackers: [],
    registerDebugAdapterTrackerFactory(type, factory) { vscode.debug.trackers.push(factory); return { dispose() {} }; },
  },
  commands: {
    registerCommand(id, fn) { commands.set(id, fn); return { dispose() {} }; },
    async executeCommand(id) {
      if (id === 'vscode.executeWorkspaceSymbolProvider') return canned.symbols;
      if (id === 'vscode.executeReferenceProvider') return canned.references;
      return [];
    },
  },
  // test helpers
  _: {
    emitter, document, diagnostics, documents, config, canned, prompts, commands, opened,
    answerPrompts(answer) { promptAnswer = answer; },
  },
};

module.exports = vscode;
