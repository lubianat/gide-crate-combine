"""Validate FBbi and NCBITaxon term usage across GIDE RO-Crate files.

Compares the names used in crate JSON-LD files against the canonical labels
from the FBbi OWL ontology and the NCBITaxon TSV hierarchy. Reports
case-insensitive mismatches and missing labels in an HTML report.

Expects the same directory layout as the companion scripts:
  ./GIDE_crates/*-ro-crate-metadata.json
  ./ontologies/raw/fbbi.owl
  ./ontologies/raw/ncbitaxon_hierarchy_wikidata.tsv
"""

from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
CRATES_DIR = ROOT / "GIDE_crates"
FBBI_OWL = ROOT / "ontologies" / "raw" / "fbbi.owl"
NCBITAXON_TSV = ROOT / "ontologies" / "raw" / "ncbitaxon_hierarchy_wikidata.tsv"
HTML_OUTPUT = ROOT / "ncbi_fbbi_usage.html"

OBO_BASE = "http://purl.obolibrary.org/obo/"

# XML / RDF namespaces (for parsing fbbi.owl)
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
OWL_NS = "http://www.w3.org/2002/07/owl#"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {"rdf": RDF_NS, "rdfs": RDFS_NS, "owl": OWL_NS}

# Regex for matching OBO URIs in JSON-LD values
FBBI_URI_RE = re.compile(r"http://purl\.obolibrary\.org/obo/(?:FBbi|FBBI)_(\d+)")
NCBI_URI_RE = re.compile(r"http://purl\.obolibrary\.org/obo/NCBITaxon_(\d+)")


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class TermUsage:
    """A single use of an ontology term inside a crate."""

    term_id: str  # e.g. "FBbi_00000243" or "NCBITaxon_9606"
    ontology: str  # "FBbi" or "NCBITaxon"
    crate_name: str  # filename of the crate
    crate_label: str  # name/label found in the crate for this term
    canonical_label: str  # label from the source ontology ("" if unknown)
    match: str  # "exact", "case_mismatch", "name_mismatch", "missing_in_ontology", "missing_in_crate"


@dataclass
class CrateSummary:
    """Aggregated results for one crate."""

    name: str
    publisher: str = "Unknown"
    usages: list[TermUsage] = field(default_factory=list)

    @property
    def mismatches(self) -> list[TermUsage]:
        return [u for u in self.usages if u.match not in ("exact",)]

    @property
    def n_exact(self) -> int:
        return sum(1 for u in self.usages if u.match == "exact")

    @property
    def n_case(self) -> int:
        return sum(1 for u in self.usages if u.match == "case_mismatch")

    @property
    def n_name(self) -> int:
        return sum(1 for u in self.usages if u.match == "name_mismatch")

    @property
    def n_missing_onto(self) -> int:
        return sum(1 for u in self.usages if u.match == "missing_in_ontology")

    @property
    def n_missing_crate(self) -> int:
        return sum(1 for u in self.usages if u.match == "missing_in_crate")


# ── Load canonical ontology labels ───────────────────────────────────────────


def load_fbbi_labels() -> dict[str, str]:
    """Parse fbbi.owl and return {normalised_id: label}.

    Normalised id uses the form "FBbi_NNNNNNN".
    """
    if not FBBI_OWL.exists():
        print(f"  WARNING: {FBBI_OWL} not found – FBbi labels will be empty")
        return {}

    tree = ET.parse(FBBI_OWL)
    root = tree.getroot()
    labels: dict[str, str] = {}

    for cls in root.findall(".//owl:Class", NS):
        about = cls.attrib.get(f"{{{RDF_NS}}}about", "")
        if not about.startswith(OBO_BASE):
            continue
        raw_id = about[len(OBO_BASE) :]  # e.g. "FBbi_00000243"
        # Normalise to FBbi_ prefix
        norm_id = re.sub(r"(?i)^fbbi_", "FBbi_", raw_id)

        label = ""
        for lbl in cls.findall("rdfs:label", NS):
            if not lbl.text:
                continue
            text = lbl.text.strip()
            if not text:
                continue
            lang = lbl.attrib.get(f"{{{XML_NS}}}lang", "")
            if lang.lower() == "en":
                label = text
                break
            if not label:
                label = text
        if label:
            labels[norm_id] = label

    return labels


def load_ncbitaxon_labels() -> dict[str, str]:
    """Parse the NCBITaxon TSV and return {taxon_id_digits: scientific_name}."""
    if not NCBITAXON_TSV.exists():
        print(f"  WARNING: {NCBITAXON_TSV} not found – NCBITaxon labels will be empty")
        return {}

    labels: dict[str, str] = {}

    def _clean(v: str) -> str:
        v = v.strip()
        if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            return v[1:-1]
        return v

    with NCBITAXON_TSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader, None)  # skip header
        for row in reader:
            if len(row) < 6:
                continue
            a_id = _clean(row[1])
            b_id = _clean(row[3])
            a_name = _clean(row[4])
            b_name = _clean(row[5])
            if a_id and a_name:
                labels.setdefault(a_id, a_name)
            if b_id and b_name:
                labels.setdefault(b_id, b_name)

    return labels


# ── Scan crate JSON-LD files ─────────────────────────────────────────────────


def _extract_publisher(graph: list[dict]) -> str:
    by_id = {e["@id"]: e for e in graph if isinstance(e, dict) and "@id" in e}
    for entity in graph:
        if not isinstance(entity, dict):
            continue
        etype = entity.get("@type", [])
        if isinstance(etype, str):
            etype = [etype]
        if "Dataset" in etype:
            pub_ref = entity.get("publisher")
            if isinstance(pub_ref, dict) and "@id" in pub_ref:
                pub_entity = by_id.get(pub_ref["@id"], {})
                return pub_entity.get("name", pub_ref["@id"])
            if isinstance(pub_ref, str):
                return pub_ref
    return "Unknown"


def _find_term_usages_in_entity(
    entity: dict,
    crate_name: str,
    fbbi_labels: dict[str, str],
    ncbi_labels: dict[str, str],
) -> list[TermUsage]:
    """Walk a single JSON-LD entity and find ontology term references."""
    usages: list[TermUsage] = []

    entity_id = entity.get("@id", "")
    entity_name = entity.get("name", entity.get("rdfs:label", ""))
    if isinstance(entity_name, dict):
        entity_name = entity_name.get("@value", "")
    if isinstance(entity_name, list):
        entity_name = entity_name[0] if entity_name else ""
        if isinstance(entity_name, dict):
            entity_name = entity_name.get("@value", "")
    entity_name = str(entity_name).strip()

    # Check if this entity's @id IS an ontology term
    fbbi_m = FBBI_URI_RE.fullmatch(entity_id)
    ncbi_m = NCBI_URI_RE.fullmatch(entity_id)

    if fbbi_m:
        digits = fbbi_m.group(1)
        term_id = f"FBbi_{digits}"
        canonical = fbbi_labels.get(term_id, "")
        match = _classify_match(entity_name, canonical)
        usages.append(
            TermUsage(
                term_id=term_id,
                ontology="FBbi",
                crate_name=crate_name,
                crate_label=entity_name,
                canonical_label=canonical,
                match=match,
            )
        )

    if ncbi_m:
        digits = ncbi_m.group(1)
        term_id = f"NCBITaxon_{digits}"
        canonical = ncbi_labels.get(digits, "")
        match = _classify_match(entity_name, canonical)
        usages.append(
            TermUsage(
                term_id=term_id,
                ontology="NCBITaxon",
                crate_name=crate_name,
                crate_label=entity_name,
                canonical_label=canonical,
                match=match,
            )
        )

    # Also scan all string values for references (e.g. in @id fields of
    # nested objects like {"@id": "http://purl.obolibrary.org/obo/FBbi_..."})
    # This catches references that aren't top-level entities.
    for key, val in entity.items():
        if key in ("@id", "@type", "@context"):
            continue
        _scan_value(
            val, crate_name, entity_name, fbbi_labels, ncbi_labels, usages, seen=set()
        )

    return usages


def _scan_value(
    val,
    crate_name: str,
    parent_name: str,
    fbbi_labels: dict[str, str],
    ncbi_labels: dict[str, str],
    usages: list[TermUsage],
    seen: set[str],
) -> None:
    """Recursively scan JSON values for ontology URI references in @id fields."""
    if isinstance(val, str):
        return
    if isinstance(val, dict):
        ref_id = val.get("@id", "")
        if isinstance(ref_id, str) and ref_id.startswith(OBO_BASE):
            if ref_id in seen:
                return
            seen.add(ref_id)
            # We only care about references; the entity itself will be found
            # at top-level. But some crates inline objects with @id + name.
            inline_name = val.get("name", val.get("rdfs:label", ""))
            if isinstance(inline_name, dict):
                inline_name = inline_name.get("@value", "")
            if isinstance(inline_name, list):
                inline_name = inline_name[0] if inline_name else ""
                if isinstance(inline_name, dict):
                    inline_name = inline_name.get("@value", "")
            inline_name = str(inline_name).strip() if inline_name else ""

            fbbi_m = FBBI_URI_RE.fullmatch(ref_id)
            ncbi_m = NCBI_URI_RE.fullmatch(ref_id)
            if fbbi_m and inline_name:
                digits = fbbi_m.group(1)
                term_id = f"FBbi_{digits}"
                canonical = fbbi_labels.get(term_id, "")
                match = _classify_match(inline_name, canonical)
                usages.append(
                    TermUsage(
                        term_id=term_id,
                        ontology="FBbi",
                        crate_name=crate_name,
                        crate_label=inline_name,
                        canonical_label=canonical,
                        match=match,
                    )
                )
            if ncbi_m and inline_name:
                digits = ncbi_m.group(1)
                term_id = f"NCBITaxon_{digits}"
                canonical = ncbi_labels.get(digits, "")
                match = _classify_match(inline_name, canonical)
                usages.append(
                    TermUsage(
                        term_id=term_id,
                        ontology="NCBITaxon",
                        crate_name=crate_name,
                        crate_label=inline_name,
                        canonical_label=canonical,
                        match=match,
                    )
                )
        # Recurse into nested dicts
        for v in val.values():
            _scan_value(
                v, crate_name, parent_name, fbbi_labels, ncbi_labels, usages, seen
            )
    elif isinstance(val, list):
        for item in val:
            _scan_value(
                item, crate_name, parent_name, fbbi_labels, ncbi_labels, usages, seen
            )


def _classify_match(crate_label: str, canonical_label: str) -> str:
    if not crate_label:
        return "missing_in_crate"
    if not canonical_label:
        return "missing_in_ontology"
    if crate_label == canonical_label:
        return "exact"
    if crate_label.lower() == canonical_label.lower():
        return "case_mismatch"
    return "name_mismatch"


def scan_crate(
    path: Path,
    fbbi_labels: dict[str, str],
    ncbi_labels: dict[str, str],
) -> CrateSummary:
    data = json.loads(path.read_text(encoding="utf-8"))
    graph = data.get("@graph", [])
    publisher = _extract_publisher(graph)
    summary = CrateSummary(name=path.name, publisher=publisher)

    # Deduplicate: track (term_id, crate_label) to avoid double-counting
    seen_pairs: set[tuple[str, str]] = set()

    for entity in graph:
        if not isinstance(entity, dict):
            continue
        for usage in _find_term_usages_in_entity(
            entity, path.name, fbbi_labels, ncbi_labels
        ):
            key = (usage.term_id, usage.crate_label)
            if key not in seen_pairs:
                seen_pairs.add(key)
                summary.usages.append(usage)

    return summary


# ── HTML report ──────────────────────────────────────────────────────────────

MATCH_LABELS = {
    "exact": ("Exact", "ok"),
    "case_mismatch": ("Case mismatch", "warning"),
    "name_mismatch": ("Name mismatch", "violation"),
    "missing_in_ontology": ("Not in ontology", "info"),
    "missing_in_crate": ("No name in crate", "warning"),
}


def _badge(match: str) -> str:
    label, cls = MATCH_LABELS.get(match, (match, "info"))
    return f'<span class="badge {cls}">{label}</span>'


def write_html_report(results: list[CrateSummary]) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    total_crates = len(results)
    total_usages = sum(len(r.usages) for r in results)
    total_exact = sum(r.n_exact for r in results)
    total_case = sum(r.n_case for r in results)
    total_name = sum(r.n_name for r in results)
    total_missing_onto = sum(r.n_missing_onto for r in results)
    total_missing_crate = sum(r.n_missing_crate for r in results)
    crates_with_issues = sum(1 for r in results if r.mismatches)

    # ── Global term mismatch index: term_id -> {canonical, usages_by_crate} ──
    term_index: dict[str, dict] = {}
    for r in results:
        for u in r.usages:
            if u.match == "exact":
                continue
            entry = term_index.setdefault(
                u.term_id,
                {"ontology": u.ontology, "canonical": u.canonical_label, "crates": []},
            )
            entry["crates"].append(
                {"crate": r.name, "crate_label": u.crate_label, "match": u.match}
            )

    # Sort: name_mismatch first, then case_mismatch, then others
    match_order = {
        "name_mismatch": 0,
        "case_mismatch": 1,
        "missing_in_crate": 2,
        "missing_in_ontology": 3,
    }

    sorted_terms = sorted(
        term_index.items(),
        key=lambda kv: (
            min(match_order.get(c["match"], 9) for c in kv[1]["crates"]),
            -len(kv[1]["crates"]),
            kv[0],
        ),
    )

    # ── Build term-level rows ──
    term_rows = []
    for term_id, info in sorted_terms:
        worst = min(info["crates"], key=lambda c: match_order.get(c["match"], 9))
        worst_badge = _badge(worst["match"])
        canonical_display = (
            escape(info["canonical"]) if info["canonical"] else "<em>—</em>"
        )
        crate_details = []
        for c in sorted(info["crates"], key=lambda c: c["crate"]):
            crate_details.append(
                f'{_badge(c["match"])} <code>{escape(c["crate"])}</code>: '
                f'"{escape(c["crate_label"])}"'
            )
        detail_html = "<br>".join(crate_details)
        term_rows.append(
            f'<tr data-ontology="{escape(info["ontology"])}">'
            f"<td><code>{escape(term_id)}</code></td>"
            f"<td>{escape(info['ontology'])}</td>"
            f"<td>{canonical_display}</td>"
            f"<td>{worst_badge}</td>"
            f'<td class="count">{len(info["crates"])}</td>'
            f'<td class="detail">{detail_html}</td>'
            f"</tr>"
        )

    # ── Build per-crate rows ──
    crate_rows = []
    for r in sorted(results, key=lambda r: (-len(r.mismatches), r.name)):
        if not r.mismatches:
            continue
        issue_parts = []
        for u in sorted(
            r.mismatches, key=lambda u: (match_order.get(u.match, 9), u.term_id)
        ):
            canonical_display = (
                f'"{escape(u.canonical_label)}"' if u.canonical_label else "—"
            )
            crate_display = f'"{escape(u.crate_label)}"' if u.crate_label else "—"
            issue_parts.append(
                f"{_badge(u.match)} <code>{escape(u.term_id)}</code>: "
                f"crate={crate_display} vs ontology={canonical_display}"
            )
        detail_html = "<br>".join(issue_parts)

        n_issues = len(r.mismatches)
        crate_rows.append(
            f'<tr data-publisher="{escape(r.publisher)}">'
            f'<td class="crate-name">{escape(r.name)}</td>'
            f"<td>{escape(r.publisher)}</td>"
            f'<td class="count">{n_issues}</td>'
            f'<td class="detail">{detail_html}</td>'
            f"</tr>"
        )

    # ── Publisher filter options ──
    publishers = sorted({r.publisher for r in results})
    pub_options = '<option value="all">All publishers</option>'
    for pub in publishers:
        pub_options += f'<option value="{escape(pub)}">{escape(pub)}</option>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>FBbi &amp; NCBITaxon Usage Report</title>
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
    padding: .75rem 1.25rem; min-width: 130px; text-align: center;
  }}
  .stat-card .number {{ font-size: 1.8rem; font-weight: 700; }}
  .stat-card .label {{ font-size: .75rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }}
  .stat-card.ok .number {{ color: var(--ok); }}
  .stat-card.violation .number {{ color: var(--violation); }}
  .stat-card.warning .number {{ color: var(--warning); }}
  .stat-card.info .number {{ color: var(--info); }}

  section {{ background: var(--card); border: 1px solid var(--border); border-radius: .5rem; margin-bottom: 1.5rem; overflow: hidden; }}
  section h2 {{ font-size: 1.1rem; padding: .75rem 1rem; border-bottom: 1px solid var(--border); background: var(--bg); }}

  .filters {{ padding: .5rem 1rem; display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; border-bottom: 1px solid var(--border); }}
  .filters label {{ font-size: .85rem; color: var(--muted); }}
  .filters button {{
    border: 1px solid var(--border); background: var(--card); border-radius: .35rem;
    padding: .3rem .7rem; cursor: pointer; font-size: .85rem;
  }}
  .filters button.active {{ background: var(--text); color: #fff; border-color: var(--text); }}
  select, input[type="search"] {{
    border: 1px solid var(--border); border-radius: .35rem; padding: .35rem .6rem; font-size: .85rem;
  }}
  input[type="search"] {{ width: 200px; }}

  table {{ width: 100%; border-collapse: collapse; font-size: .85rem; }}
  th {{ text-align: left; padding: .5rem .75rem; border-bottom: 2px solid var(--border); background: var(--bg); position: sticky; top: 0; }}
  td {{ padding: .45rem .75rem; border-bottom: 1px solid var(--border); vertical-align: top; }}
  .crate-name {{ font-family: monospace; white-space: nowrap; }}
  .detail {{ line-height: 1.7; }}
  .count {{ text-align: right; font-family: monospace; }}

  .badge {{
    display: inline-block; padding: .15rem .45rem; border-radius: .25rem;
    font-size: .75rem; font-weight: 600; color: #fff; vertical-align: middle;
  }}
  .badge.violation {{ background: var(--violation); }}
  .badge.warning {{ background: var(--warning); }}
  .badge.info {{ background: var(--info); }}
  .badge.ok {{ background: var(--ok); }}

  tr.hidden {{ display: none; }}

  .legend {{ padding: .75rem 1rem; font-size: .85rem; color: var(--muted); border-bottom: 1px solid var(--border); }}
  .legend .badge {{ margin-right: .3rem; }}
</style>
</head>
<body>

<h1>FBbi &amp; NCBITaxon — Ontology Term Usage Report</h1>
<p class="timestamp">Generated {timestamp}</p>

<div class="stats">
  <div class="stat-card ok"><div class="number">{total_crates}</div><div class="label">Crates scanned</div></div>
  <div class="stat-card"><div class="number">{total_usages}</div><div class="label">Term usages</div></div>
  <div class="stat-card ok"><div class="number">{total_exact}</div><div class="label">Exact matches</div></div>
  <div class="stat-card warning"><div class="number">{total_case}</div><div class="label">Case mismatches</div></div>
  <div class="stat-card violation"><div class="number">{total_name}</div><div class="label">Name mismatches</div></div>
  <div class="stat-card info"><div class="number">{total_missing_onto}</div><div class="label">Not in ontology</div></div>
  <div class="stat-card warning"><div class="number">{total_missing_crate}</div><div class="label">No name in crate</div></div>
  <div class="stat-card violation"><div class="number">{crates_with_issues}</div><div class="label">Crates with issues</div></div>
</div>

<!-- ── Section 1: Term-level view ──────────────────────────────────────── -->
<section>
<h2>Mismatched terms across all crates</h2>
<div class="legend">
  {_badge("name_mismatch")} Crate label differs from ontology &nbsp;
  {_badge("case_mismatch")} Differs only in letter case &nbsp;
  {_badge("missing_in_crate")} Term referenced but no name given in crate &nbsp;
  {_badge("missing_in_ontology")} Term ID not found in the source ontology file
</div>
<div class="filters">
  <label>Ontology:</label>
  <button class="onto-btn active" data-onto="all">All</button>
  <button class="onto-btn" data-onto="FBbi">FBbi</button>
  <button class="onto-btn" data-onto="NCBITaxon">NCBITaxon</button>
  <input type="search" id="term-search" placeholder="Search term ID or name…">
</div>
<div style="max-height:600px;overflow:auto">
<table id="term-table">
<thead><tr><th>Term ID</th><th>Ontology</th><th>Canonical label</th><th>Worst</th><th style="text-align:right">Crates</th><th>Details</th></tr></thead>
<tbody>{"".join(term_rows)}</tbody>
</table>
</div>
</section>

<!-- ── Section 2: Per-crate view ───────────────────────────────────────── -->
<section>
<h2>Crates with mismatches</h2>
<div class="filters">
  <label>Publisher:</label>
  <select id="pub-filter">{pub_options}</select>
  <input type="search" id="crate-search" placeholder="Search crate name…">
</div>
<div style="max-height:600px;overflow:auto">
<table id="crate-table">
<thead><tr><th>Crate</th><th>Publisher</th><th style="text-align:right">Issues</th><th>Details</th></tr></thead>
<tbody>{"".join(crate_rows)}</tbody>
</table>
</div>
</section>

<script>
// ── Term table filtering ──
const termRows = document.querySelectorAll('#term-table tbody tr');
const ontoBtns = document.querySelectorAll('.onto-btn');
const termSearch = document.getElementById('term-search');
let activeOnto = 'all';

function applyTermFilters() {{
  const q = termSearch.value.toLowerCase();
  termRows.forEach(row => {{
    let show = true;
    if (activeOnto !== 'all' && row.dataset.ontology !== activeOnto) show = false;
    if (q) {{
      const text = row.textContent.toLowerCase();
      if (!text.includes(q)) show = false;
    }}
    row.classList.toggle('hidden', !show);
  }});
}}
ontoBtns.forEach(btn => {{
  btn.addEventListener('click', () => {{
    ontoBtns.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    activeOnto = btn.dataset.onto;
    applyTermFilters();
  }});
}});
termSearch.addEventListener('input', applyTermFilters);

// ── Crate table filtering ──
const crateRows = document.querySelectorAll('#crate-table tbody tr');
const pubFilter = document.getElementById('pub-filter');
const crateSearch = document.getElementById('crate-search');

function applyCrateFilters() {{
  const pub = pubFilter.value;
  const q = crateSearch.value.toLowerCase();
  crateRows.forEach(row => {{
    let show = true;
    if (pub !== 'all' && row.dataset.publisher !== pub) show = false;
    if (q && !row.textContent.toLowerCase().includes(q)) show = false;
    row.classList.toggle('hidden', !show);
  }});
}}
pubFilter.addEventListener('change', applyCrateFilters);
crateSearch.addEventListener('input', applyCrateFilters);
</script>

</body>
</html>"""

    HTML_OUTPUT.write_text(html, encoding="utf-8")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    import sys

    if not CRATES_DIR.exists():
        sys.exit(f"Crates directory not found: {CRATES_DIR}")

    crate_files = sorted(CRATES_DIR.glob("*-ro-crate-metadata.json"))
    if not crate_files:
        sys.exit("No RO-Crate metadata files found.")

    print("Loading FBbi labels from fbbi.owl …")
    fbbi_labels = load_fbbi_labels()
    print(f"  {len(fbbi_labels)} FBbi terms loaded")

    print("Loading NCBITaxon labels from TSV …")
    ncbi_labels = load_ncbitaxon_labels()
    print(f"  {len(ncbi_labels)} NCBITaxon terms loaded")

    total = len(crate_files)
    print(f"\nScanning {total} crate(s) for ontology term usage …\n")

    results: list[CrateSummary] = []
    for i, path in enumerate(crate_files, 1):
        print(f"  [{i}/{total}] {path.name} … ", end="", flush=True)
        try:
            summary = scan_crate(path, fbbi_labels, ncbi_labels)
            results.append(summary)
            n = len(summary.usages)
            m = len(summary.mismatches)
            if m:
                print(f"{n} terms, {m} mismatch(es)")
            else:
                print(f"{n} terms, ok")
        except Exception as exc:
            print(f"ERROR: {exc}")
            results.append(CrateSummary(name=path.name))

    # ── Terminal summary ──
    total_usages = sum(len(r.usages) for r in results)
    total_mismatches = sum(len(r.mismatches) for r in results)
    crates_with_issues = sum(1 for r in results if r.mismatches)

    print(f"\n{'=' * 60}")
    print(f"  Total crates scanned : {total}")
    print(f"  Total term usages    : {total_usages}")
    print(f"  Exact matches        : {sum(r.n_exact for r in results)}")
    print(f"  Case mismatches      : {sum(r.n_case for r in results)}")
    print(f"  Name mismatches      : {sum(r.n_name for r in results)}")
    print(f"  Not in ontology      : {sum(r.n_missing_onto for r in results)}")
    print(f"  No name in crate     : {sum(r.n_missing_crate for r in results)}")
    print(f"  Crates with issues   : {crates_with_issues}")
    print(f"{'=' * 60}")

    if crates_with_issues:
        print(f"\n--- Crates with mismatches ({crates_with_issues}) ---\n")
        for r in sorted(results, key=lambda r: -len(r.mismatches)):
            if r.mismatches:
                print(f"  {r.name}: {len(r.mismatches)} issue(s)")
                for u in r.mismatches[:5]:
                    canonical = u.canonical_label or "—"
                    crate_lbl = u.crate_label or "—"
                    print(
                        f'    {u.term_id}: crate="{crate_lbl}" vs ontology="{canonical}" [{u.match}]'
                    )
                if len(r.mismatches) > 5:
                    print(f"    … and {len(r.mismatches) - 5} more")

    write_html_report(results)
    print(f"\nHTML report written to {HTML_OUTPUT}")

    sys.exit(1 if total_mismatches else 0)


if __name__ == "__main__":
    main()
