# Offline backup and restoration at the original location

The workflow saves application and native checkpoint databases, context/knowledge snapshots, the local session key, task files, immutable products and sealed manifests. It copies only`.forestry` and`tasks` within the configured workspace. It never executes their contents or starts inference/Workers. The backup is private and retains existing private data and session identity.

Retain the application source, Python environment, configuration and original data separately. This is a state/product backup, not full deployment migration. Resource identities, versions and inclusion are recorded; current availability/version is reported separately. Restoration preserves absolute paths and grants. Missing/changed inputs require explicit registration and task amendment; source protection is never relaxed.

1. Pause tasks, confirm actual Workers have stopped or completed, and reconcile unresolved actions. Stop the configured local service. Closing a browser does not stop execution.
2. Keep the original configuration and create a new separate backup directory from the project environment:

```powershell
python -m forestry_agent.cli backup create --config config/app.local.yaml --path backups/20261012-v1
python -m forestry_agent.cli backup inspect --config config/app.local.yaml --path backups/20261012-v1
```

The path must be outside the running workspace. Create refuses an existing destination. Output reports the manifest digest, file count/bytes, resource availability and original workspace. Native SQLite backup captures committed WAL contents under writer locks. Products are verified by size/SHA. Only known internal database sidecars are excluded; ordinary task products with`.sqlite` or`.sqlite-wal` names remain byte files.

Database copies use standalone SQLite files. Inspection uses immutable reads, creates no WAL/SHM caches and does not consume injected transactions. Nonempty internal WAL/journal files are rejected; historical empty WAL/SHM caches are not application data. The corrected inspector preserved the successful copy's complete inventory/bytes/mtimes, recorded in`backup-inspection-readonly-20261012-v1.json`, without another backup or restore.

3. Keep the service stopped and release every SQLite connection. Verify the reported original workspace path; move its existing`.forestry` and`tasks` to a separate retained location. Do not delete them. Restore refuses to overwrite either directory.
4. Restore using the same configuration and original workspace:

```powershell
python -m forestry_agent.cli backup restore --config config/app.local.yaml --path backups/20261012-v1
```

The command verifies the manifest and all files, copies and verifies a staging directory, and publishes products before the database. The service stays stopped and task states remain unchanged. No model, Worker or unresolved effect is automatically resumed. Inspect resource status before normal startup; explicitly resume paused tasks.

An occupied listen address, active task/Worker/role, queued turn, unresolved effect, unsealed delivery, corrupted database/manifest/bytes or unsafe link prevents the operation. Failed copies/staging files remain for inspection, without automatic deletion or overwriting. A partial backup without a valid manifest cannot be restored. After interrupted publication, retain the original directories and inspect published/staged data before completing recovery; do not start a partially restored workspace.

Evidence:`workspace/evidence/backup-delivery-20261012-v1.json`, task`c1c20801-10e7-4841-b64d-10ffd3590735`. Actual create/inspect/retain-original/restore preserved database history/resources/products, real checkpoint tables, three product files and session identity. Private source bytes stayed unchanged and the task stayed paused; zero inference/Jobs. Native checkpoint tables were initialized without a model run; a separate contract case proves committed nonempty WAL data roundtrip, not model execution recovery.

Initial Windows connection-timeout handling was replaced with an exclusive listen-address reservation. An empty checkpoint database's transient journal inventory was corrected. The operator demonstration initially retained SQLite handles; after that process exited, the same successful snapshot/task/products completed restoration without a new backup. Original failures and partial copies remain. Only new/failed backup cases were checked; frozen foundational/model trials were reused. Large-scale restoration, cross-device migration and installation/release remain in stage3.
