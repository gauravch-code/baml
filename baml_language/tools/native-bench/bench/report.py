"""Render a result bundle: card.html (charts), compare (delta with verdicts), markdown for a PR comment."""
import html
import json
import math
from pathlib import Path

from .rows import ORDER

NAMES = {"native": "BAML native", "native-off": "BAML native, telemetry off", "control": "control (frozen native)", "vm-off": "BAML VM, telemetry off", "vm-low": "BAML VM, low",
         "vm-medium": "BAML VM, medium", "vm-high": "BAML VM, high", "rust": "Rust", "go": "Go",
         "node": "Node", "bun": "Bun", "python": "Python"}
CASE_TITLES = {"00_startup": "Startup", "01_array_traversal": "Array traversal", "02_merge_sort": "Merge sort",
               "10_quick_sort": "Quick sort", "03_json_aggregate": "JSON aggregate",
               "04_allocation_retention": "Allocation and retention", "07_function_calls": "Function calls",
               "09_json_hello": "JSON hello", "11_generate_sort": "Generate and sort"}


class Bundle:
    def __init__(self, path):
        self.path = Path(path)
        self.manifest = json.loads((self.path / "manifest.json").read_text())
        self.summary = json.loads((self.path / "results" / "summary.json").read_text())
        for scen in self.summary.values():  # "native" and "native-off" are one row; the first bundle used the short name
            if "native" in scen and "native-off" not in scen:
                scen["native-off"] = scen["native"]
            if "native-off" in scen and "native" not in scen:
                scen["native"] = scen["native-off"]
        self.protocol = json.loads((self.path / "results" / "protocol.json").read_text()) if (self.path / "results" / "protocol.json").exists() else {}

    def scenarios(self):
        order = [s["name"] for s in self.protocol.get("scenarios", [])] or list(self.summary)
        return [s for s in order if s in self.summary]

    def case_of(self, scenario):
        for s in self.protocol.get("scenarios", []):
            if s["name"] == scenario:
                return s["case"]
        return "?"

    def cell(self, scenario, row):
        return self.summary.get(scenario, {}).get(row)

    def us(self, scenario, row, k="median"):
        c = self.cell(scenario, row)
        v = c and c.get("execution_wall_ns", {}).get("per_job", {}).get(k)
        return v / 1e3 if v is not None else None

    def rss(self, scenario, row):
        c = self.cell(scenario, row)
        return c and c.get("peak_rss_bytes", {}).get("median")


def fmt_us(v):
    if v is None:
        return "—"
    if v >= 100000:
        return f"{v / 1000:,.0f} ms"
    if v >= 1000:
        return f"{v / 1000:,.2f} ms"
    if v >= 10:
        return f"{v:,.1f} µs"
    return f"{v:.3f} µs"


def fmt_int(v):
    return "—" if v in (None, "") else f"{int(v):,}"


def fmt_mib(b):
    return "—" if b is None else f"{b / 2**20:.1f}"


def ratio(a, b):
    if a is None or b is None or b == 0:
        return "—"
    r = a / b
    return f"{r:.0f}×" if r >= 10 else f"{r:.1f}×" if r >= 1 else f"{r:.2f}×"


# ── verdicts ─────────────────────────────────────────────────────────────────

def verdicts(cur, prev, rule):
    """Per scenario: native change, control change (host drift), net, verdict."""
    thr = rule.get("threshold_percent", 5)
    out = []
    for scen in cur.scenarios():
        a, b = prev.us(scen, "native"), cur.us(scen, "native")
        if a is None or b is None:
            continue
        d = (b - a) / a * 100
        ca, cb = prev.us(scen, "control"), cur.us(scen, "control")
        drift = (cb - ca) / ca * 100 if ca and cb else None
        net = d - (drift or 0.0) if rule.get("net_of_control", True) else d
        disjoint = (cur.us(scen, "native", "min") > prev.us(scen, "native", "max")) or (cur.us(scen, "native", "max") < prev.us(scen, "native", "min"))
        significant = abs(net) > thr and (disjoint or not rule.get("require_disjoint_ranges", True))
        verdict = "unchanged" if not significant else ("faster" if net < 0 else "slower")
        out.append(dict(scenario=scen, before=a, after=b, delta=d, drift=drift, net=net, verdict=verdict))
    return out


# ── card ─────────────────────────────────────────────────────────────────────

STYLE = """
:root{--bg:#fbfaf7;--fg:#1f2420;--muted:#5e665f;--line:#e2e1da;--grid:#ebeae3;--accent:#2a78d6;--ink-bar:#8a8f98;--good:#1f6f4a;--bad:#b23b3b;
--font:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;--mono:"IBM Plex Mono",ui-monospace,Menlo,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#161a17;--fg:#e6e8e3;--muted:#9ba39c;--line:#2d332e;--grid:#242a26;--accent:#3987e5;--ink-bar:#646a73;--good:#7fc4a6;--bad:#e66767;color-scheme:dark}}
:root[data-theme=dark]{--bg:#161a17;--fg:#e6e8e3;--muted:#9ba39c;--line:#2d332e;--grid:#242a26;--accent:#3987e5;--ink-bar:#646a73;--good:#7fc4a6;--bad:#e66767;color-scheme:dark}
html,body{margin:0}body{background:var(--bg);color:var(--fg);font-family:var(--font);font-size:14px;line-height:1.5;padding-block:32px 64px;padding-inline:16px}
main{max-width:1320px;margin:0 auto}h1{font-size:1.5rem;font-weight:600;margin:0 0 4px;letter-spacing:-.01em}p.sub{color:var(--muted);margin:0 0 20px;max-width:90ch}
h2{font-size:.78rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:32px 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(640px,1fr));gap:18px 24px}@media (max-width:700px){.grid{grid-template-columns:1fr}}
.cell{min-width:0;overflow-x:auto}svg.chart{max-width:100%;height:auto;display:block}
.ttl{font:600 13px var(--font);fill:var(--fg)}.sub{font:11px var(--font);fill:var(--muted)}.tick,.val{font:11px var(--font);fill:var(--muted)}.lbl{font:11px var(--font);fill:var(--fg)}.val{font-family:var(--mono);font-variant-numeric:tabular-nums}
line.grid{stroke:var(--grid);stroke-width:1}rect.bar{fill:var(--ink-bar)}rect.bar-native{fill:var(--accent)}rect.bar-control{fill:none;stroke:var(--accent);stroke-width:1.5}line.range{stroke:var(--fg);stroke-width:1.5;opacity:.7}
.tbl{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.86rem}th,td{text-align:left;padding:5px 10px 5px 0;border-bottom:1px solid var(--line);vertical-align:top;white-space:nowrap}
th{font-weight:600;font-size:.74rem;letter-spacing:.04em;color:var(--muted)}td.n{font-family:var(--mono);font-size:.8rem;font-variant-numeric:tabular-nums;text-align:right}
td.good{color:var(--good);font-weight:500}td.bad{color:var(--bad);font-weight:500}td.muted{color:var(--muted)}p.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:.8rem;color:var(--muted);margin:0 0 8px}
.sw{display:inline-block;width:12px;height:12px;vertical-align:-1px;margin-right:6px;box-sizing:border-box}.env{font-family:var(--mono);font-size:.78rem;color:var(--muted);white-space:pre-wrap}
"""


def log_ticks(lo, hi):
    k = math.floor(math.log10(lo))
    ticks = []
    while 10 ** k <= hi * 1.0001:
        if 10 ** k >= lo * 0.9999:
            ticks.append(10 ** k)
        k += 1
    return ticks


def chart(b, scen):
    rows = [r for r in ORDER if b.us(scen, r) is not None and not (r == "native" and b.us(scen, "native-off") is not None)]
    if not rows:
        return ""
    vals = [(r, b.us(scen, r), b.us(scen, r, "min"), b.us(scen, r, "max"), b.cell(scen, r)["n"]) for r in rows]
    lo = min(v[2] for v in vals if v[2] > 0)
    hi = max(v[3] for v in vals)
    lo_axis, hi_axis = 10 ** math.floor(math.log10(lo)), 10 ** math.ceil(math.log10(hi * 1.15))
    W, LEFT, RIGHT, TOP, ROW, BAR = 640, 168, 92, 28, 24, 12
    H = TOP + ROW * len(vals) + 30
    plot_w = W - LEFT - RIGHT
    span = math.log10(hi_axis) - math.log10(lo_axis)
    x = lambda v: LEFT + (math.log10(v) - math.log10(lo_axis)) / span * plot_w
    o = [f'<svg class="chart" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="{html.escape(scen)}: execution wall per job">',
         f'<text class="ttl" x="{LEFT}" y="16">{html.escape(scen)}</text>',
         f'<text class="sub" x="{W - RIGHT}" y="16" text-anchor="end">wall µs per job, median of n, min–max, log axis</text>']
    for t in log_ticks(lo_axis, hi_axis):
        o.append(f'<line class="grid" x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{TOP}" y2="{H - 26}"/>')
        o.append(f'<text class="tick" x="{x(t):.1f}" y="{H - 12}" text-anchor="middle">{fmt_us(t).replace(".000", "").replace(".0 ", " ")}</text>')
    for i, (r, med, mn, mx, n) in enumerate(vals):
        y = TOP + i * ROW + ROW / 2
        cls = {"native": "bar-native", "control": "bar-control"}.get(r, "bar")
        o.append(f'<text class="lbl" x="{LEFT - 8}" y="{y + 4}" text-anchor="end">{html.escape(NAMES[r])}</text>')
        o.append(f'<rect class="{cls}" x="{x(lo_axis):.1f}" y="{y - BAR / 2:.1f}" width="{max(x(med) - x(lo_axis), 1):.1f}" height="{BAR}"><title>{html.escape(NAMES[r])}: median {fmt_us(med)}, min {fmt_us(mn)}, max {fmt_us(mx)}, n={n}</title></rect>')
        if mx > mn:
            o.append(f'<line class="range" x1="{x(mn):.1f}" x2="{x(mx):.1f}" y1="{y:.1f}" y2="{y:.1f}"/>')
        o.append(f'<text class="val" x="{x(mx) + 6:.1f}" y="{y + 4}">{fmt_us(med)}</text>')
    o.append("</svg>")
    return "\n".join(o)


def card(bundle_dir, previous=None):
    b = Bundle(bundle_dir)
    p = Bundle(previous) if previous else None
    m = b.manifest
    sha = (m.get("candidate_sha") or "?")[:12]
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
           f'<title>Native Bench {sha}</title>',
           '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">',
           f'<style>{STYLE}</style></head><body><main>',
           f'<h1>Native Bench {sha}</h1><p class="sub">{html.escape(m.get("candidate_subject", ""))}. Protocol {html.escape(m.get("protocol", "?"))} on {html.escape(m.get("machine", "?"))}: one pinned core, one worker, fresh process per trial, {m.get("trials", "?")} shuffled trials, oracle on every trial. Medians; ranges are observed min–max, not confidence intervals. Lower is better.</p>']
    out.append('<h2>Headline</h2><div class="tbl"><table><tr><th>scenario</th><th>native</th><th>VM medium</th><th>VM medium / native</th><th>Rust</th><th>native / Rust</th><th>Go</th><th>Go / native</th><th>Node</th><th>Node / native</th><th>RSS native MiB</th><th>RSS VM</th><th>RSS Go</th><th>fails</th></tr>')
    for scen in b.scenarios():
        nat, vmm = b.us(scen, "native"), b.us(scen, "vm-medium") or b.us(scen, "vm-off")
        fails = sum(c.get("failed", 0) for c in b.summary[scen].values())
        out.append(f'<tr><td>{html.escape(scen)}</td><td class="n">{fmt_us(nat)}</td><td class="n">{fmt_us(vmm)}</td><td class="n">{ratio(vmm, nat)}</td>'
                   f'<td class="n">{fmt_us(b.us(scen, "rust"))}</td><td class="n">{ratio(nat, b.us(scen, "rust"))}</td><td class="n">{fmt_us(b.us(scen, "go"))}</td><td class="n">{ratio(b.us(scen, "go"), nat)}</td>'
                   f'<td class="n">{fmt_us(b.us(scen, "node"))}</td><td class="n">{ratio(b.us(scen, "node"), nat)}</td><td class="n">{fmt_mib(b.rss(scen, "native"))}</td><td class="n">{fmt_mib(b.rss(scen, "vm-off"))}</td><td class="n">{fmt_mib(b.rss(scen, "go"))}</td><td class="n">{fails or ""}</td></tr>')
    out.append('</table></div>')
    if p:
        out.append(f'<h2>Against {html.escape((p.manifest.get("candidate_sha") or "?")[:12])}</h2><p class="sub">Δ control is the frozen native executable rerun on the same machine: host drift. A verdict needs the native change, net of drift, past the threshold with non-overlapping ranges.</p><div class="tbl"><table><tr><th>scenario</th><th>before</th><th>now</th><th>Δ native</th><th>Δ control</th><th>net</th><th>verdict</th></tr>')
        for v in verdicts(b, p, b.protocol.get("regression", {})):
            cls = {"faster": "good", "slower": "bad", "unchanged": "muted"}[v["verdict"]]
            out.append(f'<tr><td>{html.escape(v["scenario"])}</td><td class="n">{fmt_us(v["before"])}</td><td class="n">{fmt_us(v["after"])}</td><td class="n">{v["delta"]:+.1f}%</td><td class="n">{"—" if v["drift"] is None else f"{v["drift"]:+.1f}%"}</td><td class="n">{v["net"]:+.1f}%</td><td class="{cls}">{v["verdict"]}</td></tr>')
        out.append('</table></div>')
    out.append('<h2>Execution wall per job</h2><p class="legend"><span><span class="sw" style="background:var(--accent)"></span>BAML native, the candidate</span><span><span class="sw" style="border:1.5px solid var(--accent)"></span>control: frozen native, host drift</span><span><span class="sw" style="background:var(--ink-bar)"></span>other implementations, named on the row</span><span>hairline = min–max</span></p>')
    by_case = {}
    for scen in b.scenarios():
        by_case.setdefault(b.case_of(scen), []).append(scen)
    for case, scens in by_case.items():
        out.append(f'<h2>{html.escape(CASE_TITLES.get(case, case))}</h2><div class="grid">')
        out.extend(f'<div class="cell">{chart(b, s)}</div>' for s in scens)
        out.append('</div>')
    builds = m.get("builds", {})
    if builds:
        out.append('<h2>Native builds and artifacts</h2><div class="tbl"><table><tr><th>case</th><th>status</th><th>clean build s</th><th>stripped bytes</th><th>bytes</th><th>bytecode bytes</th></tr>')
        for case, bd in builds.items():
            art = m.get("artifacts", {}).get(case, {})
            if bd.get("status") == "ok":
                out.append(f'<tr><td>{case}</td><td>ok</td><td class="n">{bd.get("seconds", "—")}</td><td class="n">{fmt_int(bd.get("stripped_bytes"))}</td><td class="n">{fmt_int(bd.get("bytes"))}</td><td class="n">{fmt_int(art.get("program", {}).get("bytes"))}</td></tr>')
            else:
                out.append(f'<tr><td>{case}</td><td>{bd.get("status")}</td><td colspan="4">{html.escape(str(bd.get("reason", ""))[:160])}</td></tr>')
        out.append('</table></div>')
    keep = {k: m[k] for k in ("candidate_sha", "candidate_branch", "candidate_dirty_files", "machine", "host", "rustc", "go", "node", "python", "glibc", "kernel", "cpu", "loadavg_start", "loadavg_end", "protocol_sha256", "machine_sha256") if k in m}
    out.append('<h2>Provenance</h2><div class="env">' + html.escape(json.dumps(keep, indent=1)) + '</div></main></body></html>')
    path = Path(bundle_dir) / "card.html"
    path.write_text("\n".join(out))
    return path


def markdown(bundle_dir, previous=None):
    b = Bundle(bundle_dir)
    m = b.manifest
    lines = [f"## Native bench `{(m.get('candidate_sha') or '?')[:12]}` on {m.get('machine')}",
             "", "| scenario | native · off | VM · off | VM · medium | VM medium / native | Go | Go / native | Node / native |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for scen in b.scenarios():
        nat, vmo, vmm = b.us(scen, "native-off"), b.us(scen, "vm-off"), b.us(scen, "vm-medium")
        lines.append(f"| {scen} | {fmt_us(nat)} | {fmt_us(vmo)} | {fmt_us(vmm)} | {ratio(vmm, nat)} | {fmt_us(b.us(scen, 'go'))} | {ratio(b.us(scen, 'go'), nat)} | {ratio(b.us(scen, 'node'), nat)} |")
    lines += ["", "Per-job wall time, median of the trials; every BAML row names its telemetry level. Ratios above 1 mean native is faster."]
    if previous:
        p = Bundle(previous)
        vs = verdicts(b, p, b.protocol.get("regression", {}))
        n = {k: sum(1 for v in vs if v["verdict"] == k) for k in ("faster", "slower", "unchanged")}
        lines += ["", f"Against `{(p.manifest.get('candidate_sha') or '?')[:12]}`: **{n['faster']} faster, {n['slower']} slower, {n['unchanged']} unchanged** (net of control drift, threshold {b.protocol.get('regression', {}).get('threshold_percent', 5)}%)."]
        for v in vs:
            if v["verdict"] != "unchanged":
                lines.append(f"- {v['scenario']}: {fmt_us(v['before'])} → {fmt_us(v['after'])} ({v['delta']:+.1f}%, control {('%+.1f%%' % v['drift']) if v['drift'] is not None else '—'}) **{v['verdict']}**")
    return "\n".join(lines) + "\n"
