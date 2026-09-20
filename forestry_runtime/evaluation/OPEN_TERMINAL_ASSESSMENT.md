# Open Terminal 0.13.0 replacement assessment

Date: 2026-09-14

## Decision

Use Open WebUI with Open Terminal directly for ordinary interactive file and
terminal work. Do not add Open Terminal or its MCP dependency to the Forestry
Runtime core yet. Keep the project's durable code-job executor until Open
Terminal can satisfy the side-effect reconciliation contract below.

## Verified surface

The published `ghcr.io/open-webui/open-terminal:0.13.0` Linux image exists for
amd64 and arm64. Its OpenAPI surface exposes bounded file list/read/write/edit
and search, plus command start, offset-based status, process listing, input and
cancel operations. The optional MCP server exposes the same operations as 16
tools.

This is enough for ordinary CodeAct-style work: the model writes code, runs it,
reads stderr or generated files, edits the code, and runs it again. Forestry
does not need to reproduce that path for ordinary chats.

## Blocking contract gaps

1. `POST /execute` creates its own process ID and does not accept a caller
   Action ID or idempotency key. If the Runtime loses the response after the
   process starts, it cannot reliably distinguish that process from another
   attempt. Automatically replaying the action can repeat side effects.
2. The server owns process logs, but Forestry Run records cannot atomically
   register the process before submission. Domain and long-running jobs still
   need the existing create-by-ID then reconcile behavior.
3. A host-side MCP probe on Windows 11/Python 3.14 listed all tools, then failed
   its first `list_files` call with `effective_ids unavailable on this
   platform`. The failure comes from Open Terminal 0.13.0 calling
   `os.access(..., effective_ids=True)` and only catching `TypeError`; Windows
   raises `NotImplementedError`. The production target is a Linux container,
   so this is an extra Windows limitation rather than a framework criterion.
4. The official image installs the REST server by default. MCP is an optional
   Python dependency, so forcing MCP into both the Open Terminal image and the
   Runtime would add a substantial dependency tree without solving item 1.

## Adoption gate

Replace `code_run`, `job_status`, and `job_cancel` only when a pinned Linux
deployment passes all of these checks:

- a caller-supplied idempotency key identifies command submission;
- a lost submit response can be reconciled without replaying the command;
- process status and logs survive service restart;
- offset reads, cancel, timeout and generated-file discovery work;
- one chat workspace cannot access another chat's files;
- real Qwen completes the directory, CSV and script-repair evaluations at least
  as reliably as the existing executor.

Until then, the existing executor is a durable job service rather than a second
Agent loop. PydanticAI remains the only component that chooses and sequences
actions.

References:

- https://docs.openwebui.com/features/open-terminal/
- https://docs.openwebui.com/reference/server-side-tool-calling/
- https://pydantic.dev/docs/ai/mcp/client/
