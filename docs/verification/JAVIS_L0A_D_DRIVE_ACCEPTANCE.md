# Javis L0-A D-Drive Acceptance

Status: NOT EXECUTED

本表是 D 盘安装包实机验收记录，不由自动化测试代填。执行人必须保留命令输出、截图或导出文件路径，并逐项选择 PASS 或 FAIL。当前文件只定义验收结构，不表示任何 D 盘场景已经通过。

## Run Metadata

| Field | Value | Evidence | Result |
|---|---|---|---|
| Package SHA-256 | `<pending>` | `<Get-FileHash output>` | [ ] PASS  [ ] FAIL |
| Source commit | `<pending>` | `<git rev-parse HEAD output>` | [ ] PASS  [ ] FAIL |
| Package path | `<pending>` | `<absolute D-drive path>` | [ ] PASS  [ ] FAIL |
| JAVIS_DATA_ROOT | `<pending>` | `<exact configured path>` | [ ] PASS  [ ] FAIL |
| Operator and timestamp | `<pending>` | `<name and ISO-8601 time>` | [ ] PASS  [ ] FAIL |

## Restart Continuity

1. Start the packaged App with a clean, explicitly configured user-data root.
2. Capture `/api/life/identity`, `/api/life/lineage`, and `/api/life/snapshot`.
3. Close the App normally, confirm the owned backend exits, then start it again.
4. Capture the same endpoints and attach the clean-shutdown checkpoint evidence.

| Field | Value | Evidence | Result |
|---|---|---|---|
| Identity ID before restart | `<pending>` | `<identity response>` | [ ] PASS  [ ] FAIL |
| Identity ID after restart | `<pending>` | `<identity response>` | [ ] PASS  [ ] FAIL |
| Instance ID before restart | `<pending>` | `<lineage response>` | [ ] PASS  [ ] FAIL |
| Instance ID after restart | `<pending>` | `<lineage response>` | [ ] PASS  [ ] FAIL |
| Clean shutdown continuity | `<pending>` | `<runtime status and checkpoint>` | [ ] PASS  [ ] FAIL |

Identity and instance IDs must remain equal across a normal restart. Any unexpected fork is a FAIL and must retain the complete `life/instances` evidence.

## Model Route Isolation

1. Record both Live and Code model routes before the switch.
2. Change only the selected route through the unified Settings surface.
3. Restart and verify the selected route persisted while the other route did not change.
4. Send one request through each route and retain redacted diagnostics.

| Field | Value | Evidence | Result |
|---|---|---|---|
| Model route before switch | `<pending>` | `<redacted settings response>` | [ ] PASS  [ ] FAIL |
| Model route after switch | `<pending>` | `<redacted settings response>` | [ ] PASS  [ ] FAIL |
| Unselected route unchanged | `<pending>` | `<before/after comparison>` | [ ] PASS  [ ] FAIL |
| Route request smoke test | `<pending>` | `<request IDs and terminal states>` | [ ] PASS  [ ] FAIL |

## Abnormal Shutdown

1. Export diagnostic life events before the scenario.
2. While one request is active, force-terminate only the App-owned backend identified by PID.
3. Before restarting, run the `assess_previous_run` command from `JAVIS_OPERATIONS.md`.
4. Restart and verify recovery state is visible, no stale request resumes, and a new request can complete.

| Field | Value | Evidence | Result |
|---|---|---|---|
| Abnormal shutdown result | `<pending>` | `<PID, assessment, restart status>` | [ ] PASS  [ ] FAIL |
| `unclean_shutdown` detected | `<pending>` | `<assessment JSON>` | [ ] PASS  [ ] FAIL |
| `temporary_authority_valid` is false | `<pending>` | `<assessment JSON>` | [ ] PASS  [ ] FAIL |
| Stale request not resumed | `<pending>` | `<conversation and life events>` | [ ] PASS  [ ] FAIL |

## Corruption Recovery

1. Stop the App and backend, then back up the complete configured `life` directory.
2. Corrupt only the acceptance copy of `life/identity/current.json`; do not alter immutable versions or the backup.
3. Start Javis and verify `read_only_recovery=True`, identity continuity uses the last verified version, and mutating identity operations remain unavailable.
4. Stop Javis, restore the backed-up `life` directory, restart, and verify healthy read/write operation and unchanged identity ID.

| Field | Value | Evidence | Result |
|---|---|---|---|
| Corruption recovery result | `<pending>` | `<status, logs, backup and restore evidence>` | [ ] PASS  [ ] FAIL |
| Read-only recovery entered | `<pending>` | `<runtime status>` | [ ] PASS  [ ] FAIL |
| Last verified identity retained | `<pending>` | `<identity before/after comparison>` | [ ] PASS  [ ] FAIL |
| Backup restore completed | `<pending>` | `<hashes and final runtime status>` | [ ] PASS  [ ] FAIL |

## Surface Revisions

Capture revisions after one listening, thinking, speaking, tool execution, blocked, error, offline, and recovered transition. Revisions must be monotonic, and all surfaces must consume the same validated `ExpressionIntent` contract.

| Field | Value | Evidence | Result |
|---|---|---|---|
| Live snapshot revision | `<pending>` | `<diagnostic capture>` | [ ] PASS  [ ] FAIL |
| Code snapshot revision | `<pending>` | `<diagnostic capture>` | [ ] PASS  [ ] FAIL |
| Pet snapshot revision | `<pending>` | `<diagnostic capture>` | [ ] PASS  [ ] FAIL |
| Expression revision monotonic | `<pending>` | `<ordered event export>` | [ ] PASS  [ ] FAIL |
| 2D/Orb fallback result | `<pending>` | `<forced fallback evidence>` | [ ] PASS  [ ] FAIL |

## Final Decision

| Gate | Evidence | Result |
|---|---|---|
| Package integrity | `<pending>` | [ ] PASS  [ ] FAIL |
| Identity and lineage continuity | `<pending>` | [ ] PASS  [ ] FAIL |
| Model route isolation | `<pending>` | [ ] PASS  [ ] FAIL |
| Abnormal shutdown recovery | `<pending>` | [ ] PASS  [ ] FAIL |
| Corruption recovery | `<pending>` | [ ] PASS  [ ] FAIL |
| Surface revision consistency | `<pending>` | [ ] PASS  [ ] FAIL |

Overall decision: [ ] PASS  [ ] FAIL

Failure notes and follow-up issue IDs: `<pending>`
