# Local authority verification — 2026-09-22

These measurements use the actual Agent Host, stdio MCP transport and DND Runtime
with a disposable SQLite database. No LLM or existing campaign data is used.

| Measurement | Before | After, repeat |
| --- | ---: | ---: |
| Transfer SQL queries | 128 | 108 |
| Binding SQL queries | 36 | 19 |
| Empty-library cold connection | 2924.54 ms | 2932.11 ms |
| Transfer median, 5 calls | 53.61 ms | 55.33 ms |
| Binding median, 5 calls | 10.24 ms | 8.55 ms |

The first after-run was under concurrent test load: cold connection 4286 ms,
transfer median 64.31 ms and binding median 11.43 ms. All three raw samples are
retained: [before](local-authority-20260922-before.json),
[after](local-authority-20260922-after.json), and
[after repeat](local-authority-20260922-after-repeat.json). Query-count reduction
is established; transfer latency improvement is not. These tiny samples do not
support a throughput or LLM-turn speed claim.

The runtime reuses freshly derived post-commit bindings within a command and
returns authoritative inventory slices for both transfer participants. It does
not reuse authorization across commands or weaken revision/idempotency checks.

## Complete local library

The metadata-only DND lock covers ten expansions and one PHB dependency. The
original Tortle 1.0.2 archive failed runtime clause validation; an exact-hash
source-preserving local repair was required before real startup succeeded.
Generated content archives were not published. The benchmark requires an
explicit SRD skills path when mounting this library, preventing an empty-skills
fixture from being mistaken for a usable complete-content configuration.

The [first complete-library measurement](local-authority-20260922-official-library.json)
took 70.31 seconds to import content and connect; its three-call transfer median
was 51.05 ms and binding median 8.14 ms. This run predates eliminating the duplicate
PHB verification. First import must be distinguished from routine installed-content
restart and from the empty-library baseline above.

The [repeat with restart](local-authority-20260922-official-library-restart.json)
after removing duplicate PHB verification measured 68.36 seconds for first import
and 7.30 seconds for connection after restarting the same installed database.
Transfer medians were 49.76/49.67 ms; binding medians were 6.82/6.81 ms (three calls
each). This demonstrates installed-content reuse, not a controlled claim that
the verification change saved exactly 1.95 seconds. Every restart still verifies
the archive lock and rejects changed bytes.

Acceptance so far: real repaired Tortle Claws import, attacks, replay and restart;
four real archive scenarios for Artificer equipment, build-to-Defender, spell
choices/guards and SCAG Watcher Eye; one additional public protocol RNG scenario.
Runtime's 95 tests pass. These are bounded rule/build scenarios, not a completed
LLM campaign or certification of every listed 2014 mechanic.
