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
import base64
import json
import re
from datetime import datetime


# ── HTML Template ────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — OmniDesk Report</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css" integrity="sha512-DTOQO9RWCH3ppGqcWaEA1BIZOC6xxalwEsw9c2QQeAIftl+Vegovlnee1c9QX4TctnWMn13TZye+giMm8e2LwA==" crossorigin="anonymous" referrerpolicy="no-referrer" />
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --clr-primary: #4a6274;
    --clr-primary-dark: #354856;
    --clr-accent: #6b8f71;
    --clr-accent-warm: #c27c5e;
    --clr-bg: #f7f5f2;
    --clr-surface: #ffffff;
    --clr-text: #2d2a26;
    --clr-text-muted: #6b6560;
    --clr-border: #e6e2dd;
    --clr-hover: #f0ece7;
    --clr-up: #5a8a5e;
    --clr-down: #b5694d;
  }}

  * {{ margin: 0; padding: 0; box-sizing: border-box; }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
    background: var(--clr-bg); color: var(--clr-text); line-height: 1.6;
    width: 92%; max-width: 1800px; margin: 0 auto; padding: 28px 24px;
  }}

  /* ── Header ── */
  .rpt-header {{
    background: linear-gradient(135deg, var(--clr-primary) 0%, var(--clr-primary-dark) 100%);
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
    display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 18px; margin-bottom: 28px;
  }}
  @media (min-width: 1100px) {{
    .rpt-cards {{ grid-template-columns: repeat(4, 1fr); }}
  }}
  .rpt-card {{
    background: var(--clr-surface); border-radius: 12px; padding: 20px 24px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05); border: 1px solid var(--clr-border);
    transition: box-shadow 0.2s, transform 0.2s;
  }}
  .rpt-card:hover {{ box-shadow: 0 4px 14px rgba(0,0,0,0.08); transform: translateY(-1px); }}
  .rpt-card .card-header {{
    display: flex; align-items: center; gap: 8px; margin-bottom: 12px;
  }}
  .rpt-card .card-icon {{ font-size: 1.15em; color: var(--clr-accent-warm); line-height: 1; }}
  .rpt-card .card-icon i {{ opacity: 0.85; }}
  .rpt-card .card-label {{ font-size: 0.85em; color: var(--clr-text-muted); font-weight: 600; text-transform: capitalize; }}
  .rpt-card .card-value {{
    font-size: 1.6em; font-weight: 700; color: var(--clr-primary); margin-bottom: 2px;
  }}
  .rpt-card .card-change {{
    font-size: 0.8em; margin-top: 4px; font-weight: 600;
  }}
  .rpt-card .card-change.up {{ color: var(--clr-up); }}
  .rpt-card .card-change.down {{ color: var(--clr-down); }}

  /* ── Section (generic wrapper) ── */
  .rpt-section {{
    background: var(--clr-surface); border-radius: 12px; padding: 28px 32px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05); border: 1px solid var(--clr-border);
    margin-bottom: 24px;
  }}
  .rpt-section-title {{
    font-size: 1.15em; font-weight: 700; color: var(--clr-text);
    margin-bottom: 16px; padding-bottom: 10px;
    border-bottom: 2px solid var(--clr-border);
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
  .rpt-search:focus {{ outline: none; border-color: var(--clr-primary); box-shadow: 0 0 0 3px rgba(74,98,116,0.12); }}
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
    background: #f5f3f0; color: var(--clr-text); text-align: left;
    padding: 11px 14px; font-weight: 600; font-size: 0.85em;
    text-transform: uppercase; letter-spacing: 0.3px;
    border-bottom: 2px solid var(--clr-border); cursor: pointer;
    user-select: none; white-space: nowrap;
    position: sticky; top: 0;
  }}
  .rpt-table thead th:hover {{ background: var(--clr-hover); }}
  .rpt-table thead th .sort-icon {{ margin-left: 4px; opacity: 0.4; font-size: 0.8em; }}
  .rpt-table thead th .sort-icon.active {{ opacity: 1; color: var(--clr-primary); }}
  .rpt-table tbody td {{
    padding: 10px 14px; border-bottom: 1px solid #ece8e3;
    color: #3d3a36;
  }}
  .rpt-table tbody tr:hover {{ background: var(--clr-hover); }}
  .rpt-table tbody tr:nth-child(even) {{ background: #faf8f6; }}
  .rpt-table tbody tr:nth-child(even):hover {{ background: var(--clr-hover); }}

  /* Status badges */
  .badge {{
    display: inline-block; padding: 3px 10px; border-radius: 12px;
    font-size: 0.8em; font-weight: 600; text-transform: capitalize;
  }}
  .badge-green {{ background: #e8f0e4; color: #4a7a4e; }}
  .badge-yellow {{ background: #f5efe0; color: #8a6d3b; }}
  .badge-red {{ background: #f3e5e0; color: #9c5040; }}
  .badge-blue {{ background: #e4ecf2; color: #4a6274; }}
  .badge-gray {{ background: #efece8; color: #6b6560; }}

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
  .rpt-page-btn:hover {{ background: var(--clr-hover); border-color: var(--clr-primary); }}
  .rpt-page-btn.active {{ background: var(--clr-primary); color: #fff; border-color: var(--clr-primary); }}
  .rpt-page-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}

  /* ── Text (markdown) ── */
  .rpt-text h1 {{ font-size: 1.4em; color: var(--clr-primary); margin: 16px 0 10px; }}
  .rpt-text h2 {{ font-size: 1.2em; color: var(--clr-text); margin: 14px 0 8px; }}
  .rpt-text h3 {{ font-size: 1.05em; color: var(--clr-text-muted); margin: 12px 0 6px; }}
  .rpt-text p {{ margin: 8px 0; }}
  .rpt-text ul, .rpt-text ol {{ padding-left: 24px; margin: 8px 0; }}
  .rpt-text blockquote {{
    border-left: 4px solid var(--clr-accent-warm); padding: 10px 16px;
    background: #f5f0eb; margin: 12px 0; border-radius: 0 8px 8px 0;
  }}
  .rpt-text code {{ background: #efece8; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }}
  .rpt-text strong {{ color: var(--clr-primary-dark); }}

  /* ── Grid layout for side-by-side charts ── */
  .rpt-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
    gap: 24px; margin-bottom: 24px;
  }}
  .rpt-grid > .rpt-section {{ margin-bottom: 0; }}

  /* ── Footer ── */
  .rpt-footer {{
    text-align: center; padding: 20px; color: var(--clr-text-muted); font-size: 0.82em;
  }}

  /* ── Print ── */
  @media print {{
    body {{ background: #fff; padding: 0; width: 100%; max-width: 100%; }}
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
    .rpt-cards {{ grid-template-columns: repeat(2, 1fr); }}
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
document.querySelectorAll('[data-markdown-b64]').forEach(function(el) {{
  try {{
    var raw = atob(el.getAttribute('data-markdown-b64'));
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    var content = new TextDecoder('utf-8').decode(bytes);
    el.innerHTML = marked.parse(content);
  }} catch(e) {{
    el.textContent = 'Error rendering content';
  }}
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


# ── Icon Mapping (Lucide / common names → Font Awesome 6) ───────────

_ICON_MAP = {
    # Finance & Revenue
    "trending-up": "fa-arrow-trend-up",
    "trending-down": "fa-arrow-trend-down",
    "indian-rupee": "fa-indian-rupee-sign",
    "rupee": "fa-indian-rupee-sign",
    "dollar": "fa-dollar-sign",
    "money": "fa-money-bill-wave",
    "wallet": "fa-wallet",
    "credit-card": "fa-credit-card",
    "coins": "fa-coins",
    "piggy-bank": "fa-piggy-bank",
    # Shopping & Orders
    "shopping-cart": "fa-cart-shopping",
    "cart": "fa-cart-shopping",
    "shopping-bag": "fa-bag-shopping",
    "bag": "fa-bag-shopping",
    "receipt": "fa-receipt",
    "barcode": "fa-barcode",
    # People
    "users": "fa-users",
    "user": "fa-user",
    "user-plus": "fa-user-plus",
    "people": "fa-people-group",
    # Documents & Files
    "file-text": "fa-file-lines",
    "file": "fa-file",
    "file-invoice": "fa-file-invoice",
    "file-pdf": "fa-file-pdf",
    "clipboard": "fa-clipboard-list",
    "document": "fa-file-lines",
    # Alerts & Status
    "alert-circle": "fa-circle-exclamation",
    "alert-triangle": "fa-triangle-exclamation",
    "warning": "fa-triangle-exclamation",
    "check-circle": "fa-circle-check",
    "check": "fa-check",
    "info": "fa-circle-info",
    "bell": "fa-bell",
    "ban": "fa-ban",
    # Inventory & Products
    "package": "fa-box",
    "packages": "fa-boxes-stacked",
    "box": "fa-box",
    "boxes": "fa-boxes-stacked",
    "warehouse": "fa-warehouse",
    "truck": "fa-truck",
    "shipping": "fa-truck-fast",
    "tag": "fa-tag",
    "tags": "fa-tags",
    # Charts & Analytics
    "bar-chart": "fa-chart-bar",
    "chart": "fa-chart-line",
    "pie-chart": "fa-chart-pie",
    "activity": "fa-chart-line",
    "analytics": "fa-chart-column",
    # Misc
    "calendar": "fa-calendar-days",
    "clock": "fa-clock",
    "globe": "fa-globe",
    "star": "fa-star",
    "heart": "fa-heart",
    "settings": "fa-gear",
    "search": "fa-magnifying-glass",
    "mail": "fa-envelope",
    "phone": "fa-phone",
    "home": "fa-house",
    "link": "fa-link",
    "download": "fa-download",
    "upload": "fa-upload",
    "refresh": "fa-arrows-rotate",
    "percent": "fa-percent",
    "hashtag": "fa-hashtag",
    "list": "fa-list",
    "grid": "fa-grip",
    "layers": "fa-layer-group",
    "shield": "fa-shield-halved",
    "lock": "fa-lock",
    "eye": "fa-eye",
    "thumbs-up": "fa-thumbs-up",
    "thumbs-down": "fa-thumbs-down",
}


# ── Emoji → Font Awesome mapping ─────────────────────────────────────

_EMOJI_MAP = {
    # Finance
    "💰": "fa-coins", "💵": "fa-money-bill", "💲": "fa-dollar-sign",
    "💳": "fa-credit-card", "💸": "fa-money-bill-wave", "🪙": "fa-coins",
    "₹": "fa-indian-rupee-sign",
    # Shopping & Orders
    "🛒": "fa-cart-shopping", "🛍️": "fa-bag-shopping", "🛍": "fa-bag-shopping",
    "🧾": "fa-receipt",
    # Documents
    "📄": "fa-file-lines", "📋": "fa-clipboard-list", "📝": "fa-pen-to-square",
    "📊": "fa-chart-column", "📈": "fa-chart-line", "📉": "fa-chart-line",
    "📑": "fa-file-lines", "🗂️": "fa-folder-open", "🗂": "fa-folder-open",
    # Alerts & Status
    "⚠️": "fa-triangle-exclamation", "⚠": "fa-triangle-exclamation",
    "❌": "fa-xmark", "✅": "fa-circle-check", "✓": "fa-check",
    "🚫": "fa-ban", "⛔": "fa-ban", "❗": "fa-circle-exclamation",
    "ℹ️": "fa-circle-info", "ℹ": "fa-circle-info",
    "🔴": "fa-circle-exclamation", "🟢": "fa-circle-check",
    "🟡": "fa-circle-exclamation", "🟠": "fa-triangle-exclamation",
    # Inventory & Products
    "📦": "fa-box", "🏪": "fa-store", "🏭": "fa-industry",
    "🏢": "fa-building", "🏠": "fa-house",
    # People
    "👤": "fa-user", "👥": "fa-users", "👕": "fa-shirt",
    "🧑‍💼": "fa-user-tie", "👨‍💼": "fa-user-tie",
    # Time
    "⏳": "fa-hourglass-half", "⏰": "fa-clock", "🕐": "fa-clock",
    "📅": "fa-calendar-days",
    # Communication
    "📧": "fa-envelope", "📩": "fa-envelope-open", "📞": "fa-phone",
    "💬": "fa-comment", "🔔": "fa-bell",
    # Misc
    "🔍": "fa-magnifying-glass", "🔎": "fa-magnifying-glass",
    "⭐": "fa-star", "🌟": "fa-star", "🏆": "fa-trophy",
    "🥇": "fa-medal", "🥈": "fa-medal", "🥉": "fa-medal",
    "🎯": "fa-bullseye", "🚀": "fa-rocket", "💡": "fa-lightbulb",
    "🔧": "fa-wrench", "⚙️": "fa-gear", "⚙": "fa-gear",
    "🔒": "fa-lock", "🔓": "fa-lock-open", "🛡️": "fa-shield-halved",
    "📌": "fa-thumbtack", "🔗": "fa-link",
    "🧵": "fa-scissors", "🏷️": "fa-tag", "🏷": "fa-tag",
    "🗓️": "fa-calendar", "🗓": "fa-calendar",
    "📍": "fa-location-dot", "🌐": "fa-globe",
    "🔄": "fa-arrows-rotate", "➡️": "fa-arrow-right",
    "⬆️": "fa-arrow-up", "⬇️": "fa-arrow-down",
    "📤": "fa-upload", "📥": "fa-download",
    "🏪": "fa-store", "🛠️": "fa-screwdriver-wrench",
    "🧪": "fa-flask", "📐": "fa-ruler-combined",
    "🎨": "fa-palette", "🖨️": "fa-print",
    "👁️": "fa-eye", "👁": "fa-eye",
}

# Pre-compile regex: match any emoji key (sorted longest first to avoid partial matches)
_EMOJI_PATTERN = re.compile(
    "|".join(re.escape(e) for e in sorted(_EMOJI_MAP.keys(), key=len, reverse=True))
)


def _emoji_to_fa(text):
    """Replace all emojis in a string with Font Awesome <i> tags."""
    if not text:
        return text

    def _replace(match):
        emoji = match.group(0)
        fa_name = _EMOJI_MAP.get(emoji, "fa-circle")
        return f'<i class="fa-solid {fa_name}"></i>'

    return _EMOJI_PATTERN.sub(_replace, text)


def _resolve_icon(icon_name):
    """Convert an icon name to a Font Awesome <i> tag.

    Accepts: Lucide names ("trending-up"), FA names ("fa-box"),
    or emoji (converted to FA icon).
    """
    if not icon_name:
        return ""
    name = icon_name.strip()
    # If it's an emoji, convert via emoji map
    if any(ord(c) > 127 for c in name):
        fa_name = _EMOJI_MAP.get(name, None)
        if fa_name:
            return f'<div class="card-icon"><i class="fa-solid {fa_name}"></i></div>'
        # Try matching the first emoji in the string
        match = _EMOJI_PATTERN.search(name)
        if match:
            fa_name = _EMOJI_MAP[match.group(0)]
            return f'<div class="card-icon"><i class="fa-solid {fa_name}"></i></div>'
        # Unknown emoji — use a generic icon
        return f'<div class="card-icon"><i class="fa-solid fa-circle"></i></div>'
    # Already a full FA class like "fa-box" or "fa-solid fa-box"
    if name.startswith("fa-") or name.startswith("fa "):
        fa_class = name if " " in name else f"fa-solid {name}"
        return f'<div class="card-icon"><i class="{fa_class}"></i></div>'
    # Lookup in map
    fa_name = _ICON_MAP.get(name.lower(), f"fa-{name}")
    return f'<div class="card-icon"><i class="fa-solid {fa_name}"></i></div>'


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

        icon_html = _resolve_icon(card.get("icon", ""))
        label = card.get("label", "")

        html += f"""  <div class="rpt-card">
    <div class="card-header">{icon_html}<span class="card-label">{label}</span></div>
    <div class="card-value">{card["value"]}</div>
    {change_html}
  </div>\n"""
    html += '</div>'
    return html


def _build_chart_html(component):
    """Build chart section HTML. Returns (html, chart_config)."""
    chart_id = component["id"]
    title = _emoji_to_fa(component.get("title", ""))
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
    title = _emoji_to_fa(component.get("title", ""))
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
    title = _emoji_to_fa(component.get("title", ""))
    title_html = f'<div class="rpt-section-title">{title}</div>' if title else ""

    # Clean up content: decode unicode escapes and normalize newlines
    if isinstance(content, str):
        # Handle raw \n literals that should be actual newlines
        content = content.replace("\\n", "\n")
        # Handle common unicode escape artifacts
        content = content.replace("\\u20b9", "₹").replace("\\u20B9", "₹")
        content = content.replace("\\u2019", "'").replace("\\u2018", "'")
        content = content.replace("\\u201c", "\u201c").replace("\\u201d", "\u201d")
        # Replace emojis with Font Awesome icons
        content = _emoji_to_fa(content)

    # Encode as HTML-safe attribute using base64 to avoid all escaping issues
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    html = f"""<div class="rpt-section">
  {title_html}
  <div class="rpt-text" data-markdown-b64="{encoded}"></div>
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
