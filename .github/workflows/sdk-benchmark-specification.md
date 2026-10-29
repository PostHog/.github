# SDK benchmark specification v1

This is the shared workload contract for the Node, Python and .NET SDK PR benchmarks.
The [reporting contract](sdk-benchmark-report.md) defines the result schema and trusted
publication pipeline. SDK repositories own native execution, instrumentation and tests.

## Purpose and scope

Benchmarks detect changes between an SDK's merge-base revision and PR head. They are
informational, not performance merge gates or a cross-language ranking. Correctness,
build and execution failures remain failures. SDK runtime behavior and defaults are
not changed to implement this contract.

Each run uses the same head-side benchmark sources and fixtures for both SDK revisions,
separately built or installed through their supported public entry points. Full runs
use six paired repetitions, alternating which revision goes first. Revisions never
run concurrently on the same runner. Each workload/pass/repetition runs in a fresh process. Timing,
allocation instrumentation and memory observation use separate passes.

Producers implementing this contract set `suite.specification` to `sdk-benchmarks/v1`
and advance their existing suite version to 2. JSON result schema version remains 1.
Fixture identity describes benchmark-owned explicit inputs, not the SDK's public
options schema. SDK-build/dependency identity is separate from measurement environment.

## Workload inventory

The full profile has 20 workloads. `operations` counts public flag evaluations,
refreshes or capture calls as applicable; flushes are included in capture-delivery cost
but do not add to its operation denominator.

| Workload ID | Inputs / full operations | Required passes |
| --- | --- | --- |
| `flags.single.<case>` (nine cases below) | 10 definitions; 16,000 evaluations | timing, allocation |
| `flags.all.10` | 10 definitions; 1,600 evaluations | timing, allocation |
| `flags.all.100` | 100 definitions; 160 evaluations | timing, allocation |
| `flags.all.1000` | 1,000 definitions; 160 evaluations | timing, allocation |
| `flags.load.1000` | 1,000 definitions; 1,600 evaluations; concurrency 8 | memory |
| `flags.refresh.1000` | 1,000 definitions; 40 refreshes | memory |
| `capture.enqueue.32`, `capture.enqueue.4096` | 1,000 events; respective payload size | timing; allocation where the enqueue boundary supports it |
| `capture.drain.steady.32`, `capture.drain.steady.4096` | 1,000 events; explicit flush after every 100 captures | timing, allocation, memory |
| `capture.drain.burst.32`, `capture.drain.burst.4096` | 1,000 events; one explicit flush after all captures | timing, allocation, memory |

Allocation passes may use smaller documented flag-evaluation operation counts when
native instrumentation would otherwise create excessive overhead or trace files.
The actual count is recorded and used as the denominator. Capture-delivery passes
use the same 1,000 events across all runtimes and measurement passes.

The smoke profile uses 160 single-flag evaluations, 16 all-flags evaluations, 32 load
evaluations, four refreshes and 200 events for every capture workload. Steady smoke
runs flush at 100 and 200; burst smoke runs flush only at 200. Smoke is a correctness
check, not repeatability evidence.

Applicable workload parameters record `mode`, operation count, flag count/index,
concurrency and payload bytes. Delivery workloads additionally record
`drain_strategy` (`steady` or `burst`) and `flush_every` (100 for steady, total
operations for burst). Capture parameters record `completion_boundary` as described
below. Runtime-specific or non-applicable parameter fields are permitted; their
meaning must be documented and stable within a paired comparison.

## Flag fixtures and execution

The ordered fixture mix repeats every ten definitions:

1. `boolean`: unconditional active boolean.
2. `rollout`: 50% rollout.
3. `variant`: equal `control` and `test` variants.
4. `property`: person `plan` equals `pro`.
5. `regex`: person email matches `^(user|refresh-G)-[0-9]+@example[.]test$`, where G is the generation.
6. `cohort`: dynamic cohort 1, requiring `plan=pro` and `score` greater than -1 for even generations or 100 for odd generations.
7. `early`: matching plan condition followed by seven misses.
8. `late`: seven misses followed by the matching plan condition.
9. `no-match`: eight misses.
10. `inactive`: inactive unconditional flag, included in all-flags fixtures.

The nine single-flag cases are the first nine entries. Definition i has ID i+1 and
key/name `<case>-<i>`. Matching/missing groups use `plan=[pro]` / `plan=[never]`;
other groups have 100% rollout. Group-type mapping is empty. Cohort 1 is an AND
of its two person-property conditions. Cohort references use property type `cohort`,
key `id`, value 1. The boolean case is active only in even generations.

There are 16 users, `benchmark-user-0` through `benchmark-user-15`. User i has
`plan=pro` for even i and `free` otherwise, `email=user-i@example.test`, and numeric
`score=i`. Evaluation operation i selects user i modulo 16. Flags use local-only
public getters, with feature-flag-called events disabled. Correctness expectations
come from independent fixture logic, not the SDK evaluator being measured.

All measured flag workloads start with generation 0 loaded, including after warmup.
Refreshes load generations 1 through 40 (1 through 4 for smoke) through the public
reload API. Each refresh must perform a definitions request. Full result verification
for all fixture users occurs before and after measurement, not after every measured
refresh. The control suite separately verifies changing generations and reload results.

Load concurrency means at most eight active evaluations using the runtime's native
execution model: Node event-loop overlap, Python GIL-constrained threads, and .NET
ThreadPool tasks. It does not promise eight parallel CPU cores. Native warmup policies
may differ but must be recorded and identical between base and head.

## Capture fixtures and lifecycle

Each measured phase starts its event sequence at zero. Event i has:

- Event name: `benchmark`.
- Distinct ID: `benchmark-user-<i modulo 16>`.
- User-supplied properties: `payload` containing exactly 32 or 4,096 ASCII `x`
  characters, `plan` equal to `pro`, and integer `sequence` equal to i.

SDK-added metadata is allowed. Caller fixture construction is outside timed and
allocation intervals. Input ownership must prevent later calls from mutating an
already-submitted event's sequence or properties.

Use public options to disable compression and prevent count/time-based automatic
flushes during the workload. Set the automatic flush count to 100,000, queue capacity
to 100,001, and maximum count-based batch size to 100,000 where separately configurable.
Python's `flush_at` also determines its consumer's count-based batch size. These high
limits let the caller's explicit flush schedule define the scenario without an
incidental 100-event request cap changing the burst workload. Automatic polling/flush
intervals are disabled or exceed the worker deadline.
Native background consumers and size-based transport behavior remain active. No
private queue, evaluator, poller or transport internals may be replaced.

Steady and burst describe the **caller's explicit flush schedule**, not a required
number of HTTP requests or an assertion that all events remain queued. Both delivery
patterns finish with a public flush and use `completion_boundary=completed_delivery`.
The measured interval includes capture preparation, serialization and completed delivery
through the offline transport. Verification must not perform an extra flush that could
rescue incomplete measured delivery.

Enqueue-only workloads do not explicitly flush inside measurement. Their later flush
and delivery verification are outside measurement. Node waits for asynchronous
preparation/enqueue work and records `completion_boundary=prepared_enqueue`. Python
and .NET measure synchronous API return and record `completion_boundary=api_return`.
Python background preparation lacks a public enqueue-only completion signal, so its
enqueue CPU is caller-thread CPU and enqueue allocations remain explicitly unsupported.

Warmup events must be fully flushed and verified before measurement. Capture counters
and receiver validation state then reset outside measurement; measured sequence and
identity distribution must not depend on the runtime's warmup count.

Receivers validate exact payload, plan and sequence-to-identity mapping. Every expected
sequence must arrive exactly once: balanced loss/duplication must fail even when total
and per-user counts match. Delivery order is not required. Retain only bounded counters
and sequence-validation state, not payload history. Separate-process receivers may
perform validation outside the worker; in-process adapters must report their overhead
as part of measurement scope.

Node and .NET use public offline transport injection. Python uses a coordinator-owned
loopback HTTP receiver because it lacks that public injection seam. Consequently,
Python elapsed delivery time includes loopback transport, while receiver CPU and
allocations are excluded from worker measurements. These are intentional differences,
not comparable cross-language delivery costs.

## Measurement boundaries and names

- Timing uses a monotonic elapsed clock and process CPU. Enqueue-only Python uses
  caller-thread CPU instead. Report per-operation values with explicit methods; ns
  units do not imply nanosecond CPU-counter resolution.
- Allocation volume measures cumulative churn, including allocations freed during the
  pass, rather than heap growth. Node reports estimated V8 heap bytes, Python reports
  Python allocator requested bytes/calls, and .NET reports CLR managed allocation
  bytes. Native allocations outside those counters are excluded. Python allocator
  calls are not distinct object counts; Node/.NET allocation counts are unsupported.
- Memory starts after warmup and native settling. Observe checkpoints after each 32
  completed load evaluations, each refresh, or each 100 captured events. Include the
  final operation position when it is not already a checkpoint.
- Steady delivery checkpoints follow the scheduled flush. Burst checkpoints follow
  captures, before the final flush; a separate after-work snapshot covers completed
  delivery. Checkpoint operation positions must be unique and increasing.
- No forced GC occurs inside measured work or checkpoint callbacks. Natural GC remains
  enabled. Explicit settling/GC occurs before and after memory measurement; final
  correctness sweeps follow the recorded post-settling snapshot.
- Observed peak RSS is the maximum of before-work, checkpoint and after-work readings.
  It is not a continuously sampled or guaranteed workload maximum. Process-lifetime
  peak RSS is separate and includes initialization/warmup.

Use these metric IDs where supported:

| Meaning | ID |
| --- | --- |
| Elapsed time per operation | `elapsed_time_per_op` |
| Process CPU per operation | `process_cpu_time_per_op` |
| Caller-thread CPU per operation | `caller_thread_cpu_time_per_op` |
| Allocation volume per operation | `allocated_bytes_per_op` |
| Allocation count/calls per operation | `allocations_per_op` |
| Idle RSS | `idle_process_rss` |
| Observed workload peak RSS | `observed_workload_peak_rss` |
| Process-lifetime peak RSS | `process_lifetime_peak_rss` |
| Post-settling RSS | `post_settling_process_rss` |
| Post-settling managed heap | `post_settling_heap_used` |

Additional clearly scoped native metrics are allowed. Python's uninstrumented managed
heap size and unavailable OS counters remain unsupported with reasons, never zero.
Retained heap, RSS and allocation volume must not be described as interchangeable.

## Failures, CI and acceptance

Existing read-only every-PR execution and separately trusted publication remain in
place. Keep benchmark latency close to existing required-check completion, allowing
a modest additional wait rather than adding ten-plus minutes. Hosted measurements,
not concurrent local smoke runs, establish latency. Repository-specific safety
deadlines are 15 minutes per Node shard, 10 minutes per Python shard, 12 minutes
for serial .NET, and 5 minutes for shard aggregation. Measurement steps have shorter
deadlines to reserve artifact upload/reporting time: 12 minutes for Node, 8 minutes
for Python, and 10 minutes for .NET's build/measurement step. These are timeout
limits, not throughput targets; reassess workload assignment using measured hosted
evidence.

Node and Python use four independent runners for fixed workload groups:
`flags-single` (nine single-flag cases), `flags-scaling` (three all-flags sizes),
`flags-memory` (load and refresh), and `capture` (six enqueue/delivery cases).
The versioned head-side `shards.json` is an exact nonduplicated partition of full
and smoke inventories and participates in benchmark source identity. Each shard
keeps all six paired repetitions per workload, alternating revisions and using
fresh processes without same-host measurement concurrency. Shard IDs are recorded
in execution metadata, with a unique pair ID per shard. Suite metadata remains
identical across shards. .NET remains serial; unsharded execution stays supported.
Collection verifies coverage and preserves per-shard environments as defined in
the reporting contract. It does not merge measurements from different runners
into one environment or an aggregate ratio.

Workers have a 120-second deadline. Each coordinator, whether sharded or serial,
stops after two worker timeouts: finish the current pair, save partial diagnostics
and fail. Metadata commands that execute SDK lifecycle code also need bounded
execution. Runtime-native memory ceilings must be named accurately: a managed-heap
limit is not an RSS limit.

Controls must verify the full/smoke inventory and parameters, both explicit flush
schedules, fixture/sequence validation (including balanced duplicates), public lifecycle
completion, deterministic checkpoint positions, and failed-delivery detection without
a rescuing verification flush. Preserve existing counter, timeout and identity controls.

Acceptance requires native controls and paired smoke runs for all three implementations,
validation/rendering through the shared schema/reporter, and a cross-SDK inventory and
parameter comparison against this specification. Full six-pair runs validate execution
cost and the CI budget. Concurrent multi-SDK validation is correctness evidence only;
repeatability/calibration runs must use an otherwise idle host. Missing measurements,
failures and platform differences must remain visible in reports.
