# Javis L2 D-Drive Acceptance

Status: NOT EXECUTED

This checklist records manual evidence from the exact final candidate installed
on the D drive. Automated worktree tests do not execute or satisfy these gates.
No result may change from `NOT EXECUTED` until a human runs that row against the
identified package and records reproducible evidence.

## Safety Boundary

- Use a dedicated acceptance account and a new, empty acceptance data root.
- Never point an acceptance run at an existing `%APPDATA%\Javis`, `brain_data`,
  SQLite database, model directory, or user workspace.
- Record package and source hashes before execution. Do not edit the installed
  package or database to manufacture a result.
- Redact bearer capabilities, API keys, message content, and subject identifiers
  from attached evidence.
- Stop the run on an unexpected data path, identity binding, or write outside the
  isolated acceptance root.

## Candidate Metadata

| Item | Result | Recorded value | Evidence |
|---|---|---|---|
| Final candidate package SHA-256 | NOT EXECUTED | `<pending>` | `<Get-FileHash output>` |
| Source commit | NOT EXECUTED | `<pending>` | `<git rev-parse HEAD output>` |
| Installed package path | NOT EXECUTED | `<pending>` | `<absolute D-drive path>` |
| Isolated acceptance data root | NOT EXECUTED | `<pending>` | `<absolute empty path>` |
| Operator and timestamp | NOT EXECUTED | `<pending>` | `<name and ISO-8601 time>` |

## Memory Scenarios

| Gate | Result | Required evidence | Failure notes / issue |
|---|---|---|---|
| Cross-day and restart recall | NOT EXECUTED | A cited item recalled after a date boundary and a clean restart, with before/after timestamps | `<pending>` |
| Source citation | NOT EXECUTED | UI/API evidence links recalled text to the expected terminal event or source receipt without exposing private authority fields | `<pending>` |
| Shared explicit confirmation | NOT EXECUTED | Proposal is absent from recall before confirmation, present after explicit confirmation, and absent after revocation | `<pending>` |
| Guest privacy | NOT EXECUTED | Guest and a different packaged subject receive no private recall, list, or deletion-status data | `<pending>` |
| Model switch continuity | NOT EXECUTED | Memory citation and ownership remain stable across a model route switch and restart | `<pending>` |
| SQLite failure degradation | NOT EXECUTED | Deliberate failure in the isolated acceptance root degrades memory only; conversation, cancellation, L0, and L1 continue | `<pending>` |

## D-Drive Lifecycle

| Gate | Result | Required evidence | Failure notes / issue |
|---|---|---|---|
| D-drive install | NOT EXECUTED | Installer log, installed path, data-root path, runtime status, and proof that runtime data is outside the install directory | `<pending>` |
| D-drive upgrade | NOT EXECUTED | Old/new package hashes plus recall and citation evidence before and after upgrade using only the isolated acceptance data | `<pending>` |
| D-drive uninstall data retention | NOT EXECUTED | Before/after hashes and directory listing proving the isolated user data remains while program files are removed | `<pending>` |

## Deletion Evidence

| Gate | Result | Required evidence | Failure notes / issue |
|---|---|---|---|
| Delete from DB and FTS | NOT EXECUTED | Governed UI/API deletion receipt and read-only diagnostics showing no recalled result | `<pending>` |
| Cache and prompt invalidation | NOT EXECUTED | The active conversation stops surfacing the deleted item without a process restart | `<pending>` |
| Restart and reindex non-resurrection | NOT EXECUTED | Deleted text remains absent after restart, index rebuild, and terminal replay | `<pending>` |

## Final Decision

Overall result: NOT EXECUTED

Evidence bundle path: `<pending>`

Open issue IDs: `<pending>`

The L2 manual release gate remains `NOT EXECUTED` until every required row above
has real evidence from the same identified final candidate.
