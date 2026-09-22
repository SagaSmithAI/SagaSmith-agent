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

## Larger actor sets

The [27-actor sample](local-authority-20260922-27-actors.json) uses `--extra-actors 25`
and five measured transfers. Median transfer was 80.39 ms, binding 7.56 ms and
empty-library connection 2.85 seconds. Transfers use 109 SQL queries (one more
than the earlier small-party version), while binding queries remain at 19.
The extra query reads only actor IDs and revisions. Full sheets are loaded only
for selected actors; an unrelated actor's revision still invalidates context.
A Core regression records zero Character ORM loads for the revision index and
one for a selected actor among twenty large sheets, excluding another campaign.
This is a bounded-load improvement, not evidence of a latency win at every party
size. The earlier measurements remain historical results, not silently replaced.

Real Tortle acceptance additionally passed both Shell Defense variants and the
one-hour Hold Breath lifecycle. Signed Host bridge creation and SEP-2663 task
completion passed over real stdio after fixing bootstrap campaign scope.

## Independent release-lock installation

A fresh `dnd-only` installation cloned the pinned Core and D&D commits into a
separate state root and installed the minimal dependency set with Python 3.12.
The [installed-environment sample](local-authority-20260922-installed-library.json)
then exercised the real stdio server with the complete repaired local library:
first import 75.16 seconds, restart 6.99 seconds, transfer medians 49.66/47.10 ms,
and binding medians 6.65/6.06 ms (three calls per process). No LLM or user campaign
data was used. Installation and doctor now accept the single local authority
while rejecting drift in its principal, session policy, or authentication environment.

The HTTP readiness deadline is 180 seconds because first content installation can
exceed the previous 35-second limit. Child exits still fail immediately; this does
not skip content verification or make the first import faster.

## Ordinary-text reference fast path

Profiling the same installed Python environment and library, with only the Runtime
source changed, found 326,914,202 string replacements during first import. Most
strings contained no reference marker. Import/export now return those strings
directly while preserving exact chunk-key lookup and the existing substitution
path for actual references. The [profile summary](local-authority-20260922-startup-profile.json)
records 23,658,239 replacements afterward (92.8% fewer), and `localize` cumulative
time falling from 62.49 to 6.05 seconds. These timings include profiler overhead;
validation and archive checksum checks remain enabled.

The [unprofiled sample](local-authority-20260922-text-fastpath.json) measured first
connection at 47.65 seconds and restart at 7.12 seconds. Earlier complete-library
samples were 68–75 seconds. These samples were not isolated timing trials, so the
call-count reduction is stronger evidence than a precise latency percentage.
The content import/export/actor round trip, real Tortle Claws import/attack/replay/
restart, and all 95 Runtime tests passed after the change.

A separate disposable offline recovery exercise created data through real stdio,
backed it up, added another campaign, restored all 11 original files byte-for-byte,
then reopened stdio and completed another transfer. This covers closed-connection
recovery, not hot backup or detection of every externally launched stdio process.
