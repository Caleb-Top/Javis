# Javis L1 D-Drive Acceptance

Status: NOT EXECUTED

This checklist is for real-user validation on the exact installed package. It is
not completed by automated worktree tests. Every installer or D-drive line stays
`NOT EXECUTED` until a human runs the scenario on the packaged build and records
the evidence honestly.

## Run Metadata

| Field | Value | Evidence | Result |
|---|---|---|---|
| Package SHA-256 | `<pending>` | `<Get-FileHash output>` | [ ] PASS  [ ] FAIL |
| Source commit | `<pending>` | `<git rev-parse HEAD output>` | [ ] PASS  [ ] FAIL |
| Package path | `<pending>` | `<absolute installed package path>` | [ ] PASS  [ ] FAIL |
| JAVIS_DATA_ROOT | `<pending>` | `<exact configured path>` | [ ] PASS  [ ] FAIL |
| Operator and timestamp | `<pending>` | `<name and ISO-8601 time>` | [ ] PASS  [ ] FAIL |

## Exact Invocation Offline

Run the exact installed package with network intentionally unavailable. Capture
the local exact-invocation response path, the request identity, and whether the
trigger used verified voice provenance or text entry.

| Field | Value | Evidence | Result |
|---|---|---|---|
| Exact invocation offline result | `<pending>` | `<request capture and terminal evidence>` | [ ] PASS  [ ] FAIL |
| Invocation path stayed local | `<pending>` | `<logs or trace proving no model dependency>` | [ ] PASS  [ ] FAIL |
| `voice_provenance` or text source recorded | `<pending>` | `<redacted payload or screenshot>` | [ ] PASS  [ ] FAIL |

## Malicious Local Webpage Rejection

Use a local webpage or equivalent attacker-controlled origin to prove the
runtime authorization boundary holds on the installed package.

| Field | Value | Evidence | Result |
|---|---|---|---|
| Malicious local webpage rejection | `<pending>` | `<origin, request, rejection code>` | [ ] PASS  [ ] FAIL |
| No unauthorized transcript access | `<pending>` | `<logs and captured UI state>` | [ ] PASS  [ ] FAIL |

## Verified Microphone Provenance

Capture one verified microphone turn and retain only the bounded provenance
evidence needed to prove the source.

| Field | Value | Evidence | Result |
|---|---|---|---|
| Verified microphone provenance | `<pending>` | `<session, owner generation, sequence, turn>` | [ ] PASS  [ ] FAIL |
| Provenance stayed server-verified | `<pending>` | `<redacted diagnostic capture>` | [ ] PASS  [ ] FAIL |

## Delayed Playback Stop Barge-In

Interrupt delayed playback on the installed package and prove the interruption
stops the active request and playback path that was actually speaking.

| Field | Value | Evidence | Result |
|---|---|---|---|
| Delayed playback stop barge-in | `<pending>` | `<request IDs, stop timing, terminal state>` | [ ] PASS  [ ] FAIL |
| Interrupted playback stayed request-scoped | `<pending>` | `<trace or logs>` | [ ] PASS  [ ] FAIL |

## Model Switch Continuity

Record the active routes before and after switching one route. Restart, then
confirm the selected route changed without disturbing the unselected route.

| Field | Value | Evidence | Result |
|---|---|---|---|
| Model route before switch | `<pending>` | `<redacted settings capture>` | [ ] PASS  [ ] FAIL |
| Model route after switch | `<pending>` | `<redacted settings capture>` | [ ] PASS  [ ] FAIL |
| Unselected route unchanged | `<pending>` | `<before/after comparison>` | [ ] PASS  [ ] FAIL |

## Restart Recovery

Restart the exact installed package and verify the same authoritative state
comes back without fabricating a clean pass for unrun failure scenarios.

| Field | Value | Evidence | Result |
|---|---|---|---|
| Restart recovery result | `<pending>` | `<status endpoints, logs, restart trace>` | [ ] PASS  [ ] FAIL |
| `/api/life/inner-state` available after restart | `<pending>` | `<endpoint capture>` | [ ] PASS  [ ] FAIL |
| `/api/life/snapshot` revision matches inner state source revision | `<pending>` | `<before/after capture>` | [ ] PASS  [ ] FAIL |

## Surface Revision Agreement

Capture the authoritative revisions observed by Live, Code, and Pet surfaces on
the installed package. Keep the `life.inner_state.changed` evidence or an
equivalent client trace.

| Field | Value | Evidence | Result |
|---|---|---|---|
| Live surface revision | `<pending>` | `<diagnostic capture>` | [ ] PASS  [ ] FAIL |
| Code surface revision | `<pending>` | `<diagnostic capture>` | [ ] PASS  [ ] FAIL |
| Pet surface revision | `<pending>` | `<diagnostic capture>` | [ ] PASS  [ ] FAIL |
| Live/Code/Pet revision agreement | `<pending>` | `<comparison plus event trace>` | [ ] PASS  [ ] FAIL |

## Final Decision

| Gate | Evidence | Result |
|---|---|---|
| Exact invocation offline | `<pending>` | [ ] PASS  [ ] FAIL |
| Malicious local webpage rejection | `<pending>` | [ ] PASS  [ ] FAIL |
| Verified microphone provenance | `<pending>` | [ ] PASS  [ ] FAIL |
| Delayed playback stop barge-in | `<pending>` | [ ] PASS  [ ] FAIL |
| Model switch continuity | `<pending>` | [ ] PASS  [ ] FAIL |
| Restart recovery | `<pending>` | [ ] PASS  [ ] FAIL |
| Live/Code/Pet revision agreement | `<pending>` | [ ] PASS  [ ] FAIL |

Overall decision: [ ] PASS  [ ] FAIL

Failure notes and follow-up issue IDs: `<pending>`
