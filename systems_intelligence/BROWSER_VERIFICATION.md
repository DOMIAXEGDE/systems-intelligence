# Controller browser verification — 2026-09-13

Executed with the Codex in-app browser against the disposable workspace created
by `tools/controller_preview.py`. The user's configured application database was
not used. The fixture has a Unicode-capable context catalogue, a complete saved
chat turn, generator output, and a test skill that adds an example context.

| Workflow | Observed result |
| --- | --- |
| Select a context and chat together | Inspector returns the context, chat and turn; plot legend contains all three identities. |
| Change selections rapidly | Inspector and canvas retain the same latest selection after the request-order fix. |
| Inspect journal position 12 | Context revision 2 appears with the selected committed event, IDs, timestamp and causal metadata; the later chat is absent. |
| Export GIF and machine data | Both download links appear. Separate integration tests decode frames and verify order, timing, hashes and state models in the companion bundle. |
| Filter generator history | Provisional `generator.record` events remain visible even before the generator's final commit. |
| Inspect a causal run | The generator event's correlation ID can be used as the selection filter. |
| Draft and activate a skill | The reviewed version digest is shown and activation registers the skill command. |
| Run a supervised skill | A pending `context.append` proposal displays targets, payload, prior state, request ID and approved skill digest. |
| Approve the proposed action | Proposal becomes completed and its context is persisted. |
| Grant scoped autonomy and run | Receipt reports `status: executed`, one action, and the newly created test context. |
| Pause autonomy | Control changes to “Resume granted autonomy”; the test workspace remains paused. |
| Browser console | No JavaScript errors were recorded during the verified workflows. |

The first browser pass exposed two defects subsequently fixed: overlapping
selection requests could show a stale plot, and historical generator filters
omitted activity preceding the job's committed state. Both paths were exercised
again after the fixes.

To repeat: run the preview script with the built environment, open its printed
localhost URL, and follow the workflows above. Use the default example manifest
in the Controller skills panel. This is a local fixture, not an activation or
autonomy grant in the user's workspace.
