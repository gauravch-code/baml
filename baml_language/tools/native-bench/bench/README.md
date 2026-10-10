# baml-bench

The macro benchmark of the native backend: nine application-shaped BAML
programs, run as the VM at three telemetry levels, as native executables,
as a frozen native control, and as hand-written Rust, Go, Node, Bun and
Python, on one pinned core in fresh processes, every trial checked against
an independent oracle. One command, one result bundle, one card.

    python3 -m bench run <baml worktree>                # tier A on the Freestyle snapshot
    python3 -m bench run <worktree> --machine machines/local.json --protocol protocols/smoke.json
    python3 -m bench render <bundle> [--previous <bundle>]
    python3 -m bench compare <before> <after>

`run` needs the candidate's `baml-cli` built in release in the worktree
(`cargo build --release -p baml_cli`), the Freestyle key in
`~/.config/freestyle/api.key` for the Freestyle machine, and `cc` for the
observer. The package is standard library only, so the same files run on
the measuring machine.

## What a run does

1. **Stage** (here): emit `run` and `prepare` of every case through
   `baml __emit-rust` with the admission report kept; archive `baml_language`
   at HEAD; copy the cases, the protocol, the machine file and this package.
2. **Execute** (on the machine): unpack the source; freeze the snapshot's
   native executables as the control row; compile bytecode with the pinned
   VM host; build every native project from a clean target in release
   (the clean build time is a measurement); verify every row on every
   case's edge-case set against the oracle, one trial each, and stop on any
   failure; run the protocol's trials shuffled with its seed; summarize.
3. **Collect** (here): fetch `results/`, write `manifest.json`, render
   `card.html` and `summary.md`, delete the VM.

## The bundle

    <out>/
      manifest.json      candidate sha and branch, emitter, protocol and machine hashes, toolchains,
                         CPU, kernel, glibc, load before and after, every artifact's bytes and sha256,
                         native build seconds, verify and trial counts, status
      stage.json         what was emitted and why anything was not
      emitted/<case>/    the admission report, the emit log, the generated project
      results/
        trials.csv       every trial: status, timings, RSS, GC counters, telemetry bytes, hashes
        summary.json     per scenario x row: n, failed, median/min/max per metric, per job
        verify.csv       the failed verification trials, if any
        runtime/...      per-trial sample.json, events, usage, host snapshots
        execute.log
      card.html          the charts
      summary.md         the table and verdicts for a PR comment

Statuses other than `ok` on a trial are never dropped silently: they stay
in `trials.csv`, count as `failed` in the summary, and show on the card.

## Rules the code enforces

- A row whose artifact is missing is skipped and listed under
  `verify.skipped_rows`; a run with no trials fails.
- A native build failure fails the run. The candidate not compiling is the
  first thing the bench exists to notice.
- Telemetry rows must produce a recording and report `telemetry_ok` with
  zero delivery loss; off rows must produce none. Recordings are measured in
  bytes and deleted after each trial.
- A regression verdict needs the candidate's change, net of the control
  row's change on the same run, past `regression.threshold_percent` with
  disjoint min-max ranges. Everything else is `unchanged`.
- Results from different machine files are never compared.

## Files

    protocols/tier-a.json     the nightly matrix: 21 scenarios, 10 rows, 20 trials, seed 1729, cpu 3
    protocols/smoke.json      two scenarios, three trials, for plumbing
    machines/bench-x86-v1.json  the Freestyle snapshot and its guest layout
    machines/local.json       this machine; native rows only unless a host and references are present
