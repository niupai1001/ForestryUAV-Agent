# Revised implementation baseline · 1.4

This is an independent implementation, with no reuse or migration of the legacy ForestryUAV-Agent runtime. The supplied Chinese project book is the current requirements source; this companion revision shares its version number.

R01–R16 and U01–U08 remain in scope. Deliver the complete general agent and usable local developer UI before implementing the two real forestry workflows, followed by the six-arm evaluation, lower-resource validation and real input-scale testing of at least 100 GB. Early domain compatibility and public-data feasibility checks are permitted, without implementing domain features early.

Keep persistent role definitions and delegate only when isolation or independent work helps. Internally checked products without field truth are acceptable with explicit support and limitations; they do not establish external accuracy. A green vegetation proxy does not satisfy a required canopy metric. The user selects lower-resource hardware after the pilot; full V1 remains incomplete until it is frozen and measured.

Use one PydanticAI/Harness loop, native history, persistence and compaction. Application SQLite remains authoritative for tasks, grants, actions, jobs, evidence and acceptance. Persist an action/attempt before its effect. Ordinary messages do not revise the task specification; amendments do. Validate candidate products before acceptance, seal the final manifest and publish recoverably before the terminal task commit. A manifest does not recursively hash itself.

Use spatial_support_ref consistently. Implement only real external/persistent boundaries, without one repository or service for each record. WP describes implementation, AC describes acceptance and DOM maps to WP. Share execution, resources, events, verification and delivery between general and domain features.

Private-derived network payloads require scoped authorization. Public fetch rejects private/loopback destinations and unsafe redirects. HTML previews isolate scripts and network. Reject extra fields at control boundaries, keep sources read-only, issue user grants only through authenticated human routes, and confirm worker termination before final cancellation.

Freeze max_unchanged_decisions for new tasks. Persist observations per task revision, logical user turn and role across native runs and restarts. Repeated observations, failures and empty end-of-file windows stop with NO_PROGRESS when the limit is reached, preserving confirmed effects. Resume does not reset the counter. Authenticated corrective input may stop the old turn and start a new corrective turn, without resetting task-level budgets; changes to objectives, inputs, methods or constraints still require amendment. Novel observations do not establish semantic acceptance.

TaskConstraints.allowed_capabilities is a unique list of registered capabilities. Omission or null inherits configured ceilings; an empty list denies every registered action. Explicit scope constrains native tool discovery, execution, job submission/start and delegated role intersections. Ordinary messages and models cannot expand it. Native overflow readers only access this run's existing tool results and grant no new source, network or execution authority. Amendments omitting constraints preserve the previous scope; explicitly submitting {} inherits configured ceilings. The UI revises objectives, inputs and scope, pauses old execution, and requires reconciliation before resume.

Amendments retain unchanged output preferences, requested metrics and frozen fields. Control retries only return their original receipt. Pause/amendment/cancellation retries cannot cancel a newer run token; resume retries cannot create a new model turn.

Version 1.4 preserves the entire 1.3 scope and adds task capability scope, UI amendments and control retries. The application database remains at migration 2; existing specification JSON holds the constraint, without a new permission service or legacy project migration.

Batches: P0 baseline/feasibility; P1 real UI/model/file loop; P2 restricted execution/recovery; P3 complete general product; P4 imagery; P5 LiDAR; P6 frozen experiments/scale/release. Track implementation and verification separately. Docker, model tool round trips, search, compatible raw-elevation UAV LiDAR/terrain, ≥100 GB real data, lower-resource hardware and pilot release tolerances still require evidence.
