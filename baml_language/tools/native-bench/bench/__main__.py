"""baml-bench.

  python3 -m bench run <worktree> [--protocol protocols/tier-a.json] [--machine machines/bench-x86-v1.json] [--cli PATH] [--out DIR] [--previous DIR]
  python3 -m bench execute --stage DIR --machine FILE --protocol FILE --out DIR [--root DIR]     (on the measuring machine)
  python3 -m bench render <bundle> [--previous <bundle>]
  python3 -m bench compare <before> <after>
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import kpis, machine as machines, report, runner, stage
from .protocol import Machine, Protocol

ROOT = Path(__file__).resolve().parents[1]


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def cmd_run(a):
    protocol = Protocol.load(a.protocol)
    mach = Machine.load(a.machine)
    worktree = Path(a.worktree).resolve()
    cli = Path(a.cli) if a.cli else worktree / "baml_language" / "target" / "release" / "baml-cli"
    if not cli.exists():
        sys.exit(f"no emitter at {cli}; build it: cargo build --release -p baml_cli")
    sha = stage.git(worktree, "rev-parse", "HEAD")
    out = Path(a.out) if a.out else ROOT / "measurements" / "ci" / mach.name / f"{time.strftime('%Y-%m-%d', time.gmtime())}-{sha[:12]}"
    if out.exists():
        sys.exit(f"{out} exists; pass --out")
    out.mkdir(parents=True)
    tmp = Path(tempfile.mkdtemp(prefix="bench-stage."))
    st = tmp / "stage"
    print(f"staging {sha[:12]} for {mach.name} / {protocol.name}", file=sys.stderr)
    meta = stage.build_stage(worktree, cli, protocol, mach, st)
    (out / "stage.json").write_text(json.dumps(meta, indent=1))
    shutil.copytree(st / "native", out / "emitted", ignore=shutil.ignore_patterns("target"))
    code = machines.provider(mach).run(st, out)
    shutil.rmtree(tmp, ignore_errors=True)
    results = out / "results"
    manifest = {**meta}
    if (results / "manifest.json").exists():
        manifest.update(json.loads((results / "manifest.json").read_text()))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    if code != 0 or not (results / "summary.json").exists():
        print(f"execute failed with {code}; see {results / 'execute.log'}", file=sys.stderr)
        sys.exit(code or 1)
    print(report.card(out, a.previous))
    print(kpis.render(out, a.previous))
    (out / "summary.md").write_text(report.markdown(out, a.previous))
    print(out / "summary.md")


def cmd_execute(a):
    protocol = Protocol.load(a.protocol)
    mach = Machine.load(a.machine)
    root = Path(a.root).expanduser() if a.root else mach.bench_root
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    tools = {k: (Path(v).expanduser() if Path(v).expanduser().is_absolute() else root / v) for k, v in mach.data["tools"].items()}
    layout = runner.Layout(root=root, stage=Path(a.stage).resolve(), out=out, tools=tools)
    shutil.copy(protocol.path, out / "protocol.json")
    manifest = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "machine": mach.name, "protocol": protocol.name,
                "trials": protocol.trials, "cpu": protocol.cpu, "workers": protocol.workers,
                "loadavg_start": sh("cat /proc/loadavg"), "kernel": platform.release(), "cpu_model": sh("grep -m1 'model name' /proc/cpuinfo"),
                "nproc": os.cpu_count(), "rustc": sh("rustc -Vv"), "cargo": sh("cargo -V"), "glibc": sh("ldd --version | head -1"),
                "go": sh(f"{tools.get('go', 'go')} version"), "node": sh(f"{tools.get('node', 'node')} --version"),
                "python": sh(f"{tools.get('python', 'python3')} --version")}
    runner.log("execute", mach.name, protocol.name, "->", out)
    sha = runner.unpack_source(layout)
    manifest["candidate_sha"] = sha
    runner.freeze_control(layout, protocol.cases, mach)
    if layout.host.exists():
        manifest["host"] = {"sha256": runner.digest(layout.host), "bytes": layout.host.stat().st_size, **(mach.data.get("host") or {})}
        runner.compile_bytecode(layout, protocol.cases)
    else:
        runner.log("no VM host at", layout.host, "- VM rows are skipped")
    manifest["builds"] = runner.build_native(layout, protocol.cases, a.jobs)
    broken = [c for c, b in manifest["builds"].items() if b["status"] != "ok"]
    if broken and not a.keep_going:
        manifest["status"] = "native_build_failed"
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
        sys.exit(f"native build failed for {', '.join(broken)}; see build-<case>.log")
    manifest["artifacts"] = {case: {k: {"bytes": (layout.artifacts / case / k).stat().st_size, "sha256": runner.digest(layout.artifacts / case / k)}
                                    for k in ("program", "native", "native.stripped", "native-control", "rust", "go") if (layout.artifacts / case / k).exists()}
                             for case in protocol.cases}
    # the pinned host's bytecode compile time, written by compile_bytecode beside the program
    manifest["bytecode_compile_seconds"] = {case: float((layout.artifacts / case / "program.compile_seconds").read_text())
                                            for case in protocol.cases if (layout.artifacts / case / "program.compile_seconds").exists()} or None
    failures, skipped = runner.verify(layout, protocol, out)
    manifest["verify"] = {"failures": len(failures), "skipped_rows": skipped}
    if failures and not a.keep_going:
        manifest["status"] = "verify_failed"
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
        for f in failures[:20]:
            runner.log("FAIL", f["case"], f["row"], f["scenario"], f["status"], (f.get("observed_result") or f.get("error") or "")[:160])
        sys.exit(1)
    time.sleep(10)
    rows, skipped = runner.run(layout, protocol, out, a.rows.split(",") if a.rows else None)
    summary = runner.summarize(rows, protocol)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    if not rows:
        manifest["status"] = "no_trials"
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
        sys.exit(f"no trials ran; skipped rows: {skipped}")
    manifest.update(status="ok", finished=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), loadavg_end=sh("cat /proc/loadavg"),
                    trials_total=len(rows), trials_failed=sum(1 for r in rows if r["status"] != "ok"))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    runner.log("done:", manifest["trials_total"], "trials,", manifest["trials_failed"], "failed")


def main():
    p = argparse.ArgumentParser(prog="bench", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("worktree"); r.add_argument("--protocol", default=str(ROOT / "protocols/tier-a.json"))
    r.add_argument("--machine", default=str(ROOT / "machines/bench-x86-v1.json")); r.add_argument("--cli"); r.add_argument("--out"); r.add_argument("--previous")
    e = sub.add_parser("execute"); e.add_argument("--stage", required=True); e.add_argument("--machine", required=True); e.add_argument("--protocol", required=True)
    e.add_argument("--out", required=True); e.add_argument("--root"); e.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2))); e.add_argument("--rows"); e.add_argument("--keep-going", action="store_true")
    d = sub.add_parser("render"); d.add_argument("bundle"); d.add_argument("--previous")
    c = sub.add_parser("compare"); c.add_argument("before"); c.add_argument("after")
    o = sub.add_parser("kpis"); o.add_argument("bundle"); o.add_argument("--previous"); o.add_argument("--out")
    a = p.parse_args()
    if a.cmd == "run":
        cmd_run(a)
    elif a.cmd == "execute":
        cmd_execute(a)
    elif a.cmd == "render":
        print(report.card(a.bundle, a.previous)); print(report.markdown(a.bundle, a.previous))
    elif a.cmd == "compare":
        print(report.markdown(a.after, a.before))
    elif a.cmd == "kpis":
        print(kpis.render(a.bundle, a.previous, a.out))


if __name__ == "__main__":
    main()
