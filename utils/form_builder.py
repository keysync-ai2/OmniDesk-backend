"""Form builder — generates professional themed HTML forms from schema definitions."""
import json

FORM_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — OmniDesk Form</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: {bg_color}; color: #212529; line-height: 1.6;
    min-height: 100vh; display: flex; justify-content: center; padding: 40px 20px;
  }}
  .form-wrapper {{
    width: 100%; max-width: 640px;
  }}
  .form-header {{
    background: {primary_color};
    color: white; padding: 32px; border-radius: 12px 12px 0 0;
  }}
  .form-header h1 {{ font-size: 1.6em; margin-bottom: 8px; }}
  .form-header .desc {{ opacity: 0.9; font-size: 0.95em; }}
  .form-body {{
    background: white; padding: 32px; border-radius: 0 0 12px 12px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  }}
  .field-group {{
    margin-bottom: 24px;
  }}
  .field-group label {{
    display: block; font-weight: 600; margin-bottom: 6px; color: #333;
  }}
  .field-group label .required {{
    color: #ea4335; margin-left: 2px;
  }}
  .field-group .hint {{
    font-size: 0.85em; color: #666; margin-bottom: 6px;
  }}
  input[type="text"], input[type="email"], input[type="tel"],
  input[type="number"], input[type="date"], input[type="url"],
  select, textarea {{
    width: 100%; padding: 10px 14px; border: 1.5px solid #dadce0;
    border-radius: 8px; font-size: 1em; font-family: inherit;
    transition: border-color 0.2s;
  }}
  input:focus, select:focus, textarea:focus {{
    outline: none; border-color: {primary_color};
    box-shadow: 0 0 0 3px {primary_color}22;
  }}
  textarea {{ resize: vertical; min-height: 100px; }}
  .radio-group, .checkbox-group {{
    display: flex; flex-direction: column; gap: 8px; margin-top: 4px;
  }}
  .radio-group label, .checkbox-group label {{
    font-weight: 400; display: flex; align-items: center; gap: 8px; cursor: pointer;
  }}
  input[type="file"] {{
    padding: 8px; border: 1.5px dashed #dadce0; border-radius: 8px;
    width: 100%; cursor: pointer;
  }}
  .submit-btn {{
    background: {primary_color}; color: white; border: none;
    padding: 14px 32px; border-radius: 8px; font-size: 1.05em;
    font-weight: 600; cursor: pointer; width: 100%;
    transition: opacity 0.2s;
  }}
  .submit-btn:hover {{ opacity: 0.9; }}
  .submit-btn:disabled {{ opacity: 0.5; cursor: not-allowed; }}
  .success-msg {{
    display: none; text-align: center; padding: 40px;
    background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  }}
  .success-msg h2 {{ color: #34a853; margin-bottom: 8px; }}
  .error-msg {{
    display: none; background: #fce8e6; color: #c5221f;
    padding: 12px 16px; border-radius: 8px; margin-bottom: 16px;
  }}
  .form-footer {{
    text-align: center; padding: 16px; color: #999; font-size: 0.8em;
  }}
</style>
</head>
<body>
<div class="form-wrapper">
  <div id="form-container">
    <div class="form-header">
      <h1>{title}</h1>
      <div class="desc" id="form-description"></div>
    </div>
    <div class="form-body">
      <div class="error-msg" id="error-msg"></div>
      <form id="omnidesk-form" enctype="multipart/form-data">
        {fields_html}
        <div class="field-group">
          <button type="submit" class="submit-btn" id="submit-btn">Submit</button>
        </div>
      </form>
    </div>
  </div>
  <div class="success-msg" id="success-msg">
    <h2>Thank you!</h2>
    <p>Your response has been submitted successfully.</p>
  </div>
  <div class="form-footer">Powered by OmniDesk</div>
</div>

<script>
// Render markdown description
const descMd = {description_json};
if (descMd) {{
  document.getElementById('form-description').innerHTML = marked.parse(descMd);
}}

const form = document.getElementById('omnidesk-form');
const submitBtn = document.getElementById('submit-btn');
const errorMsg = document.getElementById('error-msg');

form.addEventListener('submit', async function(e) {{
  e.preventDefault();
  submitBtn.disabled = true;
  submitBtn.textContent = 'Submitting...';
  errorMsg.style.display = 'none';

  const formData = new FormData(form);
  const data = {{}};
  for (const [key, value] of formData.entries()) {{
    if (data[key]) {{
      if (Array.isArray(data[key])) data[key].push(value);
      else data[key] = [data[key], value];
    }} else {{
      data[key] = value;
    }}
  }}

  try {{
    const resp = await fetch('{submit_url}', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(data),
    }});
    const result = await resp.json();
    if (resp.ok) {{
      document.getElementById('form-container').style.display = 'none';
      document.getElementById('success-msg').style.display = 'block';
    }} else {{
      errorMsg.textContent = result.error || 'Submission failed. Please try again.';
      errorMsg.style.display = 'block';
      submitBtn.disabled = false;
      submitBtn.textContent = 'Submit';
    }}
  }} catch (err) {{
    errorMsg.textContent = 'Network error. Please try again.';
    errorMsg.style.display = 'block';
    submitBtn.disabled = false;
    submitBtn.textContent = 'Submit';
  }}
}});
</script>
</body>
</html>"""

THEMES = {
    "default": {"primary_color": "#1a73e8", "bg_color": "#f0f2f5"},
    "dark": {"primary_color": "#bb86fc", "bg_color": "#121212"},
    "green": {"primary_color": "#34a853", "bg_color": "#f0faf3"},
    "red": {"primary_color": "#ea4335", "bg_color": "#fef7f6"},
    "orange": {"primary_color": "#f57c00", "bg_color": "#fff8f0"},
}

INPUT_TYPE_MAP = {
    "text": "text",
    "email": "email",
    "phone": "tel",
    "number": "number",
    "date": "date",
    "url": "url",
}


def _build_field_html(field):
    """Generate HTML for a single form field."""
    name = field["name"]
    label = field.get("label", name.replace("_", " ").title())
    field_type = field.get("type", "text")
    required = field.get("required", False)
    placeholder = field.get("placeholder", "")
    options = field.get("options", [])
    hint = field.get("hint", "")

    req_star = '<span class="required">*</span>' if required else ""
    req_attr = "required" if required else ""
    hint_html = f'<div class="hint">{hint}</div>' if hint else ""

    if field_type == "textarea":
        return f"""<div class="field-group">
  <label>{label}{req_star}</label>{hint_html}
  <textarea name="{name}" placeholder="{placeholder}" {req_attr}></textarea>
</div>"""

    if field_type == "select":
        opts_html = f'<option value="">Select {label}...</option>'
        for opt in options:
            opts_html += f'<option value="{opt}">{opt}</option>'
        return f"""<div class="field-group">
  <label>{label}{req_star}</label>{hint_html}
  <select name="{name}" {req_attr}>{opts_html}</select>
</div>"""

    if field_type == "radio":
        opts_html = ""
        for opt in options:
            opts_html += f'<label><input type="radio" name="{name}" value="{opt}" {req_attr}> {opt}</label>'
        return f"""<div class="field-group">
  <label>{label}{req_star}</label>{hint_html}
  <div class="radio-group">{opts_html}</div>
</div>"""

    if field_type == "checkbox":
        opts_html = ""
        for opt in options:
            opts_html += f'<label><input type="checkbox" name="{name}" value="{opt}"> {opt}</label>'
        return f"""<div class="field-group">
  <label>{label}{req_star}</label>{hint_html}
  <div class="checkbox-group">{opts_html}</div>
</div>"""

    if field_type == "file":
        return f"""<div class="field-group">
  <label>{label}{req_star}</label>{hint_html}
  <input type="file" name="{name}" {req_attr}>
</div>"""

    # Default: text-like input
    input_type = INPUT_TYPE_MAP.get(field_type, "text")
    return f"""<div class="field-group">
  <label>{label}{req_star}</label>{hint_html}
  <input type="{input_type}" name="{name}" placeholder="{placeholder}" {req_attr}>
</div>"""


def build_form_html(title, description, fields, submit_url, theme="default"):
    """Build a complete HTML form page from field definitions.

    Args:
        title: Form title
        description: Markdown description (rendered client-side)
        fields: List of field defs [{name, type, label, required, options, placeholder, hint}]
        submit_url: API endpoint for form submission
        theme: Theme name (default, dark, green, red, orange)

    Returns:
        Complete HTML string
    """
    theme_config = THEMES.get(theme, THEMES["default"])
    fields_html = "\n".join(_build_field_html(f) for f in fields)

    html = FORM_HTML_TEMPLATE.format(
        title=title,
        description_json=json.dumps(description or ""),
        fields_html=fields_html,
        submit_url=submit_url,
        **theme_config,
    )
    return html
