# Local authority verification — 2026-09-21

Scope: local workspace changes across SagaSmith-agent, Sagasmith-dnd and
sagasmith-core. Tests use temporary homes and campaigns. No user's campaign,
running configuration, provider settings or published release was changed.

| Validation | Result |
| --- | --- |
| Core complete suite | 408 passed, 19 skipped (427 collected) |
| DND Runtime complete suite | 95 passed; local recovery/attack/rest cases rerun after final changes |
| MCP affected contracts | 143 passed: stdio, tasks, context, transfers, attacks, reactions, transactions, render, random stream, Skills, catalog, NPC contracts |
| MCP configuration/media follow-up | 26 passed |
| MCP rest/Hit Dice/local stdio follow-up | 24 passed |
| Agent runner/subagent/domain context/metrics | 303 passed, 1079 unrelated tests deselected |
| Agent MCP/local stack/task integration | 179 passed |
| Agent final local stack/real stdio/metrics follow-up | 32 passed |
| Generated Runtime operation contract | `python -m sagasmith_dnd_runtime.publish --check` passed |
| Ruff and diff whitespace | Passed in all three repositories |

Groups overlap; counts must not be added as unique tests. The complete MCP corpus
was stopped before completion and replaced by the scoped contract suites above;
the full official-content corpus is not claimed as passing. Existing warnings
include Pydantic TypedDict configuration, SQLAlchemy's cyclic metadata ordering,
and enabling the GIL for the installed SQLAlchemy extension under Python 3.14.

New acceptance checks cover exclusive process ownership, process-death unlock,
refusing reuse of a released database, persisted Host operation identity, restart
after a lost transfer response, restore-response recovery, rejecting old-timeline
replay, player audience isolation, ordinary attack without model-supplied actor or
protocol fields, exactly reused attack rolls, nested rest revisions, and preserved
binary media through the local MCP adapter.

The reproducible [Host/stdio benchmark](local-authority-20260921.json) uses five
transfers and five binding queries, disposable data and no model. It verifies
both the final source quantity and target total. This is an absolute local sample,
not a before/after comparison or a real-provider latency acceptance test.

Not performed: real-provider long-campaign play, remote CI, package publication,
release-manifest promotion, or changing the user's installed/running stack.
See [workspace installation and recovery](../guides/local-first-dnd.md).
