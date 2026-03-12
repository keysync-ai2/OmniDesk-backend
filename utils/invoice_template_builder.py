"""Invoice template editor — generates a self-contained HTML page with split-panel layout.

Left panel (60%): Scrollable config — logo upload, text inputs, theme swatches, grouped checkboxes.
Right panel (40%): Fixed live preview — HTML mock invoice that updates in real-time.

Follows the same pattern as form_builder.py: generate HTML → upload to S3 → return signed URL.
"""
import json


def build_invoice_template_editor(template_config, logo_url=None, save_endpoint="", logo_endpoint="", auth_token=""):
    """Build a complete HTML editor page for invoice template customization.

    Args:
        template_config: Current template config dict (fields, custom_text, theme)
        logo_url: Signed CloudFront URL for current logo (or None)
        save_endpoint: API endpoint to POST template updates
        logo_endpoint: API endpoint to POST logo uploads
        auth_token: JWT token for authenticating save/upload requests

    Returns:
        Complete HTML string
    """
    config_json = json.dumps(template_config)
    logo_url_json = json.dumps(logo_url or "")
    save_endpoint_json = json.dumps(save_endpoint)
    logo_endpoint_json = json.dumps(logo_endpoint)
    auth_token_json = json.dumps(auth_token or "")

    return EDITOR_HTML_TEMPLATE.format(
        config_json=config_json,
        logo_url_json=logo_url_json,
        save_endpoint_json=save_endpoint_json,
        logo_endpoint_json=logo_endpoint_json,
        auth_token_json=auth_token_json,
    )


EDITOR_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Invoice Template Editor — OmniDesk</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
  .theme-swatch {{ width: 40px; height: 40px; border-radius: 8px; cursor: pointer; border: 3px solid transparent; transition: all 0.2s; }}
  .theme-swatch.active {{ border-color: #111; transform: scale(1.1); }}
  .theme-swatch:hover {{ transform: scale(1.05); }}
  .field-toggle {{ cursor: pointer; user-select: none; }}
  .field-toggle input[type="checkbox"] {{ width: 16px; height: 16px; accent-color: #2563eb; }}
  .preview-invoice {{ font-size: 11px; line-height: 1.4; }}
  .config-panel {{ overflow-y: auto; }}
  .config-panel::-webkit-scrollbar {{ width: 6px; }}
  .config-panel::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 3px; }}
  .save-toast {{ position: fixed; top: 20px; right: 20px; padding: 12px 24px; border-radius: 8px;
    background: #16a34a; color: white; font-weight: 600; z-index: 100; transition: opacity 0.3s; opacity: 0; pointer-events: none; }}
  .save-toast.show {{ opacity: 1; }}
</style>
</head>
<body class="bg-gray-100 h-screen overflow-hidden">

<div class="save-toast" id="save-toast">Saved successfully!</div>

<!-- Top Bar -->
<div class="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
  <div class="flex items-center gap-3">
    <h1 class="text-lg font-bold text-gray-800">Invoice Template Editor</h1>
    <span class="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full font-medium">OmniDesk</span>
  </div>
  <button onclick="saveTemplate()" id="save-btn"
    class="bg-blue-600 hover:bg-blue-700 text-white px-6 py-2 rounded-lg font-semibold text-sm transition-colors">
    Save Template
  </button>
</div>

<!-- Split Layout -->
<div class="flex h-[calc(100vh-57px)]">

  <!-- Left: Config Panel (60%) -->
  <div class="w-[60%] config-panel p-6 space-y-6">

    <!-- Logo Upload -->
    <div class="bg-white rounded-xl p-5 shadow-sm">
      <h3 class="text-sm font-bold text-gray-700 uppercase tracking-wide mb-3">Company Logo</h3>
      <div class="flex items-center gap-4">
        <div id="logo-preview-box" class="w-16 h-16 bg-gray-100 rounded-lg flex items-center justify-center text-gray-400 text-xs overflow-hidden border border-gray-200">
          <span id="logo-placeholder">No logo</span>
          <img id="logo-preview-img" class="w-full h-full object-contain hidden" alt="Logo">
        </div>
        <div>
          <input type="file" id="logo-input" accept="image/png,image/jpeg" class="hidden" onchange="handleLogoUpload(event)">
          <button onclick="document.getElementById('logo-input').click()"
            class="bg-gray-100 hover:bg-gray-200 text-gray-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors">
            Upload Logo
          </button>
          <p class="text-xs text-gray-400 mt-1">PNG or JPEG, max 2MB</p>
        </div>
      </div>
    </div>

    <!-- Custom Text -->
    <div class="bg-white rounded-xl p-5 shadow-sm">
      <h3 class="text-sm font-bold text-gray-700 uppercase tracking-wide mb-3">Branding &amp; Text</h3>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="block text-xs font-medium text-gray-600 mb-1">Brand Name</label>
          <input type="text" id="input-brand_name" placeholder="e.g. Acme Corp"
            class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-blue-400" oninput="updatePreview()">
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-600 mb-1">Tagline</label>
          <input type="text" id="input-tagline" placeholder="e.g. Quality you can trust"
            class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-blue-400" oninput="updatePreview()">
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-600 mb-1">Invoice Prefix</label>
          <input type="text" id="input-invoice_prefix" placeholder="INV" maxlength="10"
            class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-blue-400" oninput="updatePreview()">
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-600 mb-1">Footer Text</label>
          <input type="text" id="input-footer_text" placeholder="Thank you for your business"
            class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-blue-400" oninput="updatePreview()">
        </div>
      </div>
    </div>

    <!-- Theme Selection -->
    <div class="bg-white rounded-xl p-5 shadow-sm">
      <h3 class="text-sm font-bold text-gray-700 uppercase tracking-wide mb-3">Color Theme</h3>
      <div class="flex gap-3" id="theme-swatches"></div>
    </div>

    <!-- Field Toggles -->
    <div class="bg-white rounded-xl p-5 shadow-sm">
      <h3 class="text-sm font-bold text-gray-700 uppercase tracking-wide mb-4">Visible Fields</h3>
      <div class="space-y-5" id="field-groups"></div>
    </div>

  </div>

  <!-- Right: Live Preview (40%) -->
  <div class="w-[40%] bg-gray-200 p-6 overflow-y-auto">
    <div class="bg-white rounded-xl shadow-lg p-6 preview-invoice" id="invoice-preview">
      <!-- Rendered by JS -->
    </div>
  </div>

</div>

<script>
// ── Data ────────────────────────────────────────────────────────────
const CONFIG = {config_json};
const LOGO_URL = {logo_url_json};
const SAVE_ENDPOINT = {save_endpoint_json};
const LOGO_ENDPOINT = {logo_endpoint_json};
const AUTH_TOKEN = {auth_token_json};
const AUTH_HEADERS = AUTH_TOKEN
  ? {{ 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + AUTH_TOKEN }}
  : {{ 'Content-Type': 'application/json' }};

let currentConfig = JSON.parse(JSON.stringify(CONFIG));
let currentLogoUrl = LOGO_URL;

const THEMES = {{
  professional_blue: {{ header: '#2563EB', dark: '#1E293B', muted: '#64748B', accent: '#2563EB', label: 'Blue' }},
  forest_green: {{ header: '#166534', dark: '#1a2e1a', muted: '#6b7280', accent: '#16a34a', label: 'Green' }},
  charcoal: {{ header: '#374151', dark: '#111827', muted: '#6b7280', accent: '#4b5563', label: 'Charcoal' }},
  warm_terracotta: {{ header: '#b45309', dark: '#451a03', muted: '#78716c', accent: '#d97706', label: 'Terracotta' }},
  royal_purple: {{ header: '#7c3aed', dark: '#2e1065', muted: '#6b7280', accent: '#8b5cf6', label: 'Purple' }},
}};

const FIELD_GROUPS = [
  {{
    title: 'Company Info',
    fields: [
      {{ key: 'company_logo', label: 'Company Logo' }},
      {{ key: 'company_name', label: 'Company Name' }},
      {{ key: 'brand_name', label: 'Brand Name (custom)' }},
      {{ key: 'company_address', label: 'Company Address' }},
      {{ key: 'company_phone', label: 'Company Phone' }},
      {{ key: 'company_email', label: 'Company Email' }},
      {{ key: 'tagline', label: 'Tagline' }},
    ]
  }},
  {{
    title: 'Invoice Metadata',
    fields: [
      {{ key: 'invoice_number', label: 'Invoice Number' }},
      {{ key: 'invoice_date', label: 'Invoice Date' }},
      {{ key: 'due_date', label: 'Due Date' }},
      {{ key: 'order_reference', label: 'Order Reference' }},
    ]
  }},
  {{
    title: 'Customer (Bill To)',
    fields: [
      {{ key: 'customer_name', label: 'Customer Name' }},
      {{ key: 'customer_email', label: 'Customer Email' }},
      {{ key: 'customer_phone', label: 'Customer Phone' }},
      {{ key: 'customer_address', label: 'Customer Address' }},
    ]
  }},
  {{
    title: 'Line Items',
    fields: [
      {{ key: 'item_number', label: 'Row Number (#)' }},
      {{ key: 'item_sku', label: 'SKU Code' }},
      {{ key: 'item_description', label: 'Item Description' }},
      {{ key: 'item_quantity', label: 'Quantity' }},
      {{ key: 'item_unit_price', label: 'Unit Price' }},
      {{ key: 'item_line_total', label: 'Line Total' }},
    ]
  }},
  {{
    title: 'Totals',
    fields: [
      {{ key: 'subtotal', label: 'Subtotal' }},
      {{ key: 'tax_line', label: 'Tax Line' }},
      {{ key: 'grand_total', label: 'Grand Total' }},
    ]
  }},
  {{
    title: 'Footer',
    fields: [
      {{ key: 'payment_terms', label: 'Payment Terms' }},
      {{ key: 'notes', label: 'Notes Section' }},
      {{ key: 'footer_text', label: 'Footer Text' }},
      {{ key: 'powered_by_omnidesk', label: 'Powered by OmniDesk' }},
    ]
  }},
];

// ── Init ─────────────────────────────────────────────────────────────
function init() {{
  // Populate text inputs
  const ct = currentConfig.custom_text || {{}};
  document.getElementById('input-brand_name').value = ct.brand_name || '';
  document.getElementById('input-tagline').value = ct.tagline || '';
  document.getElementById('input-invoice_prefix').value = ct.invoice_prefix || 'INV';
  document.getElementById('input-footer_text').value = ct.footer_text || '';

  // Logo
  if (currentLogoUrl) {{
    document.getElementById('logo-preview-img').src = currentLogoUrl;
    document.getElementById('logo-preview-img').classList.remove('hidden');
    document.getElementById('logo-placeholder').classList.add('hidden');
  }}

  // Theme swatches
  const swatchContainer = document.getElementById('theme-swatches');
  Object.entries(THEMES).forEach(([key, t]) => {{
    const div = document.createElement('div');
    div.className = 'theme-swatch' + (currentConfig.theme === key ? ' active' : '');
    div.style.background = t.header;
    div.title = t.label;
    div.dataset.theme = key;
    div.onclick = () => selectTheme(key);
    swatchContainer.appendChild(div);
  }});

  // Field groups
  const groupContainer = document.getElementById('field-groups');
  FIELD_GROUPS.forEach(group => {{
    const groupDiv = document.createElement('div');
    groupDiv.innerHTML = `<h4 class="text-xs font-semibold text-gray-500 uppercase mb-2">${{group.title}}</h4>`;
    const fieldsDiv = document.createElement('div');
    fieldsDiv.className = 'grid grid-cols-2 gap-2';
    group.fields.forEach(f => {{
      const checked = currentConfig.fields[f.key] ? 'checked' : '';
      fieldsDiv.innerHTML += `
        <label class="field-toggle flex items-center gap-2 py-1 px-2 rounded hover:bg-gray-50">
          <input type="checkbox" data-field="${{f.key}}" ${{checked}} onchange="toggleField('${{f.key}}', this.checked)">
          <span class="text-sm text-gray-700">${{f.label}}</span>
        </label>`;
    }});
    groupDiv.appendChild(fieldsDiv);
    groupContainer.appendChild(groupDiv);
  }});

  updatePreview();
}}

// ── Actions ──────────────────────────────────────────────────────────
function selectTheme(key) {{
  currentConfig.theme = key;
  document.querySelectorAll('.theme-swatch').forEach(s => {{
    s.classList.toggle('active', s.dataset.theme === key);
  }});
  updatePreview();
}}

function toggleField(key, checked) {{
  currentConfig.fields[key] = checked;
  updatePreview();
}}

function getCustomText() {{
  return {{
    brand_name: document.getElementById('input-brand_name').value,
    tagline: document.getElementById('input-tagline').value,
    invoice_prefix: document.getElementById('input-invoice_prefix').value || 'INV',
    footer_text: document.getElementById('input-footer_text').value,
  }};
}}

// ── Logo Upload ──────────────────────────────────────────────────────
async function handleLogoUpload(event) {{
  const file = event.target.files[0];
  if (!file) return;

  if (!['image/png', 'image/jpeg'].includes(file.type)) {{
    alert('Please upload a PNG or JPEG image.');
    return;
  }}
  if (file.size > 2 * 1024 * 1024) {{
    alert('File too large. Maximum size is 2MB.');
    return;
  }}

  const reader = new FileReader();
  reader.onload = async function(e) {{
    const base64 = e.target.result.split(',')[1];

    // Show preview immediately
    document.getElementById('logo-preview-img').src = e.target.result;
    document.getElementById('logo-preview-img').classList.remove('hidden');
    document.getElementById('logo-placeholder').classList.add('hidden');
    currentLogoUrl = e.target.result;
    updatePreview();

    // Upload to server
    if (LOGO_ENDPOINT) {{
      try {{
        const resp = await fetch(LOGO_ENDPOINT, {{
          method: 'POST',
          headers: AUTH_HEADERS,
          body: JSON.stringify({{
            data: base64,
            filename: file.name,
            content_type: file.type,
          }}),
        }});
        const result = await resp.json();
        if (result.body) {{
          const body = JSON.parse(result.body);
          if (body.logo_url) {{
            currentLogoUrl = body.logo_url;
            document.getElementById('logo-preview-img').src = body.logo_url;
            updatePreview();
          }}
        }}
      }} catch (err) {{
        console.error('Logo upload failed:', err);
      }}
    }}
  }};
  reader.readAsDataURL(file);
}}

// ── Save ─────────────────────────────────────────────────────────────
async function saveTemplate() {{
  const btn = document.getElementById('save-btn');
  btn.disabled = true;
  btn.textContent = 'Saving...';

  currentConfig.custom_text = getCustomText();

  try {{
    const resp = await fetch(SAVE_ENDPOINT, {{
      method: 'POST',
      headers: AUTH_HEADERS,
      body: JSON.stringify({{ config: currentConfig }}),
    }});
    const toast = document.getElementById('save-toast');
    if (resp.ok) {{
      toast.textContent = 'Saved successfully!';
      toast.style.background = '#16a34a';
    }} else {{
      toast.textContent = 'Save failed. Try again.';
      toast.style.background = '#dc2626';
    }}
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2500);
  }} catch (err) {{
    const toast = document.getElementById('save-toast');
    toast.textContent = 'Network error.';
    toast.style.background = '#dc2626';
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2500);
  }}

  btn.disabled = false;
  btn.textContent = 'Save Template';
}}

// ── Live Preview ─────────────────────────────────────────────────────
function updatePreview() {{
  currentConfig.custom_text = getCustomText();
  const f = currentConfig.fields;
  const ct = currentConfig.custom_text;
  const theme = THEMES[currentConfig.theme] || THEMES.professional_blue;
  const prefix = ct.invoice_prefix || 'INV';

  let html = '';

  // Header
  html += `<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">`;
  html += `<div>`;
  if (f.company_logo && currentLogoUrl) {{
    html += `<img src="${{currentLogoUrl}}" style="height:28px;margin-bottom:4px;" alt="Logo">`;
  }}
  if (f.company_name) {{
    const name = (f.brand_name && ct.brand_name) ? ct.brand_name : 'Your Company';
    html += `<div style="font-size:16px;font-weight:700;color:${{theme.header}}">${{name}}</div>`;
  }}
  if (f.tagline && ct.tagline) {{
    html += `<div style="font-size:9px;color:${{theme.muted}};font-style:italic">${{ct.tagline}}</div>`;
  }}
  html += `</div>`;
  html += `<div style="font-size:20px;font-weight:800;color:${{theme.dark}}">INVOICE</div>`;
  html += `</div>`;

  // Company details
  const companyParts = [];
  if (f.company_address) companyParts.push('123 Business St');
  if (f.company_phone) companyParts.push('+1 555-0100');
  if (f.company_email) companyParts.push('hello@company.com');
  if (companyParts.length) {{
    html += `<div style="font-size:8px;color:${{theme.muted}};margin-bottom:6px">${{companyParts.join(' | ')}}</div>`;
  }}

  // Divider
  html += `<hr style="border:none;border-top:1px solid #e2e8f0;margin:8px 0">`;

  // Meta + Bill To
  html += `<div style="display:flex;gap:20px;margin-bottom:12px;">`;

  // Invoice details
  const metaItems = [];
  if (f.invoice_number) metaItems.push(`<strong style="font-size:10px;color:${{theme.dark}}">${{prefix}}-20260312-A1B2</strong>`);
  if (f.invoice_date) metaItems.push(`Date: 2026-03-12`);
  if (f.due_date) metaItems.push(`Due: 2026-04-11`);
  if (f.order_reference) metaItems.push(`Order: ORD-20260312-X9Y8`);
  if (metaItems.length) {{
    html += `<div style="flex:1"><div style="font-size:7px;font-weight:700;color:${{theme.muted}};text-transform:uppercase;margin-bottom:3px">Invoice Details</div>`;
    html += `<div style="font-size:8px;color:#475569;line-height:1.5">${{metaItems.join('<br>')}}</div></div>`;
  }}

  // Bill to
  const billItems = [];
  if (f.customer_name) billItems.push(`<strong style="font-size:10px;color:${{theme.dark}}">John Doe</strong>`);
  if (f.customer_address) billItems.push('456 Customer Ave');
  if (f.customer_email) billItems.push('john@example.com');
  if (f.customer_phone) billItems.push('+1 555-0200');
  if (billItems.length) {{
    html += `<div style="flex:1"><div style="font-size:7px;font-weight:700;color:${{theme.muted}};text-transform:uppercase;margin-bottom:3px">Bill To</div>`;
    html += `<div style="font-size:8px;color:#475569;line-height:1.5">${{billItems.join('<br>')}}</div></div>`;
  }}
  html += `</div>`;

  // Items table
  const cols = [];
  if (f.item_number) cols.push({{ h: '#', w: '8%', a: 'center' }});
  if (f.item_description) cols.push({{ h: 'Item', w: 'auto', a: 'left' }});
  if (f.item_quantity) cols.push({{ h: 'Qty', w: '12%', a: 'center' }});
  if (f.item_unit_price) cols.push({{ h: 'Price', w: '18%', a: 'right' }});
  if (f.item_line_total) cols.push({{ h: 'Amount', w: '18%', a: 'right' }});

  if (cols.length) {{
    html += `<table style="width:100%;border-collapse:collapse;margin-bottom:8px;font-size:8px">`;
    html += `<tr style="background:${{theme.dark}};color:#fff">`;
    cols.forEach(c => {{ html += `<th style="padding:4px 6px;text-align:${{c.a}};font-size:8px">${{c.h}}</th>`; }});
    html += `</tr>`;

    // Sample rows
    const sampleItems = [
      {{ n: 1, item: 'Blue T-Shirt' + (f.item_sku ? ' (SKU-001)' : ''), qty: 5, price: '$20.00', total: '$100.00' }},
      {{ n: 2, item: 'Red Hoodie' + (f.item_sku ? ' (SKU-002)' : ''), qty: 2, price: '$45.00', total: '$90.00' }},
    ];
    sampleItems.forEach((row, i) => {{
      const bg = i % 2 === 1 ? '#f8fafc' : '#fff';
      html += `<tr style="background:${{bg}}">`;
      if (f.item_number) html += `<td style="padding:3px 6px;text-align:center">${{row.n}}</td>`;
      if (f.item_description) html += `<td style="padding:3px 6px">${{row.item}}</td>`;
      if (f.item_quantity) html += `<td style="padding:3px 6px;text-align:center">${{row.qty}}</td>`;
      if (f.item_unit_price) html += `<td style="padding:3px 6px;text-align:right">${{row.price}}</td>`;
      if (f.item_line_total) html += `<td style="padding:3px 6px;text-align:right">${{row.total}}</td>`;
      html += `</tr>`;
    }});
    html += `</table>`;
  }}

  // Totals
  html += `<div style="text-align:right;margin-bottom:10px">`;
  if (f.subtotal) html += `<div style="font-size:9px;color:#475569">Subtotal: $190.00</div>`;
  if (f.tax_line) html += `<div style="font-size:9px;color:#475569">GST (18%): $34.20</div>`;
  if (f.grand_total) {{
    html += `<div style="border-top:2px solid ${{theme.dark}};display:inline-block;padding-top:4px;margin-top:4px">`;
    html += `<span style="font-size:12px;font-weight:700;color:${{theme.dark}}">TOTAL: $224.20</span></div>`;
  }}
  html += `</div>`;

  // Footer section
  if (f.payment_terms) {{
    html += `<div style="font-size:7px;font-weight:700;color:${{theme.muted}};text-transform:uppercase;margin-bottom:2px">Payment Terms</div>`;
    html += `<div style="font-size:8px;color:#475569;margin-bottom:6px">Net 30</div>`;
  }}
  if (f.notes) {{
    html += `<div style="font-size:7px;font-weight:700;color:${{theme.muted}};text-transform:uppercase;margin-bottom:2px">Notes</div>`;
    html += `<div style="font-size:8px;color:#475569;margin-bottom:6px">Thank you for your order!</div>`;
  }}

  // Footer
  html += `<div style="text-align:center;padding-top:8px;border-top:1px solid #e2e8f0;margin-top:8px">`;
  if (f.footer_text) {{
    const footerText = ct.footer_text || 'Thank you for your business';
    html += `<div style="font-size:7px;color:#999;font-style:italic">${{footerText}}</div>`;
  }}
  if (f.powered_by_omnidesk) {{
    html += `<div style="font-size:7px;color:#ccc">Generated by OmniDesk</div>`;
  }}
  html += `</div>`;

  document.getElementById('invoice-preview').innerHTML = html;
}}

// Boot
init();
</script>
</body>
</html>"""
