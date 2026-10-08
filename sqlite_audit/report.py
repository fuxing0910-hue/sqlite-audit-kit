"""A portable, script-free HTML report; all database text is escaped."""

from __future__ import annotations

from html import escape
import json
from typing import Any


def _text(value: Any) -> str:
    return escape(str(value), quote=True)


def _number(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


def _change(value: int | None) -> str:
    return "—" if value is None else f"{value:+,}"


def _rate(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _distribution(value: dict[str, int] | None) -> str:
    if value is None:
        return "—"
    parts = [f'<span class="type">{_text(name)} <b>{count:,}</b></span>'
             for name, count in value.items() if count]
    return " ".join(parts) if parts else '<span class="muted">empty</span>'


def _chart(tables: list[dict[str, Any]]) -> str:
    selected = sorted(tables, key=lambda table: -max(table["rows"]["before"] or 0, table["rows"]["after"] or 0))[:12]
    height = 54 + 54 * len(selected)
    maximum = max((max(item["rows"]["before"] or 0, item["rows"]["after"] or 0) for item in selected), default=1) or 1
    fragments = [f'<svg viewBox="0 0 900 {height}" role="img" aria-labelledby="chart-title">',
                 '<title id="chart-title">Table row counts before and after; first twelve tables by size</title>',
                 '<rect x="218" y="10" width="12" height="12" rx="3" fill="#8193a7"/>',
                 '<text x="237" y="20">Before</text>',
                 '<rect x="316" y="10" width="12" height="12" rx="3" fill="#35ccb0"/>',
                 '<text x="335" y="20">After</text>']
    for index, table in enumerate(selected):
        y = 48 + index * 54
        name = table["table"]
        label = name if len(name) <= 25 else name[:24] + "…"
        fragments.append(f'<text x="8" y="{y + 18}"><title>{_text(name)}</title>{_text(label)}</text>')
        for offset, key, color in ((0, "before", "#8193a7"), (20, "after", "#35ccb0")):
            count = table["rows"][key]
            width = ((count or 0) / maximum) * 530
            fragments.append(f'<rect x="218" y="{y + offset}" width="{width:.2f}" height="15" rx="3" fill="{color}"/>')
            fragments.append(f'<text x="{225 + width:.2f}" y="{y + offset + 12}">{_number(count)}</text>')
    if not selected:
        fragments.append('<text x="8" y="48">No supported user tables in either snapshot</text>')
    return "".join(fragments) + "</svg>"


def render_report(comparison: dict[str, Any]) -> str:
    """Render comparison data as one HTML file without network requests."""
    status = comparison["gate"]["status"]
    regressions = comparison["regressions"]
    findings = "".join(f'<li>{_text(item["message"])}</li>' for item in regressions)
    findings = f'<ul class="findings">{findings}</ul>' if findings else '<p>No counted FK/key regression or failed after integrity check was found.</p>'
    incomplete = "".join(f'<li>{_text(item)}</li>' for item in comparison["gate"]["incomplete_reasons"])
    if incomplete:
        findings += f'<div class="notice"><b>Incomplete coverage</b><ul>{incomplete}</ul></div>'
    fk = comparison["foreign_keys"]
    cards = f'''<div class="cards">
      <div><span>Supported tables</span><strong>{len(comparison['tables'])}</strong><small>Union of before / after</small></div>
      <div><span>Schema changes</span><strong>{len(comparison['summary']['schema_changed_tables'])}</strong><small>Tables with structural or DDL changes</small></div>
      <div><span>Foreign-key violations</span><strong>{_number(fk['before']['violation_count'])} <i>→</i> {_number(fk['after']['violation_count'])}</strong><small>Exact counts in supported tables</small></div>
      <div><span>Regression findings</span><strong>{len(regressions)}</strong><small>Defined checks, no health score</small></div>
    </div>'''
    sections = []
    for index, table in enumerate(comparison["tables"]):
        metrics = []
        for column in table["columns"]:
            nulls = column["nulls"]
            metrics.append(f'''<tr><th scope="row">{_text(column['name'])}</th>
              <td>{_number(nulls['before'])} <span class="muted">→</span> {_number(nulls['after'])}<small>Δ {_change(nulls['delta'])}</small></td>
              <td>{_rate(column['null_rate_before'])} <span class="muted">→</span> {_rate(column['null_rate_after'])}</td>
              <td>{_distribution(column['types_before'])}<small>→ {_distribution(column['types_after'])}</small></td></tr>''')
        keys = []
        for key in table["keys"]:
            groups, extra, excluded = key["duplicate_groups"], key["extra_rows"], key["excluded_null_rows"]
            keys.append(f'''<tr><th scope="row">{_text(', '.join(key['columns']))}</th>
              <td>{_number(groups['before'])} → {_number(groups['after'])}</td>
              <td>{_number(extra['before'])} → {_number(extra['after'])}</td>
              <td>{_number(excluded['before'])} → {_number(excluded['after'])}</td></tr>''')
        key_section = f'''<h3>Selected key checks</h3><div class="scroll"><table><thead><tr><th>Columns</th><th>Duplicate groups</th><th>Excess rows</th><th>Rows excluded: any NULL</th></tr></thead><tbody>{''.join(keys)}</tbody></table></div>''' if keys else '<p class="muted">No candidate key was selected for this table.</p>'
        schema = ""
        if table["schema_changes"]:
            schema = f'''<details><summary>Inspect {len(table['schema_changes'])} schema changes</summary><pre>{_text(json.dumps(table['schema_changes'], indent=2, ensure_ascii=False))}</pre></details>'''
        rows = table["rows"]
        sections.append(f'''<section class="panel" id="table-{index}">
          <div class="table-title"><h2>{_text(table['table'])}</h2><span class="badge">{_text(table['status'])}</span></div>
          <p>Rows <b>{_number(rows['before'])} → {_number(rows['after'])}</b> <span class="muted">· Δ {_change(rows['delta'])}</span></p>
          <div class="scroll"><table><thead><tr><th>Column</th><th>NULL count</th><th>NULL share</th><th>Actual storage types: before → after</th></tr></thead><tbody>{''.join(metrics)}</tbody></table></div>
          {key_section}{schema}</section>''')
    diagnostics = {"integrity": comparison["integrity"], "foreign_keys": comparison["foreign_keys"],
                   "unsupported_tables": comparison["unsupported_tables"]}
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
<title>SQLite Audit Kit · Snapshot comparison</title><style>
:root{{--bg:#0b1420;--panel:#142131;--line:#2a3b4d;--text:#e9f1f8;--muted:#9bacc0;--accent:#35ccb0}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif}}main{{max-width:1180px;margin:auto;padding:44px 24px 70px}}header{{margin-bottom:28px}}.eyebrow{{letter-spacing:.15em;text-transform:uppercase;color:var(--accent);font-size:12px;font-weight:700}}h1{{font-size:clamp(28px,4vw,44px);line-height:1.2;margin:12px 0}}h2{{font-size:22px;margin:0;overflow-wrap:anywhere}}h3{{font-size:16px;margin:25px 0 8px}}p{{margin:10px 0}}.lede{{color:var(--muted);max-width:830px}}.panel{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:24px;margin-top:22px}}.cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}}.cards>div{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:19px}}.cards span,.cards small{{display:block;color:var(--muted)}}.cards strong{{display:block;font-size:30px;margin:4px 0}}.cards i{{font-size:20px;font-style:normal;color:var(--muted)}}small{{display:block;font-size:12px;color:var(--muted)}}.muted{{color:var(--muted)}}.status,.badge{{display:inline-block;border-radius:30px;background:#22364a;padding:4px 12px;font-size:12px;font-weight:700;white-space:nowrap}}.status.regression{{background:#572836;color:#ffbac7}}.status.pass{{background:#173e38;color:#8ff0d9}}.status.incomplete{{background:#4a3b1e;color:#f8db96}}.findings{{padding-left:24px}}.findings li{{margin:6px 0;color:#ffbac7}}.notice{{border-left:3px solid #e7be6f;padding:8px 16px;background:#282a29;margin:16px 0}}.notice ul{{margin:8px 0;padding-left:22px}}.table-title{{display:flex;align-items:center;gap:14px;flex-wrap:wrap}}.scroll{{overflow:auto;margin-top:15px}}table{{width:100%;border-collapse:collapse;text-align:left;font-size:13px}}th,td{{padding:12px;border-bottom:1px solid var(--line);vertical-align:top}}thead th{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}}tbody th{{font-weight:600;overflow-wrap:anywhere}}td{{font-variant-numeric:tabular-nums}}.type{{display:inline-block;margin:2px 4px 2px 0;color:var(--muted);font-size:11px}}.type b{{color:var(--text);font-weight:500}}details{{margin-top:20px}}summary{{cursor:pointer;color:var(--accent)}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.7 ui-monospace,Consolas,monospace;background:#0c1725;padding:16px;border-radius:8px}}svg{{width:100%;height:auto;display:block;margin-top:18px}}svg text{{fill:var(--muted);font:13px system-ui,sans-serif}}footer{{color:var(--muted);font-size:12px;margin-top:30px}}@media(max-width:800px){{.cards{{grid-template-columns:repeat(2,minmax(0,1fr))}}main{{padding:26px 14px}}.panel{{padding:18px}}}}@media print{{:root{{--bg:white;--panel:white;--text:#172533;--muted:#45586c;--line:#bcc8d4}}main{{padding:0}}.panel{{break-inside:avoid}}}}
</style></head><body><main>
<header><div class="eyebrow">SQLite Audit Kit / full scan</div><h1>What changed in your database?</h1><p class="lede">A reproducible comparison of schema, exact counts and selected constraints. Row changes, NULL changes and mixed SQLite storage types are observations; they do not automatically mean a regression.</p></header>
{cards}<section class="panel"><div class="table-title"><h2>Regression gate</h2><span class="status {_text(status)}">{_text(status.upper())}</span></div>{findings}<p class="muted">Fails on increased FK violations per table, increased selected-key duplicate counts, or a failed after integrity check. Different key coverage or unsupported tables prevent a complete pass.</p></section>
<section class="panel"><h2>Row counts at a glance</h2><p class="muted">Up to twelve largest tables. Missing tables use an em dash; a zero is an empty table.</p>{_chart(comparison['tables'])}</section>
{''.join(sections)}<section class="panel"><details><summary>SQLite diagnostics and audit scope</summary><pre>{_text(json.dumps(diagnostics, indent=2, ensure_ascii=False))}</pre></details></section>
<footer>Snapshot format v1 · Exact measurements inside read-only transactions · No record payloads are exported · Generated by SQLite Audit Kit · Offline report with no scripts, trackers or external resources.</footer>
</main></body></html>'''
