贾维斯 / J.A.R.V.I.S.
======================

贾维斯 is a personal Windows desktop butler built with PySide6 on the
Codex agent runtime.

Current 0.3.1 milestone:
- project sessions backed by Codex app-server
- tool-free pure model chats
- project folders and project AGENTS.md editing
- context and token telemetry card
- Markdown answers with IDE-style code syntax highlighting
- collapsible streamed reasoning merged into the assistant response
- paged chat history with coalesced streaming updates and render caching
- server-request approvals, project approval memory, and permission modes
- turn checkpoints, diff review, partial restore, and task progress
- session search, rename, pin, archive, fork, and Markdown/JSON export
- file explorer
- explorer copy, paste, recycle-bin delete, and folder creation
- region and per-display screenshots with image attachments
- per-server MCP enable/disable controls and discoverable Skill management
- PowerShell command console
- embedded browser
- keyboard command registry
- nine persistent visual themes: hacker green, Gundam, Iron Man, sci-fi,
  black empire, Cyberpunk 2077, Pacific Rim, StarCraft, and retro CRT
- in-app Markdown update history viewer

Project layout
--------------

  sourcecode\  Source, tests and the active Python virtual environment
  Tools\       Offline OpenAI docs, skills, policies and China MCP
  Envirment\   Python and Codex offline installers and setup guide

Run
---

  $env:PYTHONPATH = "$PWD\src"
  $env:CODEX_CIL_CODEX_BIN = "$PWD\..\Envirment\Codex运行环境\codex.exe"
  .\.venv\Scripts\python.exe -m codex_cil

After the user environment variables have been configured, the application
can also be launched from any directory:

  codex-cil

From the sourcecode directory, the quick launcher is:

  ./启动

If the offline environment has not been installed yet:

  ..\Envirment\检查运行环境.ps1 -InstallAll

Optional environment variables:

  CODEX_CIL_DATA_DIR         Override local application data directory
  CODEX_CIL_CODEX_BIN        Override the Codex executable path
  CODEX_HOME                 Codex state directory (defaults to ~/.codex)

Model and authentication
------------------------

Project and tool-free chats both inherit model, provider, base URL, and
authentication from CODEX_HOME/config.toml. Codex CIL never copies provider
tokens into its database or project files.
