"""Report builder — generates professional HTML reports from structured components.

Components:
  - summary_cards: Key metric cards (e.g. Total Revenue, Order Count)
  - table: Interactive table with search, column filters, sorting, pagination
  - chart: Chart.js chart (bar, line, doughnut, pie, etc.)
  - text: Markdown text section (rendered via marked.js)

Usage:
    components = [
        {"type": "summary_cards", "cards": [{"label": "Revenue", "value": "Rs. 50,000", "icon": "💰"}]},
        {"type": "chart", "id": "rev-chart", "chart_type": "line", "data": {...}, "options": {...}},
        {"type": "table", "id": "orders-tbl", "title": "Orders", "columns": [...], "rows": [...]},
        {"type": "text", "content": "## Notes\\nSome markdown here."},
    ]
    html = build_report_html("Sales Report", components, subtitle="March 2026")
"""
import json
from datetime import datetime


# ── HTML Template ────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — OmniDesk Report</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
    background: #f0f2f5; color: #1a1a2e; line-height: 1.6;
    max-width: 1100px; margin: 0 auto; padding: 24px 16px;
  }}

  /* ── Header ── */
  .rpt-header {{
    background: linear-gradient(135deg, #1a73e8 0%, #0d47a1 100%);
    color: #fff; padding: 32px 36px; border-radius: 14px;
    margin-bottom: 28px; position: relative; overflow: hidden;
  }}
  .rpt-header::after {{
    content: ''; position: absolute; top: -40%; right: -10%;
    width: 300px; height: 300px; border-radius: 50%;
    background: rgba(255,255,255,0.06);
  }}
  .rpt-header h1 {{ font-size: 1.75em; font-weight: 700; margin-bottom: 6px; position: relative; z-index: 1; }}
  .rpt-header .meta {{ opacity: 0.85; font-size: 0.9em; position: relative; z-index: 1; }}

  /* ── Summary Cards ── */
  .rpt-cards {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px; margin-bottom: 28px;
  }}
  .rpt-card {{
    background: #fff; border-radius: 12px; padding: 20px 24px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06); border: 1px solid #e8eaed;
    transition: box-shadow 0.2s;
  }}
  .rpt-card:hover {{ box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
  .rpt-card .card-icon {{ font-size: 1.6em; margin-bottom: 8px; }}
  .rpt-card .card-value {{
    font-size: 1.6em; font-weight: 700; color: #1a73e8; margin-bottom: 2px;
  }}
  .rpt-card .card-label {{ font-size: 0.85em; color: #5f6368; font-weight: 500; }}
  .rpt-card .card-change {{
    font-size: 0.8em; margin-top: 4px; font-weight: 600;
  }}
  .rpt-card .card-change.up {{ color: #34a853; }}
  .rpt-card .card-change.down {{ color: #ea4335; }}

  /* ── Section (generic wrapper) ── */
  .rpt-section {{
    background: #fff; border-radius: 12px; padding: 28px 32px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06); border: 1px solid #e8eaed;
    margin-bottom: 24px;
  }}
  .rpt-section-title {{
    font-size: 1.15em; font-weight: 700; color: #1a1a2e;
    margin-bottom: 16px; padding-bottom: 10px;
    border-bottom: 2px solid #e8eaed;
  }}

  /* ── Chart ── */
  .rpt-chart {{ position: relative; }}
  .rpt-chart canvas {{ max-height: 360px; }}

  /* ── Table ── */
  .rpt-table-controls {{
    display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
    margin-bottom: 14px;
  }}
  .rpt-search {{
    flex: 1; min-width: 180px; padding: 8px 14px;
    border: 1.5px solid #dadce0; border-radius: 8px;
    font-size: 0.9em; font-family: inherit;
    transition: border-color 0.2s;
  }}
  .rpt-search:focus {{ outline: none; border-color: #1a73e8; box-shadow: 0 0 0 3px rgba(26,115,232,0.12); }}
  .rpt-filter-select {{
    padding: 8px 12px; border: 1.5px solid #dadce0; border-radius: 8px;
    font-size: 0.85em; font-family: inherit; background: #fff; cursor: pointer;
  }}
  .rpt-table-wrap {{ overflow-x: auto; }}
  .rpt-table {{
    width: 100%; border-collapse: separate; border-spacing: 0;
    font-size: 0.9em;
  }}
  .rpt-table thead th {{
    background: #f8f9fa; color: #1a1a2e; text-align: left;
    padding: 11px 14px; font-weight: 600; font-size: 0.85em;
    text-transform: uppercase; letter-spacing: 0.3px;
    border-bottom: 2px solid #e8eaed; cursor: pointer;
    user-select: none; white-space: nowrap;
    position: sticky; top: 0;
  }}
  .rpt-table thead th:hover {{ background: #e8eaed; }}
  .rpt-table thead th .sort-icon {{ margin-left: 4px; opacity: 0.4; font-size: 0.8em; }}
  .rpt-table thead th .sort-icon.active {{ opacity: 1; color: #1a73e8; }}
  .rpt-table tbody td {{
    padding: 10px 14px; border-bottom: 1px solid #f0f0f0;
    color: #333;
  }}
  .rpt-table tbody tr:hover {{ background: #f0f6ff; }}
  .rpt-table tbody tr:nth-child(even) {{ background: #fafbfc; }}
  .rpt-table tbody tr:nth-child(even):hover {{ background: #f0f6ff; }}

  /* Status badges */
  .badge {{
    display: inline-block; padding: 3px 10px; border-radius: 12px;
    font-size: 0.8em; font-weight: 600; text-transform: capitalize;
  }}
  .badge-green {{ background: #e6f4ea; color: #1e8e3e; }}
  .badge-yellow {{ background: #fef7e0; color: #b06000; }}
  .badge-red {{ background: #fce8e6; color: #c5221f; }}
  .badge-blue {{ background: #e8f0fe; color: #1a73e8; }}
  .badge-gray {{ background: #f1f3f4; color: #5f6368; }}

  /* Pagination */
  .rpt-pagination {{
    display: flex; justify-content: space-between; align-items: center;
    margin-top: 14px; font-size: 0.85em; color: #5f6368;
  }}
  .rpt-pagination .page-info {{ font-weight: 500; }}
  .rpt-page-btns {{ display: flex; gap: 6px; }}
  .rpt-page-btn {{
    padding: 6px 12px; border: 1px solid #dadce0; border-radius: 6px;
    background: #fff; cursor: pointer; font-family: inherit; font-size: 0.9em;
    transition: all 0.15s;
  }}
  .rpt-page-btn:hover {{ background: #f0f6ff; border-color: #1a73e8; }}
  .rpt-page-btn.active {{ background: #1a73e8; color: #fff; border-color: #1a73e8; }}
  .rpt-page-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}

  /* ── Text (markdown) ── */
  .rpt-text h1 {{ font-size: 1.4em; color: #1a73e8; margin: 16px 0 10px; }}
  .rpt-text h2 {{ font-size: 1.2em; color: #333; margin: 14px 0 8px; }}
  .rpt-text h3 {{ font-size: 1.05em; color: #555; margin: 12px 0 6px; }}
  .rpt-text p {{ margin: 8px 0; }}
  .rpt-text ul, .rpt-text ol {{ padding-left: 24px; margin: 8px 0; }}
  .rpt-text blockquote {{
    border-left: 4px solid #1a73e8; padding: 10px 16px;
    background: #f0f6ff; margin: 12px 0; border-radius: 0 8px 8px 0;
  }}
  .rpt-text code {{ background: #f1f3f4; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }}
  .rpt-text strong {{ color: #1a73e8; }}

  /* ── Grid layout for side-by-side charts ── */
  .rpt-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
    gap: 24px; margin-bottom: 24px;
  }}
  .rpt-grid > .rpt-section {{ margin-bottom: 0; }}

  /* ── Footer ── */
  .rpt-footer {{
    text-align: center; padding: 20px; color: #999; font-size: 0.82em;
  }}

  /* ── Print ── */
  @media print {{
    body {{ background: #fff; padding: 0; max-width: 100%; }}
    .rpt-header {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; border-radius: 0; }}
    .rpt-section {{ box-shadow: none; border: 1px solid #ddd; break-inside: avoid; }}
    .rpt-search, .rpt-filter-select, .rpt-page-btns {{ display: none; }}
    .rpt-pagination .page-info {{ display: none; }}
  }}

  /* ── Responsive ── */
  @media (max-width: 600px) {{
    body {{ padding: 12px 8px; }}
    .rpt-header {{ padding: 24px 20px; }}
    .rpt-section {{ padding: 20px 16px; }}
    .rpt-cards {{ grid-template-columns: 1fr 1fr; }}
    .rpt-grid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>

<div class="rpt-header">
  <h1>{title}</h1>
  <div class="meta">{subtitle} &bull; Generated {generated_at}</div>
</div>

{components_html}

<div class="rpt-footer">
  Generated by OmniDesk &bull; {generated_at}
</div>

<script>
// ── Markdown renderer ──
document.querySelectorAll('[data-markdown]').forEach(function(el) {{
  el.innerHTML = marked.parse(el.getAttribute('data-markdown'));
}});

// ── Chart renderer ──
const chartConfigs = {charts_json};
chartConfigs.forEach(function(cfg) {{
  const ctx = document.getElementById(cfg.id);
  if (ctx) {{
    new Chart(ctx, {{
      type: cfg.chart_type || cfg.type,
      data: cfg.data,
      options: Object.assign({{
        responsive: true,
        maintainAspectRatio: true,
        plugins: {{ legend: {{ position: 'bottom' }} }}
      }}, cfg.options || {{}})
    }});
  }}
}});

// ── Interactive Table Engine ──
const tableConfigs = {tables_json};

tableConfigs.forEach(function(tbl) {{
  const container = document.getElementById('tbl-wrap-' + tbl.id);
  if (!container) return;

  const state = {{
    data: tbl.rows,
    filtered: tbl.rows.slice(),
    page: 0,
    pageSize: tbl.page_size || 15,
    sortCol: null,
    sortAsc: true,
    searchTerm: '',
    filters: {{}},
  }};

  const searchInput = container.querySelector('.rpt-search');
  const filterSelects = container.querySelectorAll('.rpt-filter-select');
  const tbody = container.querySelector('tbody');
  const pageInfo = container.querySelector('.page-info');
  const pageBtns = container.querySelector('.rpt-page-btns');
  const headers = container.querySelectorAll('thead th');

  function applyFilters() {{
    let result = state.data;
    // Search across all columns
    if (state.searchTerm) {{
      const q = state.searchTerm.toLowerCase();
      result = result.filter(function(row) {{
        return row.some(function(cell) {{
          return String(cell).toLowerCase().indexOf(q) !== -1;
        }});
      }});
    }}
    // Column filters
    Object.keys(state.filters).forEach(function(colIdx) {{
      const val = state.filters[colIdx];
      if (val) {{
        result = result.filter(function(row) {{
          return String(row[parseInt(colIdx)]) === val;
        }});
      }}
    }});
    // Sort
    if (state.sortCol !== null) {{
      const ci = state.sortCol;
      const asc = state.sortAsc;
      result = result.slice().sort(function(a, b) {{
        let va = a[ci], vb = b[ci];
        const na = parseFloat(String(va).replace(/[^0-9.-]/g, ''));
        const nb = parseFloat(String(vb).replace(/[^0-9.-]/g, ''));
        if (!isNaN(na) && !isNaN(nb)) {{ va = na; vb = nb; }}
        else {{ va = String(va).toLowerCase(); vb = String(vb).toLowerCase(); }}
        if (va < vb) return asc ? -1 : 1;
        if (va > vb) return asc ? 1 : -1;
        return 0;
      }});
    }}
    state.filtered = result;
    state.page = 0;
    render();
  }}

  function render() {{
    const start = state.page * state.pageSize;
    const end = Math.min(start + state.pageSize, state.filtered.length);
    const pageData = state.filtered.slice(start, end);

    // Render rows
    tbody.innerHTML = '';
    if (pageData.length === 0) {{
      const tr = document.createElement('tr');
      tr.innerHTML = '<td colspan="' + tbl.columns.length + '" style="text-align:center;padding:24px;color:#999;">No data found</td>';
      tbody.appendChild(tr);
    }} else {{
      pageData.forEach(function(row) {{
        const tr = document.createElement('tr');
        row.forEach(function(cell, ci) {{
          const td = document.createElement('td');
          // Check if column has badge mapping
          const col = tbl.columns[ci];
          if (col.badges && cell in col.badges) {{
            td.innerHTML = '<span class="badge badge-' + col.badges[cell] + '">' + cell + '</span>';
          }} else {{
            td.textContent = cell != null ? cell : '';
          }}
          tr.appendChild(td);
        }});
        tbody.appendChild(tr);
      }});
    }}

    // Page info
    if (pageInfo) {{
      pageInfo.textContent = 'Showing ' + (state.filtered.length === 0 ? 0 : start + 1) + '–' + end + ' of ' + state.filtered.length;
    }}

    // Page buttons
    if (pageBtns) {{
      const totalPages = Math.max(1, Math.ceil(state.filtered.length / state.pageSize));
      pageBtns.innerHTML = '';
      // Prev
      const prev = document.createElement('button');
      prev.className = 'rpt-page-btn';
      prev.textContent = '‹ Prev';
      prev.disabled = state.page === 0;
      prev.onclick = function() {{ state.page--; render(); }};
      pageBtns.appendChild(prev);
      // Page numbers (show max 7)
      const maxShow = 7;
      let pStart = Math.max(0, state.page - 3);
      let pEnd = Math.min(totalPages, pStart + maxShow);
      if (pEnd - pStart < maxShow) pStart = Math.max(0, pEnd - maxShow);
      for (let i = pStart; i < pEnd; i++) {{
        const btn = document.createElement('button');
        btn.className = 'rpt-page-btn' + (i === state.page ? ' active' : '');
        btn.textContent = i + 1;
        btn.onclick = (function(p) {{ return function() {{ state.page = p; render(); }}; }})(i);
        pageBtns.appendChild(btn);
      }}
      // Next
      const next = document.createElement('button');
      next.className = 'rpt-page-btn';
      next.textContent = 'Next ›';
      next.disabled = state.page >= totalPages - 1;
      next.onclick = function() {{ state.page++; render(); }};
      pageBtns.appendChild(next);
    }}

    // Update sort icons
    headers.forEach(function(th, i) {{
      const icon = th.querySelector('.sort-icon');
      if (icon) {{
        if (state.sortCol === i) {{
          icon.className = 'sort-icon active';
          icon.textContent = state.sortAsc ? ' ▲' : ' ▼';
        }} else {{
          icon.className = 'sort-icon';
          icon.textContent = ' ⇅';
        }}
      }}
    }});
  }}

  // Event: search
  if (searchInput) {{
    searchInput.addEventListener('input', function() {{
      state.searchTerm = this.value;
      applyFilters();
    }});
  }}

  // Event: column filters
  filterSelects.forEach(function(sel) {{
    sel.addEventListener('change', function() {{
      const colIdx = this.getAttribute('data-col');
      state.filters[colIdx] = this.value;
      applyFilters();
    }});
  }});

  // Event: sort by column click
  headers.forEach(function(th, i) {{
    th.addEventListener('click', function() {{
      if (state.sortCol === i) {{ state.sortAsc = !state.sortAsc; }}
      else {{ state.sortCol = i; state.sortAsc = true; }}
      applyFilters();
    }});
  }});

  // Initial render
  render();
}});
</script>
</body>
</html>"""


# ── Component Builders ───────────────────────────────────────────────

def _build_cards_html(component):
    """Build summary cards HTML."""
    cards = component.get("cards", [])
    html = '<div class="rpt-cards">\n'
    for card in cards:
        change_html = ""
        if card.get("change"):
            direction = "up" if card.get("change_direction", "up") == "up" else "down"
            change_html = f'<div class="card-change {direction}">{card["change"]}</div>'

        icon_html = f'<div class="card-icon">{card["icon"]}</div>' if card.get("icon") else ""

        html += f"""  <div class="rpt-card">
    {icon_html}
    <div class="card-value">{card["value"]}</div>
    <div class="card-label">{card["label"]}</div>
    {change_html}
  </div>\n"""
    html += '</div>'
    return html


def _build_chart_html(component):
    """Build chart section HTML. Returns (html, chart_config)."""
    chart_id = component["id"]
    title = component.get("title", "")
    title_html = f'<div class="rpt-section-title">{title}</div>' if title else ""

    html = f"""<div class="rpt-section rpt-chart">
  {title_html}
  <canvas id="{chart_id}" height="{component.get('height', 320)}"></canvas>
</div>"""

    config = {
        "id": chart_id,
        "chart_type": component.get("chart_type", "bar"),
        "data": component.get("data", {}),
        "options": component.get("options", {}),
    }
    return html, config


def _build_table_html(component):
    """Build interactive table section HTML. Returns (html, table_config)."""
    table_id = component["id"]
    title = component.get("title", "")
    columns = component.get("columns", [])
    rows = component.get("rows", [])
    filterable_cols = component.get("filterable_columns", [])
    page_size = component.get("page_size", 15)

    title_html = f'<div class="rpt-section-title">{title}</div>' if title else ""

    # Build filter dropdowns for specified columns
    filters_html = ""
    for col_idx in filterable_cols:
        if col_idx < len(columns):
            col = columns[col_idx]
            col_name = col["name"] if isinstance(col, dict) else col
            unique_vals = sorted(set(str(row[col_idx]) for row in rows if row[col_idx] is not None))
            opts = "".join(f'<option value="{v}">{v}</option>' for v in unique_vals)
            filters_html += f'<select class="rpt-filter-select" data-col="{col_idx}"><option value="">All {col_name}</option>{opts}</select>\n'

    # Build table headers
    headers_html = ""
    for col in columns:
        col_name = col["name"] if isinstance(col, dict) else col
        headers_html += f'<th>{col_name}<span class="sort-icon"> ⇅</span></th>'

    html = f"""<div class="rpt-section" id="tbl-wrap-{table_id}">
  {title_html}
  <div class="rpt-table-controls">
    <input type="text" class="rpt-search" placeholder="Search...">
    {filters_html}
  </div>
  <div class="rpt-table-wrap">
    <table class="rpt-table">
      <thead><tr>{headers_html}</tr></thead>
      <tbody></tbody>
    </table>
  </div>
  <div class="rpt-pagination">
    <span class="page-info"></span>
    <div class="rpt-page-btns"></div>
  </div>
</div>"""

    # Normalize columns for JSON config
    col_configs = []
    for col in columns:
        if isinstance(col, dict):
            col_configs.append(col)
        else:
            col_configs.append({"name": col})

    config = {
        "id": table_id,
        "columns": col_configs,
        "rows": rows,
        "page_size": page_size,
    }
    return html, config


def _build_text_html(component):
    """Build markdown text section HTML."""
    content = component.get("content", "")
    title = component.get("title", "")
    title_html = f'<div class="rpt-section-title">{title}</div>' if title else ""

    escaped = json.dumps(content)
    html = f"""<div class="rpt-section">
  {title_html}
  <div class="rpt-text" data-markdown={escaped}></div>
</div>"""
    return html


def _build_grid_html(children_html):
    """Wrap components in a side-by-side grid."""
    return f'<div class="rpt-grid">\n{children_html}\n</div>'


# ── Main Builder ─────────────────────────────────────────────────────

def build_report_html(title, components, subtitle=""):
    """Build a complete HTML report from structured components.

    Args:
        title: Report title
        components: List of component dicts. Each must have a "type" key.
            Types: summary_cards, chart, table, text, grid
        subtitle: Optional subtitle line

    Returns:
        Complete HTML string
    """
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    charts = []
    tables = []
    html_parts = []

    for comp in components:
        comp_type = comp.get("type", "text")

        if comp_type == "summary_cards":
            html_parts.append(_build_cards_html(comp))

        elif comp_type == "chart":
            h, cfg = _build_chart_html(comp)
            charts.append(cfg)
            html_parts.append(h)

        elif comp_type == "table":
            h, cfg = _build_table_html(comp)
            tables.append(cfg)
            html_parts.append(h)

        elif comp_type == "text":
            html_parts.append(_build_text_html(comp))

        elif comp_type == "grid":
            # Grid wraps child components side-by-side
            children_html = []
            for child in comp.get("children", []):
                child_type = child.get("type", "text")
                if child_type == "chart":
                    h, cfg = _build_chart_html(child)
                    charts.append(cfg)
                    children_html.append(h)
                elif child_type == "table":
                    h, cfg = _build_table_html(child)
                    tables.append(cfg)
                    children_html.append(h)
                elif child_type == "text":
                    children_html.append(_build_text_html(child))
            html_parts.append(_build_grid_html("\n".join(children_html)))

    components_html = "\n\n".join(html_parts)

    html = HTML_TEMPLATE.format(
        title=title,
        subtitle=subtitle,
        generated_at=generated_at,
        components_html=components_html,
        charts_json=json.dumps(charts),
        tables_json=json.dumps(tables),
    )
    return html
