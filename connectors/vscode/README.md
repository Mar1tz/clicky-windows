# HeyTora DevLink

Lets [HeyTora](https://heytora.co.za) read your workspace as data instead of screenshots. Then
"what's wrong?", "why did the build fail?" and "where was I?" get answered from what your editor
actually knows, including the off-screen parts.

## What HeyTora can read

Nothing until you allow it, per workspace. You choose any of:

| Permission | What it covers |
| --- | --- |
| **Code and problems** | The project and file you are in, the cursor and selection, open tabs, errors and warnings, symbols and references, git branch and changes |
| **Terminals and tasks** | Commands you run in the integrated terminal, their output and exit codes; tasks starting and ending |
| **Debugging** | Debug sessions, their output, and where the debugger stopped |

Each one can be allowed for good (in this workspace), until HeyTora restarts, or for just the next
answer.

## What it never does

- **Never changes anything.** It is read-only: it does not edit files, run commands or start tasks.
- **Never reads secrets files**, whatever you allow: `.env*`, `*.pem`, `*.key`, `id_rsa*`,
  `.ssh/`, `.aws/`, credentials files, and anything in a `.heytoraignore` at your workspace's root
  (gitignore-style lines).
- **Never talks to the internet.** It talks to the HeyTora app on this computer (127.0.0.1) with a
  per-install token. HeyTora redacts keys and tokens before it keeps or sends anything, and logs every
  answer it sends to the AI. You can see them in HeyTora → Settings → Code editors → What HeyTora sent.

## The status bar

`$(eye) HeyTora` shows what HeyTora can read right now. Click it to change permissions, pause
everything, or stop reading this workspace.

| Shows | Means |
| --- | --- |
| eye | Reading what you allowed |
| eye closed | Connected, nothing allowed here |
| pause | Paused: nothing is read |
| unplugged | HeyTora is not running, or DevLink is off in its settings |

## Requirements

- The HeyTora app, running on this computer. Install the extension from HeyTora: Settings → Code
  editors → Connect.
- VS Code 1.93 or newer, or a fork: Cursor, Windsurf, VSCodium, Trae.
- Terminal output needs VS Code's shell integration, which is on by default for PowerShell, bash, zsh
  and fish.
- Remote workspaces (SSH, WSL, Dev Containers) connect, but git state and file search need a local
  folder for now.
