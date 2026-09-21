"""Deterministic project templates: goal text in, pre-filled Steps out.

Why templates before LLMs: the Phase 1 acceptance run ("create a folder
called MyApp and build a React foundation in it") must complete with
zero network. A template knows the exact files a minimal Vite+React or
Python project needs and emits one Step per file with a ready-made
`create_file` call, so the worker just executes and the verifier just
checks the disk. Anything a template does not recognise falls through
to a single LLM-driven step (see planner.py).

Tool names and argument shapes match what agents/tools registers:
    create_folder{path}  create_file{path, content}  list_dir{path, depth}
    run_command{command, cwd}
All paths are relative to the task workspace and use forward slashes;
ToolContext.resolve_path() normalises them per OS.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from agents.core import AgentContext, Step, ToolCall


# -- Spec shared by every template -------------------------------------------

@dataclass
class TemplateSpec:
    """What a template needs to know about the task."""
    goal: str                       # lower-cased goal text
    root: str = ""                  # subdir under the workspace, "" = workspace itself
    name: str = "my-app"            # npm / project display name
    package: str = "app"            # python package name
    install: bool = False           # add the "npm install" step
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_context(cls, ctx: AgentContext) -> "TemplateSpec":
        meta = ctx.task.metadata or {}
        root = _clean_subdir(meta.get("subdir") or "")
        base = root or _basename(ctx.task.workspace) or "my-app"
        return cls(
            goal=(ctx.task.goal or "").lower(),
            root=root,
            name=npm_name(meta.get("name") or base),
            package=py_package(meta.get("package") or meta.get("name") or base),
            install=bool(meta.get("install", False)),
        )

    def path(self, *parts: str) -> str:
        return join(self.root, *parts)


def join(*parts: str) -> str:
    """'MyApp' + 'src/App.jsx' -> 'MyApp/src/App.jsx'; '' + 'x' -> 'x'."""
    return "/".join(p.strip("/") for p in parts if p and p.strip("/"))


def _clean_subdir(sub: str) -> str:
    sub = str(sub).strip().replace("\\", "/").strip("/")
    if not sub or ".." in sub.split("/"):
        return ""
    return sub


def _basename(path: str) -> str:
    path = str(path or "").replace("\\", "/").rstrip("/")
    return path.rsplit("/", 1)[-1] if path else ""


def npm_name(raw: str) -> str:
    n = re.sub(r"[^a-z0-9._-]+", "-", str(raw).strip().lower()).strip("-._")
    return n or "my-app"


def py_package(raw: str) -> str:
    n = re.sub(r"[^a-z0-9_]+", "_", str(raw).strip().lower()).strip("_")
    if not n:
        return "app"
    return f"_{n}" if n[0].isdigit() else n


# -- Step builders -----------------------------------------------------------

def create_folder_step(path: str, title: Optional[str] = None) -> Step:
    label = path or "."
    return Step(title=title or f"Create folder {label}",
                description=f"Create the directory '{label}' inside the workspace.",
                tool_calls=[ToolCall("create_folder", {"path": label})])


def create_file_step(path: str, content: str, description: str = "") -> Step:
    return Step(title=f"Create {path}",
                description=description or f"Write '{path}'.",
                tool_calls=[ToolCall("create_file", {"path": path, "content": content})])


def verify_step(root: str, depth: int = 2) -> Step:
    return Step(title="Verify project structure",
                description="List the project tree to confirm every file exists.",
                tool_calls=[ToolCall("list_dir", {"path": root or ".", "depth": depth})])


# -- Templates ---------------------------------------------------------------

class ProjectTemplate:
    """Subclasses: set `name`, implement matches() and steps()."""
    name: str = "base"

    def matches(self, spec: TemplateSpec) -> bool:
        raise NotImplementedError

    def steps(self, spec: TemplateSpec) -> list[Step]:
        raise NotImplementedError


class ReactViteTemplate(ProjectTemplate):
    name = "react-vite"

    def matches(self, spec: TemplateSpec) -> bool:
        return "react" in spec.goal

    def steps(self, spec: TemplateSpec) -> list[Step]:
        n = spec.name
        files = [
            ("package.json", REACT_PACKAGE_JSON.replace("__NAME__", n)),
            ("index.html", REACT_INDEX_HTML.replace("__NAME__", n)),
            ("vite.config.js", REACT_VITE_CONFIG),
            ("src/main.jsx", REACT_MAIN_JSX),
            ("src/App.jsx", REACT_APP_JSX.replace("__NAME__", n)),
            ("src/App.css", REACT_APP_CSS),
            (".gitignore", NODE_GITIGNORE),
            ("README.md", REACT_README.replace("__NAME__", n)),
        ]
        steps = [create_folder_step(spec.root, "Create project folder" if spec.root else "Prepare workspace")]
        steps += [create_file_step(spec.path(p), c) for p, c in files]
        if spec.install:
            steps.append(Step(
                title="Install dependencies",
                description="Run npm install in the project folder (needs Node.js).",
                tool_calls=[ToolCall("run_command", {"command": "npm install", "cwd": spec.root or "."})],
            ))
        steps.append(verify_step(spec.root))
        return steps


class PythonProjectTemplate(ProjectTemplate):
    name = "python-project"
    _QUALIFIERS = ("project", "package", "app", "application", "library", "module", "cli", "tool")

    def matches(self, spec: TemplateSpec) -> bool:
        return "python" in spec.goal and any(q in spec.goal for q in self._QUALIFIERS)

    def steps(self, spec: TemplateSpec) -> list[Step]:
        pkg = spec.package
        files = [
            ("pyproject.toml", PY_PYPROJECT.replace("__PKG__", pkg)),
            (f"src/{pkg}/__init__.py", PY_INIT.replace("__PKG__", pkg)),
            ("tests/test_smoke.py", PY_TEST.replace("__PKG__", pkg)),
            ("README.md", PY_README.replace("__PKG__", pkg)),
            (".gitignore", PY_GITIGNORE),
        ]
        steps = [create_folder_step(spec.root, "Create project folder" if spec.root else "Prepare workspace")]
        steps += [create_file_step(spec.path(p), c) for p, c in files]
        steps.append(verify_step(spec.root))
        return steps


class FolderTemplate(ProjectTemplate):
    """Plain 'create a folder called X' - one step. Needs a name: either
    metadata.subdir or a `called/named X` phrase in the goal."""
    name = "folder"
    _NAME = re.compile(r"(?:called|named)\s+[\"'`]?([^\s\"'`,.]+)")

    def matches(self, spec: TemplateSpec) -> bool:
        if not any(w in spec.goal for w in ("folder", "directory")):
            return False
        return bool(self.folder_name(spec))

    def folder_name(self, spec: TemplateSpec) -> str:
        if spec.root:
            return spec.root
        m = self._NAME.search(spec.goal)
        return _clean_subdir(m.group(1)) if m else ""

    def steps(self, spec: TemplateSpec) -> list[Step]:
        return [create_folder_step(self.folder_name(spec))]


# Order matters: React / Python goals usually also say "folder".
DEFAULT_TEMPLATES: list[ProjectTemplate] = [
    ReactViteTemplate(), PythonProjectTemplate(), FolderTemplate(),
]


def match_template(spec: TemplateSpec,
                   templates: Optional[list[ProjectTemplate]] = None) -> Optional[ProjectTemplate]:
    for t in templates or DEFAULT_TEMPLATES:
        if t.matches(spec):
            return t
    return None


# -- File contents -----------------------------------------------------------
# Minimal but real: `npm install && npm run dev` / `pip install -e . && pytest`
# work as written.

REACT_PACKAGE_JSON = """{
  "name": "__NAME__",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.4",
    "vite": "^6.0.0"
  }
}
"""

REACT_INDEX_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>__NAME__</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
"""

REACT_VITE_CONFIG = """import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
})
"""

REACT_MAIN_JSX = """import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import './App.css'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
"""

REACT_APP_JSX = """import { useState } from 'react'

function App() {
  const [count, setCount] = useState(0)

  return (
    <main className="app">
      <h1>__NAME__</h1>
      <p>Your React + Vite app is running.</p>
      <button onClick={() => setCount((c) => c + 1)}>
        count is {count}
      </button>
      <p className="hint">Edit <code>src/App.jsx</code> and save to reload.</p>
    </main>
  )
}

export default App
"""

REACT_APP_CSS = """:root {
  font-family: system-ui, Avenir, Helvetica, Arial, sans-serif;
  line-height: 1.5;
  color-scheme: light dark;
  color: #213547;
  background-color: #ffffff;
}

@media (prefers-color-scheme: dark) {
  :root {
    color: rgba(255, 255, 255, 0.87);
    background-color: #242424;
  }
}

body {
  margin: 0;
  display: flex;
  min-height: 100vh;
  align-items: center;
  justify-content: center;
}

.app {
  max-width: 640px;
  padding: 2rem;
  text-align: center;
}

button {
  padding: 0.6em 1.2em;
  font-size: 1em;
  border-radius: 8px;
  border: 1px solid transparent;
  cursor: pointer;
}

.hint {
  opacity: 0.7;
}
"""

NODE_GITIGNORE = """node_modules
dist
dist-ssr
*.local
.env
.env.*
npm-debug.log*
yarn-debug.log*
yarn-error.log*
.DS_Store
"""

REACT_README = """# __NAME__

A minimal React application scaffolded with [Vite](https://vite.dev).

## Getting started

```bash
npm install
npm run dev
```

Then open the URL Vite prints (usually http://localhost:5173).

## Scripts

- `npm run dev` - start the dev server with hot reload
- `npm run build` - production build into `dist/`
- `npm run preview` - serve the production build locally

## Layout

```
index.html        entry page, mounts #root
vite.config.js    Vite + React plugin
src/main.jsx      bootstraps React
src/App.jsx       root component
src/App.css       styles
```
"""

PY_PYPROJECT = """[project]
name = "__PKG__"
version = "0.1.0"
description = "A Python project scaffolded by Tora."
readme = "README.md"
requires-python = ">=3.10"
dependencies = []

[project.optional-dependencies]
dev = ["pytest"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
"""

PY_INIT = '''"""__PKG__ - project scaffold."""

__version__ = "0.1.0"


def hello(name: str = "world") -> str:
    return f"Hello, {name}!"
'''

PY_TEST = """from __PKG__ import hello


def test_hello():
    assert hello() == "Hello, world!"
"""

PY_README = """# __PKG__

A minimal Python project layout (`src/` package + `tests/`).

## Getting started

```bash
python -m venv .venv
.venv\\Scripts\\activate      # Windows   (source .venv/bin/activate on macOS/Linux)
pip install -e .[dev]
pytest
```
"""

PY_GITIGNORE = """__pycache__/
*.py[cod]
.venv/
venv/
build/
dist/
*.egg-info/
.pytest_cache/
.env
"""
