"""Validate GIDE RO-Crate JSON-LD files against the GIDE SHACL profile.

Loads gide_shapes.ttl (with sh:severity for MUST/SHOULD/MAY) and validates
each crate individually. Produces a terminal summary and an HTML report.
"""

import json
import logging
import sys
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urljoin

from pyshacl import validate
from rdflib import Graph, Namespace
from rdflib.plugins.shared.jsonld import context as jsonld_context

HERE = Path(__file__).parent.resolve()
CRATES_DIR = HERE / "GIDE_crates"
SHAPES_FILE = HERE / "gide_shapes.ttl"
HTML_OUTPUT = HERE / "index.html"

RO_CRATE_CONTEXT_URL = "https://w3id.org/ro/crate/1.2/context"
_ro_crate_context_cache: dict | None = None

SH = Namespace("http://www.w3.org/ns/shacl#")

IGNORED_WARNINGS = {"ConjunctiveGraph is deprecated, use Dataset instead."}

logging.getLogger("rdflib").setLevel(logging.ERROR)


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class Finding:
    severity: str  # "Violation", "Warning", "Info"
    message: str
    focus_node: str
    path: str


@dataclass
class CrateResult:
    name: str
    publisher: str = "Unknown"
    dataset_id: str = ""
    findings: list[Finding] = field(default_factory=list)
    error: str | None = None

    @property
    def violations(self) -> int:
        return sum(1 for f in self.findings if f.severity == "Violation")

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == "Warning")

    @property
    def infos(self) -> int:
        return sum(1 for f in self.findings if f.severity == "Info")

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        if self.violations:
            return "fail"
        return "pass"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_ro_crate_context() -> dict:
    global _ro_crate_context_cache
    if _ro_crate_context_cache is None:
        import urllib.request

        with urllib.request.urlopen(RO_CRATE_CONTEXT_URL) as resp:
            _ro_crate_context_cache = json.loads(resp.read())
    return _ro_crate_context_cache


def _install_context_hook():
    original_fetch = jsonld_context.Context._fetch_context

    def _patched_fetch(self, source, base, referenced_contexts):
        source_url = urljoin(base or "", source)
        if source_url == RO_CRATE_CONTEXT_URL:
            return _get_ro_crate_context()
        return original_fetch(self, source, base, referenced_contexts)

    jsonld_context.Context._fetch_context = _patched_fetch
    return original_fetch


def extract_metadata(path: Path) -> tuple[str, str]:
    """Extract publisher name and dataset ID from JSON-LD without full RDF parsing."""
    publisher = "Unknown"
    dataset_id = ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        graph = data.get("@graph", [])
        # Build an index of @id -> entity
        by_id = {e["@id"]: e for e in graph if isinstance(e, dict) and "@id" in e}
        # Find the root Dataset
        for entity in graph:
            if isinstance(entity, dict) and "Dataset" in (entity.get("@type") or []):
                # Extract Dataset ID
                dataset_id = entity.get("@id", "")

                # Extract Publisher
                pub_ref = entity.get("publisher")
                if isinstance(pub_ref, dict) and "@id" in pub_ref:
                    pub_entity = by_id.get(pub_ref["@id"], {})
                    publisher = pub_entity.get("name", pub_ref["@id"])
                elif isinstance(pub_ref, str):
                    publisher = pub_ref
                break
    except Exception:
        pass
    return publisher, dataset_id


def parse_jsonld(path: Path) -> Graph:
    data = json.loads(path.read_text(encoding="utf-8"))
    base_uri = path.resolve().as_uri()
    g = Graph()
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("ignore")
        g.parse(data=json.dumps(data), format="json-ld", publicID=base_uri)
    return g


def extract_findings(results_graph: Graph) -> list[Finding]:
    findings: list[Finding] = []
    for result in results_graph.objects(predicate=SH.result):
        severity_uri = results_graph.value(result, SH.resultSeverity)
        if severity_uri == SH.Violation:
            sev = "Violation"
        elif severity_uri == SH.Warning:
            sev = "Warning"
        elif severity_uri == SH.Info:
            sev = "Info"
        else:
            continue
        msg = str(results_graph.value(result, SH.resultMessage) or "")
        focus = str(results_graph.value(result, SH.focusNode) or "")
        path = str(results_graph.value(result, SH.resultPath) or "")
        findings.append(Finding(severity=sev, message=msg, focus_node=focus, path=path))
    return findings


# ── HTML report ──────────────────────────────────────────────────────────────


def write_html_report(results: list[CrateResult], output_path: Path) -> None:
    total = len(results)
    n_violations = sum(r.violations for r in results)
    n_warnings = sum(r.warnings for r in results)
    n_infos = sum(r.infos for r in results)
    n_errors = sum(1 for r in results if r.error)
    crates_with_violations = sum(1 for r in results if r.violations)
    crates_with_warnings = sum(1 for r in results if r.warnings)
    crates_clean = sum(
        1 for r in results if r.status == "pass" and not r.warnings and not r.infos
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # For each finding message, collect which crates are missing it
    # message -> {severity, crates_missing (set of names)}
    msg_meta: dict[str, dict] = {}
    for r in results:
        seen_msgs: set[str] = set()
        for f in r.findings:
            if f.message in seen_msgs:
                continue
            seen_msgs.add(f.message)
            if f.message not in msg_meta:
                msg_meta[f.message] = {"severity": f.severity, "crates_missing": set()}
            msg_meta[f.message]["crates_missing"].add(r.name)

    # Order: Violation first, then Warning, then Info; within each group sort by coverage ascending
    sev_order = {"Violation": 0, "Warning": 1, "Info": 2}
    sorted_msgs = sorted(
        msg_meta.items(),
        key=lambda item: (
            sev_order[item[1]["severity"]],
            -len(item[1]["crates_missing"]),
        ),
    )

    valid_crates = total - n_errors

    def severity_badge(sev: str) -> str:
        cls = {"Violation": "violation", "Warning": "warning", "Info": "info"}
        return f'<span class="badge {cls[sev]}">{sev}</span>'

    rows = []
    for r in results:
        if r.error:
            status_html = '<span class="badge violation">ERROR</span>'
            detail = escape(r.error)
        else:
            parts = []
            if r.violations:
                parts.append(
                    f'<span class="badge violation">{r.violations} violation(s)</span>'
                )
            if r.warnings:
                parts.append(
                    f'<span class="badge warning">{r.warnings} warning(s)</span>'
                )
            if r.infos:
                parts.append(f'<span class="badge info">{r.infos} info(s)</span>')
            status_html = (
                " ".join(parts) if parts else '<span class="badge ok">OK</span>'
            )

            detail_parts = []
            for f in sorted(
                r.findings,
                key=lambda f: ("Violation", "Warning", "Info").index(f.severity),
            ):
                detail_parts.append(f"{severity_badge(f.severity)} {escape(f.message)}")
            detail = "<br>".join(detail_parts)

        # Prepare link to original resource if dataset_id looks like a URL
        link_html = ""
        if r.dataset_id.startswith("http"):
            link_html = f'<br><a href="{escape(r.dataset_id)}" target="_blank" style="font-size:.8rem">Original Resource ↗</a>'

        rows.append(
            f"""<tr class="crate-row" data-status="{r.status}"
            data-violations="{r.violations}" data-warnings="{r.warnings}" data-infos="{r.infos}"
            data-publisher="{escape(r.publisher)}">
            <td class="crate-name">{escape(r.name)}{link_html}</td>
            <td class="publisher-cell">{escape(r.publisher)}</td>
            <td>{status_html}</td>
            <td class="detail">{detail}</td></tr>"""
        )

    # Calculate valid crates per publisher (excluding errors)
    publisher_valid_counts = {}
    crate_publisher_map = {}
    valid_publishers = set()
    for r in results:
        if not r.error:
            valid_publishers.add(r.publisher)
            crate_publisher_map[r.name] = r.publisher
            publisher_valid_counts[r.publisher] = (
                publisher_valid_counts.get(r.publisher, 0) + 1
            )

    sorted_publishers = sorted(valid_publishers)

    # Coverage rows - also build a mapping of constraint -> crates missing it
    coverage_rows = []
    constraint_idx = 0
    constraint_crates_map: dict[int, list[str]] = {}

    for msg, meta in sorted_msgs:
        sev = meta["severity"]
        missing_crates = meta["crates_missing"]
        missing_crates_list = sorted(missing_crates)
        constraint_crates_map[constraint_idx] = missing_crates_list

        # Global stats
        missing = len(missing_crates)
        present = valid_crates - missing
        pct = (present / valid_crates * 100) if valid_crates else 0
        bar_color = {
            "Violation": "var(--violation)",
            "Warning": "var(--warning)",
            "Info": "var(--info)",
        }[sev]

        # Clickable global percentage cell
        global_pct_click = ""
        if missing > 0:
            global_pct_click = f' class="count clickable-pct" data-constraint="{constraint_idx}" data-pub="all" title="Click to filter crates missing this constraint"'
        else:
            global_pct_click = ' class="count"'

        # Per-publisher stats
        pub_cells = []
        for pub_idx, pub in enumerate(sorted_publishers):
            total_pub = publisher_valid_counts.get(pub, 0)
            if total_pub == 0:
                pub_cells.append('<td class="count">-</td>')
                continue

            missing_pub = sum(
                1 for c in missing_crates if crate_publisher_map.get(c) == pub
            )
            present_pub = total_pub - missing_pub
            pct_pub = present_pub / total_pub * 100

            # Highlight low coverage and make clickable
            style_parts = []
            if pct_pub < 100:
                if sev == "Violation":
                    style_parts.append("color: var(--violation); font-weight: bold;")
                elif sev == "Warning":
                    style_parts.append("color: var(--warning);")

            if missing_pub > 0:
                style_str = f' style="{" ".join(style_parts)}"' if style_parts else ""
                pub_cells.append(
                    f'<td class="count clickable-pct" data-constraint="{constraint_idx}" data-pub="{escape(pub)}" title="Click to filter {pub} crates missing this constraint"{style_str}>{pct_pub:.0f}%</td>'
                )
            else:
                style_str = f' style="{" ".join(style_parts)}"' if style_parts else ""
                pub_cells.append(f'<td class="count"{style_str}>{pct_pub:.0f}%</td>')

        pub_cells_html = "".join(pub_cells)

        coverage_rows.append(
            f"<tr><td>{severity_badge(sev)}</td>"
            f"<td>{escape(msg)}</td>"
            f'<td class="count">{present}/{valid_crates}</td>'
            f"<td{global_pct_click}>{pct:.0f}%</td>"
            f'<td><div class="bar-bg"><div class="bar-fill" style="width:{pct:.1f}%;background:{bar_color}"></div></div></td>'
            f"{pub_cells_html}"
            f"</tr>"
        )
        constraint_idx += 1

    # Build JSON map of constraint index -> crates missing
    constraint_crates_json = json.dumps(constraint_crates_map)

    # Dynamic header for coverage table
    pub_headers = "".join(
        f'<th style="text-align:right" title="{escape(p)}">{escape(p)[:15]}</th>'
        for p in sorted_publishers
    )
    coverage_thead = f'<thead><tr><th>Level</th><th>Constraint</th><th style="text-align:right">Have it</th><th style="text-align:right">%</th><th>Coverage</th>{pub_headers}</tr></thead>'

    # Per-publisher summary
    pub_stats: dict[str, dict] = {}
    for r in results:
        ps = pub_stats.setdefault(
            r.publisher, {"total": 0, "violations": 0, "warnings": 0, "infos": 0}
        )
        ps["total"] += 1
        ps["violations"] += r.violations
        ps["warnings"] += r.warnings
        ps["infos"] += r.infos

    pub_summary_rows = []
    for pub in sorted(pub_stats):
        ps = pub_stats[pub]
        pub_summary_rows.append(
            f"<tr><td>{escape(pub)}</td>"
            f'<td class="count">{ps["total"]}</td>'
            f'<td class="count">{ps["violations"]}</td>'
            f'<td class="count">{ps["warnings"]}</td>'
            f'<td class="count">{ps["infos"]}</td></tr>'
        )

    # Publisher list for dropdown
    publishers = sorted({r.publisher for r in results})
    pub_options = '<option value="all">All publishers</option>'
    for pub in publishers:
        pub_count = sum(1 for r in results if r.publisher == pub)
        pub_options += (
            f'<option value="{escape(pub)}">{escape(pub)} ({pub_count})</option>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>GIDE SHACL Validation Report</title>
<style>
  :root {{
    --bg: #f8f9fa; --card: #fff; --border: #dee2e6;
    --violation: #dc3545; --warning: #fd7e14; --info: #0d6efd; --ok: #198754;
    --text: #212529; --muted: #6c757d;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); padding: 1.5rem; }}
  h1 {{ font-size: 1.5rem; margin-bottom: .25rem; }}
  .timestamp {{ color: var(--muted); font-size: .85rem; margin-bottom: 1.5rem; }}
  .stats {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }}
  .stat-card {{
    background: var(--card); border: 1px solid var(--border); border-radius: .5rem;
    padding: .75rem 1.25rem; min-width: 140px; text-align: center;
  }}
  .stat-card .number {{ font-size: 1.8rem; font-weight: 700; }}
  .stat-card .label {{ font-size: .8rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }}
  .stat-card.violation .number {{ color: var(--violation); }}
  .stat-card.warning .number {{ color: var(--warning); }}
  .stat-card.info .number {{ color: var(--info); }}
  .stat-card.ok .number {{ color: var(--ok); }}

  .filters {{ margin-bottom: 1rem; display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }}
  .filters label {{ font-size: .85rem; color: var(--muted); }}
  .filters button {{
    border: 1px solid var(--border); background: var(--card); border-radius: .35rem;
    padding: .3rem .7rem; cursor: pointer; font-size: .85rem;
  }}
  .filters button.active {{ background: var(--text); color: #fff; border-color: var(--text); }}
  input[type="search"], select {{
    border: 1px solid var(--border); border-radius: .35rem; padding: .35rem .6rem;
    font-size: .85rem;
  }}
  input[type="search"] {{ width: 220px; }}
  .publisher-cell {{ white-space: nowrap; }}

  section {{ background: var(--card); border: 1px solid var(--border); border-radius: .5rem; margin-bottom: 1.5rem; overflow: hidden; }}
  section h2 {{ font-size: 1.1rem; padding: .75rem 1rem; border-bottom: 1px solid var(--border); background: var(--bg); }}

  table {{ width: 100%; border-collapse: collapse; font-size: .85rem; }}
  th {{ text-align: left; padding: .5rem .75rem; border-bottom: 2px solid var(--border); background: var(--bg); position: sticky; top: 0; }}
  td {{ padding: .45rem .75rem; border-bottom: 1px solid var(--border); vertical-align: top; }}
  .crate-name {{ font-family: monospace; white-space: nowrap; }}
  .detail {{ line-height: 1.7; }}
  .count {{ text-align: right; font-family: monospace; }}
  .clickable-pct {{ cursor: pointer; text-decoration: underline; }}
  .clickable-pct:hover {{ background: var(--bg); }}

  .badge {{
    display: inline-block; padding: .15rem .45rem; border-radius: .25rem;
    font-size: .75rem; font-weight: 600; color: #fff; vertical-align: middle;
  }}
  .badge.violation {{ background: var(--violation); }}
  .badge.warning {{ background: var(--warning); }}
  .badge.info {{ background: var(--info); }}
  .badge.ok {{ background: var(--ok); }}

  .bar-bg {{ background: #e9ecef; border-radius: .25rem; height: .9rem; min-width: 120px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: .25rem; transition: width .2s; }}

  tr.hidden {{ display: none; }}
</style>
</head>
<body>

<h1>GIDE SHACL Validation Report</h1>
<p class="timestamp">Generated {timestamp}</p>

<div class="stats">
  <div class="stat-card ok"><div class="number">{total}</div><div class="label">Total crates</div></div>
  <div class="stat-card ok"><div class="number">{crates_clean}</div><div class="label">Fully clean</div></div>
  <div class="stat-card violation"><div class="number">{n_violations}</div><div class="label">Violations (MUST)</div></div>
  <div class="stat-card warning"><div class="number">{n_warnings}</div><div class="label">Warnings (SHOULD)</div></div>
  <div class="stat-card info"><div class="number">{n_infos}</div><div class="label">Info (MAY)</div></div>
  <div class="stat-card"><div class="number">{n_errors}</div><div class="label">Errors</div></div>
</div>

<section>
<h2>Summary by publisher</h2>
<table>
<thead><tr><th>Publisher</th><th style="text-align:right">Crates</th><th style="text-align:right">Violations</th><th style="text-align:right">Warnings</th><th style="text-align:right">Info</th></tr></thead>
<tbody>{"".join(pub_summary_rows)}</tbody>
</table>
</section>

<section>
<h2>Property coverage across crates</h2>
<table id="coverage-table">
{coverage_thead}
<tbody>{"".join(coverage_rows)}</tbody>
</table>
</section>

<section>
<h2>Per-crate results</h2>

<div class="filters">
  <label>Show:</label>
  <button class="filter-btn active" data-filter="all">All ({total})</button>
  <button class="filter-btn" data-filter="fail">Violations ({crates_with_violations})</button>
  <button class="filter-btn" data-filter="warn">Warnings ({crates_with_warnings})</button>
  <button class="filter-btn" data-filter="clean">Clean ({crates_clean})</button>
  <select id="pub-filter">{pub_options}</select>
  <input type="search" id="search" placeholder="Search crate name...">
</div>

<table id="crate-table">
<thead><tr><th>Crate</th><th>Publisher</th><th>Status</th><th>Details</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
</section>

<script>
const rows = document.querySelectorAll('.crate-row');
const buttons = document.querySelectorAll('.filter-btn');
const search = document.getElementById('search');
const pubFilter = document.getElementById('pub-filter');
let activeFilter = 'all';
let constraintFilter = null; // stores constraint index and publisher filter

// Map of constraint index -> list of crate names missing that constraint
const constraintCrates = {constraint_crates_json};

function applyFilters() {{
  const q = search.value.toLowerCase();
  const pub = pubFilter.value;
  rows.forEach(row => {{
    const crateCell = row.querySelector('.crate-name');
    // Get the crate name from the first text node (before any <br> or links)
    const crateName = crateCell.childNodes[0].textContent.trim();
    const name = crateName.toLowerCase();
    const status = row.dataset.status;
    const v = parseInt(row.dataset.violations);
    const w = parseInt(row.dataset.warnings);
    const inf = parseInt(row.dataset.infos);
    let show = true;

    // Standard filters
    if (activeFilter === 'fail') show = v > 0 || status === 'error';
    else if (activeFilter === 'warn') show = w > 0;
    else if (activeFilter === 'clean') show = v === 0 && w === 0 && inf === 0 && status !== 'error';

    if (pub !== 'all' && row.dataset.publisher !== pub) show = false;
    if (q && !name.includes(q)) show = false;

    // Constraint filter from coverage table click
    if (constraintFilter !== null) {{
      const missingCrates = constraintCrates[constraintFilter.constraint] || [];
      const isMissing = missingCrates.includes(crateName);
      if (!isMissing) show = false;
      if (constraintFilter.pub !== 'all' && row.dataset.publisher !== constraintFilter.pub) show = false;
    }}

    row.classList.toggle('hidden', !show);
  }});
}}

function clearConstraintFilter() {{
  constraintFilter = null;
  const indicator = document.getElementById('constraint-filter-indicator');
  if (indicator) indicator.remove();
}}

buttons.forEach(btn => {{
  btn.addEventListener('click', () => {{
    buttons.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    activeFilter = btn.dataset.filter;
    clearConstraintFilter();
    applyFilters();
  }});
}});
search.addEventListener('input', () => {{ clearConstraintFilter(); applyFilters(); }});
pubFilter.addEventListener('change', () => {{ clearConstraintFilter(); applyFilters(); }});

// Handle clicks on coverage table percentage cells
document.querySelectorAll('.clickable-pct').forEach(cell => {{
  cell.addEventListener('click', (e) => {{
    const constraintIdx = parseInt(cell.dataset.constraint);
    const pub = cell.dataset.pub;

    // Set constraint filter
    constraintFilter = {{ constraint: constraintIdx, pub: pub }};

    // Reset other filters
    buttons.forEach(b => b.classList.remove('active'));
    buttons[0].classList.add('active');
    activeFilter = 'all';
    search.value = '';
    if (pub !== 'all') {{
      pubFilter.value = pub;
    }} else {{
      pubFilter.value = 'all';
    }}

    // Add visual indicator
    let indicator = document.getElementById('constraint-filter-indicator');
    if (!indicator) {{
      indicator = document.createElement('span');
      indicator.id = 'constraint-filter-indicator';
      indicator.style.cssText = 'background: var(--info); color: white; padding: .2rem .5rem; border-radius: .25rem; font-size: .8rem; margin-left: .5rem; cursor: pointer;';
      indicator.title = 'Click to clear constraint filter';
      indicator.addEventListener('click', () => {{ clearConstraintFilter(); applyFilters(); }});
      document.querySelector('.filters').appendChild(indicator);
    }}
    const constraintName = document.querySelectorAll('#coverage-table tbody tr')[constraintIdx]?.querySelector('td:nth-child(2)')?.textContent || 'constraint';
    indicator.textContent = '✕ Filtering: ' + (pub !== 'all' ? pub + ' - ' : '') + constraintName.substring(0, 40) + (constraintName.length > 40 ? '...' : '');

    // Scroll to per-crate results
    document.getElementById('crate-table').scrollIntoView({{ behavior: 'smooth', block: 'start' }});

    applyFilters();
  }});
}});
</script>

</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    if not CRATES_DIR.exists():
        sys.exit(f"Crates directory not found: {CRATES_DIR}")
    if not SHAPES_FILE.exists():
        sys.exit(f"Shapes file not found: {SHAPES_FILE}")

    crate_files = sorted(CRATES_DIR.glob("*-ro-crate-metadata.json"))
    if not crate_files:
        sys.exit("No RO-Crate metadata files found.")

    total = len(crate_files)
    print(f"Validating {total} crate(s) against {SHAPES_FILE.name} ...\n")

    original_fetch = _install_context_hook()

    shapes_graph = Graph()
    shapes_graph.parse(str(SHAPES_FILE), format="turtle")

    all_results: list[CrateResult] = []

    try:
        for i, crate_path in enumerate(crate_files, 1):
            name = crate_path.name
            pub, ds_id = extract_metadata(crate_path)
            cr = CrateResult(name=name, publisher=pub, dataset_id=ds_id)
            print(f"  [{i}/{total}] {name} ... ", end="", flush=True)

            try:
                data_graph = parse_jsonld(crate_path)
            except Exception as exc:
                cr.error = str(exc)
                all_results.append(cr)
                print("ERROR")
                continue

            try:
                _, results_graph, _ = validate(
                    data_graph,
                    shacl_graph=shapes_graph,
                    abort_on_first=False,
                    allow_infos=True,
                    allow_warnings=True,
                )
            except Exception as exc:
                cr.error = str(exc)
                all_results.append(cr)
                print("ERROR")
                continue

            cr.findings = extract_findings(results_graph)
            all_results.append(cr)

            parts = []
            if cr.violations:
                parts.append(f"{cr.violations} violation(s)")
            if cr.warnings:
                parts.append(f"{cr.warnings} warning(s)")
            if cr.infos:
                parts.append(f"{cr.infos} info(s)")
            print(", ".join(parts) if parts else "ok")
    finally:
        jsonld_context.Context._fetch_context = original_fetch

    # ── Aggregate ─────────────────────────────────────────────────────────
    total_violations = sum(r.violations for r in all_results)
    total_warnings = sum(r.warnings for r in all_results)
    total_infos = sum(r.infos for r in all_results)
    crates_with_violations = sum(1 for r in all_results if r.violations)
    crates_with_warnings = sum(1 for r in all_results if r.warnings)
    n_errors = sum(1 for r in all_results if r.error)
    clean = total - crates_with_violations - n_errors

    # ── Terminal summary ──────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  TOTAL CRATES       : {total}")
    print(f"  Clean (no issues)  : {clean}")
    print(
        f"  With violations    : {crates_with_violations}  ({total_violations} total)"
    )
    print(f"  With warnings      : {crates_with_warnings}  ({total_warnings} total)")
    print(
        f"  With info notices  : {sum(1 for r in all_results if r.infos)}  ({total_infos} total)"
    )
    print(f"  Parse errors       : {n_errors}")
    print(f"{'='*60}")

    if crates_with_violations:
        print(f"\n--- MUST violations ({crates_with_violations} crate(s)) ---\n")
        for r in all_results:
            if r.violations:
                print(f"  {r.name}: {r.violations} violation(s)")

    if crates_with_warnings:
        print(f"\n--- SHOULD warnings ({crates_with_warnings} crate(s)) ---\n")
        for r in all_results:
            if r.warnings:
                print(f"  {r.name}: {r.warnings} warning(s)")

    if n_errors:
        print(f"\n--- Parse / runtime errors ({n_errors}) ---\n")
        for r in all_results:
            if r.error:
                print(f"  {r.name}: {r.error}")

    # ── HTML report ───────────────────────────────────────────────────────
    write_html_report(all_results, HTML_OUTPUT)
    print(f"\nHTML report written to {HTML_OUTPUT}")

    sys.exit(1 if (crates_with_violations or n_errors) else 0)


if __name__ == "__main__":
    main()
