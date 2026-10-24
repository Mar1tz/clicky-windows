"""Connect an editor: find VS Code and its forks, and install the connector.

Settings → Code editors → Connect. The connector ships inside HeyTora
(connectors/vscode); here it is packed into a .vsix - a zip with a
manifest, made with zipfile, so neither npm nor vsce is needed - and
installed with the editor's own command line (`code --install-extension`).
That is the same path a Marketplace install takes, works while the editor
is open, and the editor keeps the extension updated from then on only if
we publish it there: until then a newer HeyTora reinstalls its own copy.

Which editors: any folder under Programs or Program Files with a VS Code
product.json (VS Code, Insiders, Cursor, Windsurf, VSCodium, Trae, ...),
with its CLI in bin\\ or resources\\app\\bin\\.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

EXTENSION_ID = "heytora.heytora-devlink"
INSTALL_TIMEOUT_S = 120


def connector_dir() -> Path:
    """connectors/vscode - beside the code when run from source, in the
    bundle's _internal folder when frozen."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "connectors" / "vscode"


def manifest() -> dict:
    return json.loads((connector_dir() / "package.json").read_text(encoding="utf-8"))


def build_vsix(dest: Path) -> Path:
    """The connector as a .vsix in `dest`."""
    src, pkg = connector_dir(), manifest()
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{pkg['name']}-{pkg['version']}.vsix"
    files = ["package.json", "extension.js", "README.md", "icon.png"]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("extension.vsixmanifest", _vsixmanifest(pkg))
        for name in files:
            if (src / name).exists():
                z.write(src / name, f"extension/{name}")
        if (src / "LICENSE").exists():
            z.write(src / "LICENSE", "extension/LICENSE.txt")
    return out


_CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension=".json" ContentType="application/json"/><Default Extension=".js" ContentType="application/javascript"/>
<Default Extension=".md" ContentType="text/markdown"/><Default Extension=".png" ContentType="image/png"/>
<Default Extension=".txt" ContentType="text/plain"/><Default Extension=".vsixmanifest" ContentType="text/xml"/>
</Types>"""


def _vsixmanifest(pkg: dict) -> str:
    kinds = ",".join(pkg.get("extensionKind") or ["workspace"])
    return f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="{escape(pkg['name'])}" Version="{escape(pkg['version'])}" Publisher="{escape(pkg['publisher'])}" />
    <DisplayName>{escape(pkg['displayName'])}</DisplayName>
    <Description xml:space="preserve">{escape(pkg['description'])}</Description>
    <Tags>{escape(','.join(pkg.get('keywords') or []))}</Tags>
    <Categories>{escape(','.join(pkg.get('categories') or []))}</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{escape(pkg['engines']['vscode'])}" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value="" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value="" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="{escape(kinds)}" />
      <Property Id="Microsoft.VisualStudio.Code.LocalizedLanguages" Value="" />
      <Property Id="Microsoft.VisualStudio.Services.GitHubFlavoredMarkdown" Value="true" />
    </Properties>
    <License>extension/LICENSE.txt</License>
    <Icon>extension/icon.png</Icon>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code" />
  </Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/README.md" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Content.License" Path="extension/LICENSE.txt" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Icons.Default" Path="extension/icon.png" Addressable="true" />
  </Assets>
</PackageManifest>"""


# ── the editors on this PC ─────────────────────────────────────────────────

@dataclass
class Editor:
    name: str               # "Visual Studio Code", "Cursor"
    cli: str                # its code.cmd / cursor.cmd
    data_folder: str        # ".vscode", ".cursor": where its extensions live, under the home folder
    installed: str = ""     # the connector's version in it, "" for none

    @property
    def key(self) -> str:
        return self.data_folder.strip(".") or self.name.lower()


def editors(roots: Optional[list[Path]] = None, home: Optional[Path] = None) -> list[Editor]:
    """VS Code and every fork installed for this user or for all users.
    (TORA_EDITOR_ROOTS / TORA_EDITOR_HOME: where to look instead - the
    tests' sandbox.)"""
    if roots is None:
        override = os.environ.get("TORA_EDITOR_ROOTS")
        places = (override.split(os.pathsep) if override is not None else
                  (os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
                   os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")))
        roots = [Path(p) for p in places if p and os.path.isdir(p)]
    home = home or Path(os.environ.get("TORA_EDITOR_HOME") or Path.home())
    found: dict[str, Editor] = {}
    want = manifest()
    for root in roots:
        try:
            apps = [d for d in root.iterdir() if d.is_dir()]
        except OSError:
            continue
        for app in apps:
            product = next(iter([app / "resources" / "app" / "product.json"] if (app / "resources" / "app" / "product.json").exists()
                                else sorted(app.glob("*/resources/app/product.json"))), None)
            if product is None:
                continue
            try:
                data = json.loads(product.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            folder, cli = data.get("dataFolderName"), _cli(app, data.get("applicationName") or "")
            if not folder or not cli or folder in found:
                continue
            found[folder] = Editor(data.get("nameLong") or app.name, cli, folder,
                                   _installed(home / folder / "extensions", want["publisher"], want["name"]))
    return sorted(found.values(), key=lambda e: (e.data_folder != ".vscode", e.name))


def _cli(app: Path, application: str) -> str:
    for folder in (app / "bin", app / "resources" / "app" / "bin"):
        if not folder.is_dir():
            continue
        preferred = folder / f"{application}.cmd"
        if preferred.exists():
            return str(preferred)
        for f in sorted(folder.glob("*.cmd")):
            if "tunnel" not in f.name:
                return str(f)
    return ""


def _installed(extensions: Path, publisher: str, name: str) -> str:
    try:
        versions = [d.name.rsplit("-", 1)[-1] for d in extensions.glob(f"{publisher}.{name}-*") if d.is_dir()]
    except OSError:
        return ""
    return max(versions, key=_version_key, default="")


def _version_key(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))


def memory_key(editor: Editor) -> str:
    """Where the hub remembers the connector version it put into `editor`."""
    return f"connector:{editor.key}"


def memory_keys() -> list[str]:
    return [memory_key(e) for e in editors()]


def ensure_installed(known: dict[str, str], dest: Path) -> dict[str, str]:
    """The connector into every editor that lacks it or has an older one.
    `known`: memory_key -> the version HeyTora installed there before. An
    editor HeyTora had connected that has none now was cleaned out by the
    user, and is left alone. {memory_key: version} for each one done."""
    want = manifest()["version"]
    done: dict[str, str] = {}
    vsix: Optional[Path] = None
    for editor in editors():
        key = memory_key(editor)
        if editor.installed and _version_key(editor.installed) >= _version_key(want):
            continue
        if not editor.installed and known.get(key):
            continue                                       # they removed it
        vsix = vsix or build_vsix(dest)
        ok, _said = install(editor, vsix)
        if ok:
            done[key] = want
    return done


# A VS Code-family window's title: "● app.ts - shop - Visual Studio Code"
# (activeEditorShort - rootName - appName, the default title format).
_TITLE_APPS = ("Visual Studio Code - Insiders", "Visual Studio Code", "Cursor", "Windsurf", "VSCodium", "TRAE SOLO",
               "Trae", "Antigravity", "Kiro")


def parse_title(title: str) -> tuple[str, str, str]:
    """(editor, file, project) from a window title; ("", "", "") when it
    is not a VS Code-family editor's."""
    parts = [p.strip() for p in (title or "").lstrip("●* ").split(" - ")]
    app = next((a for a in _TITLE_APPS if parts and parts[-1].lower() == a.lower()), "")
    if not app:
        return "", "", ""
    rest = parts[:-1]
    if len(rest) >= 2:
        return app, rest[0], rest[1]
    return app, (rest[0] if rest else ""), ""


def install(editor: Editor, vsix: Path) -> tuple[bool, str]:
    """Install `vsix` into `editor` with its own CLI. (ok, what it said)."""
    try:
        out = subprocess.run([editor.cli, "--install-extension", str(vsix), "--force"], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=INSTALL_TIMEOUT_S,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    said = (out.stdout + out.stderr).strip()
    return out.returncode == 0 and "fail" not in said.lower(), said[-500:]
