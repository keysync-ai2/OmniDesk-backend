"""Professional PDF invoice builder using fpdf2.

Supports:
- Customizable company info (name, address, phone, email, logo)
- Multi-currency (INR, USD, EUR, GBP, AED, SGD, etc.)
- Configurable tax label (GST, VAT, Sales Tax)
- Custom payment terms and footer
- Locale-aware number formatting
- Template-based field toggling (28 fields)
- 5 predefined color themes
- Logo rendering from S3
- Brand name and tagline support
"""
import os
import boto3
from fpdf import FPDF


# PDF-safe currency symbols (Helvetica-compatible ASCII fallbacks)
PDF_CURRENCY_MAP = {
    "₹": "Rs.",
    "€": "EUR ",
    "£": "GBP ",
    "¥": "JPY ",
}

# Currency locale formatting
LOCALE_FORMATS = {
    "en-IN": {"decimal": ".", "thousands": ",", "date": "%d %b %Y"},
    "en-US": {"decimal": ".", "thousands": ",", "date": "%b %d, %Y"},
    "en-GB": {"decimal": ".", "thousands": ",", "date": "%d %b %Y"},
    "en-EU": {"decimal": ",", "thousands": ".", "date": "%d.%m.%Y"},
    "en-AE": {"decimal": ".", "thousands": ",", "date": "%d %b %Y"},
    "en-SG": {"decimal": ".", "thousands": ",", "date": "%d %b %Y"},
}

# ── Invoice Themes ─────────────────────────────────────────────────────
# Each theme defines RGB tuples for header, dark, muted, and accent colors.
INVOICE_THEMES = {
    "professional_blue": {
        "header": (37, 99, 235),
        "dark": (30, 41, 59),
        "muted": (100, 116, 139),
        "accent": (37, 99, 235),
    },
    "forest_green": {
        "header": (22, 101, 52),
        "dark": (26, 46, 26),
        "muted": (107, 114, 128),
        "accent": (22, 163, 74),
    },
    "charcoal": {
        "header": (55, 65, 81),
        "dark": (17, 24, 39),
        "muted": (107, 114, 128),
        "accent": (75, 85, 99),
    },
    "warm_terracotta": {
        "header": (180, 83, 9),
        "dark": (69, 26, 3),
        "muted": (120, 113, 108),
        "accent": (217, 119, 6),
    },
    "royal_purple": {
        "header": (124, 58, 237),
        "dark": (46, 16, 101),
        "muted": (107, 114, 128),
        "accent": (139, 92, 246),
    },
}

# Default template config (all fields ON)
DEFAULT_TEMPLATE_CONFIG = {
    "fields": {
        "company_logo": True, "company_name": True, "brand_name": False,
        "company_address": True, "company_phone": True, "company_email": True,
        "tagline": False, "invoice_number": True, "invoice_date": True,
        "due_date": True, "order_reference": True, "customer_name": True,
        "customer_email": True, "customer_phone": True, "customer_address": False,
        "item_number": True, "item_sku": True, "item_description": True,
        "item_quantity": True, "item_unit_price": True, "item_line_total": True,
        "subtotal": True, "tax_line": True, "grand_total": True,
        "payment_terms": True, "notes": True, "footer_text": True,
        "powered_by_omnidesk": True,
    },
    "custom_text": {
        "brand_name": "", "tagline": "", "invoice_prefix": "INV", "footer_text": "",
    },
    "theme": "professional_blue",
}


def _pdf_safe(text):
    """Replace Unicode characters that Helvetica can't render."""
    for unicode_char, ascii_fallback in PDF_CURRENCY_MAP.items():
        text = text.replace(unicode_char, ascii_fallback)
    return text


def _format_amount(amount, currency_symbol, locale="en-IN"):
    """Format amount with currency symbol and locale-aware separators."""
    fmt = LOCALE_FORMATS.get(locale, LOCALE_FORMATS["en-IN"])
    safe_symbol = PDF_CURRENCY_MAP.get(currency_symbol, currency_symbol)
    parts = f"{amount:,.2f}".split(".")
    int_part = parts[0].replace(",", fmt["thousands"])
    dec_part = parts[1]
    return f"{safe_symbol}{int_part}{fmt['decimal']}{dec_part}"


def _download_logo(logo_s3_key):
    """Download logo from S3 to /tmp and return the local path, or None."""
    if not logo_s3_key:
        return None
    try:
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket = os.environ.get("S3_BUCKET", "omnidesk-files-577397739686")
        ext = logo_s3_key.rsplit(".", 1)[-1] if "." in logo_s3_key else "png"
        local_path = f"/tmp/logo.{ext}"
        s3.download_file(bucket, logo_s3_key, local_path)
        return local_path
    except Exception:
        return None


class InvoicePDF(FPDF):
    """Custom PDF class for OmniDesk invoices."""

    def __init__(self, settings, template=None):
        super().__init__()
        self.settings = settings
        self.template = template or DEFAULT_TEMPLATE_CONFIG
        self.fields = self.template.get("fields", DEFAULT_TEMPLATE_CONFIG["fields"])
        self.custom_text = self.template.get("custom_text", DEFAULT_TEMPLATE_CONFIG["custom_text"])
        theme_name = self.template.get("theme", "professional_blue")
        self.theme = INVOICE_THEMES.get(theme_name, INVOICE_THEMES["professional_blue"])
        self.set_auto_page_break(auto=True, margin=25)

    def header(self):
        pass  # We handle header manually in build()

    def footer(self):
        self.set_y(-20)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)

        if self.fields.get("footer_text", True):
            custom_footer = self.custom_text.get("footer_text", "").strip()
            footer_text = custom_footer if custom_footer else self.settings.get("invoice_footer", "Thank you for your business")
            footer_text = _pdf_safe(footer_text)
            self.cell(0, 5, footer_text, align="C", new_x="LMARGIN", new_y="NEXT")

        if self.fields.get("powered_by_omnidesk", True):
            self.cell(0, 5, "Generated by OmniDesk", align="C")


def build_invoice_pdf(invoice_data, items, order, settings, template=None):
    """Build a professional PDF invoice.

    Args:
        invoice_data: dict with invoice_number, subtotal, tax_rate, tax_amount, total_amount, due_date, created_at
        items: list of dicts with product_name, sku, quantity, unit_price, total_price
        order: dict with order_number, customer_name, customer_email, customer_phone, customer_address
        settings: dict from org_settings (company_name, currency_symbol, tax_label, etc.)
        template: optional dict with fields, custom_text, theme from invoice_templates table

    Returns:
        bytes: PDF file content
    """
    if template is None:
        template = DEFAULT_TEMPLATE_CONFIG

    fields = template.get("fields", DEFAULT_TEMPLATE_CONFIG["fields"])
    custom_text = template.get("custom_text", DEFAULT_TEMPLATE_CONFIG["custom_text"])
    theme_name = template.get("theme", "professional_blue")
    theme = INVOICE_THEMES.get(theme_name, INVOICE_THEMES["professional_blue"])

    currency = settings.get("currency_symbol", "₹")
    locale = settings.get("locale", "en-IN")
    tax_label = settings.get("tax_label", "GST")

    pdf = InvoicePDF(settings, template)
    pdf.add_page()

    # ── Logo ──────────────────────────────────────────────────────────
    logo_path = None
    if fields.get("company_logo", True):
        logo_s3_key = template.get("logo_s3_key") or settings.get("company_logo_s3_key")
        logo_path = _download_logo(logo_s3_key)

    # ── Company Header ────────────────────────────────────────────────
    header_start_x = 10
    if logo_path:
        try:
            pdf.image(logo_path, x=10, y=10, h=14)
            header_start_x = 30
        except Exception:
            pass

    if fields.get("company_name", True):
        pdf.set_x(header_start_x)
        pdf.set_font("Helvetica", "B", 22)
        pdf.set_text_color(*theme["header"])
        # Use brand name if enabled, otherwise company name
        if fields.get("brand_name", False) and custom_text.get("brand_name", "").strip():
            display_name = custom_text["brand_name"].strip()
        else:
            display_name = settings.get("company_name", "OmniDesk")
        pdf.cell(100, 10, display_name, new_x="RIGHT")
    else:
        pdf.cell(100, 10, "", new_x="RIGHT")

    pdf.set_font("Helvetica", "B", 28)
    pdf.set_text_color(*theme["dark"])
    pdf.cell(0, 10, "INVOICE", align="R", new_x="LMARGIN", new_y="NEXT")

    # Tagline
    if fields.get("tagline", False) and custom_text.get("tagline", "").strip():
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*theme["muted"])
        pdf.cell(0, 5, custom_text["tagline"].strip(), new_x="LMARGIN", new_y="NEXT")

    # Company details (below name)
    company_parts = []
    if fields.get("company_address", True) and settings.get("company_address"):
        company_parts.append(settings["company_address"])
    if fields.get("company_phone", True) and settings.get("company_phone"):
        company_parts.append(settings["company_phone"])
    if fields.get("company_email", True) and settings.get("company_email"):
        company_parts.append(settings["company_email"])

    if company_parts:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*theme["muted"])
        pdf.cell(0, 5, " | ".join(company_parts), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(5)

    # ── Divider ───────────────────────────────────────────────────────
    pdf.set_draw_color(226, 232, 240)
    pdf.set_line_width(0.5)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(8)

    # ── Invoice Meta + Bill To (side by side) ─────────────────────────
    y_start = pdf.get_y()

    # Left: Invoice Details
    has_meta = any(fields.get(f, True) for f in ["invoice_number", "invoice_date", "due_date", "order_reference"])
    if has_meta:
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*theme["muted"])
        pdf.cell(60, 5, "INVOICE DETAILS", new_x="LMARGIN", new_y="NEXT")

        if fields.get("invoice_number", True):
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*theme["dark"])
            pdf.cell(60, 6, invoice_data["invoice_number"], new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(71, 85, 105)

        if fields.get("invoice_date", True):
            pdf.cell(60, 5, f"Date: {invoice_data['created_at']}", new_x="LMARGIN", new_y="NEXT")

        if fields.get("due_date", True):
            pdf.cell(60, 5, f"Due: {invoice_data['due_date'] or 'On receipt'}", new_x="LMARGIN", new_y="NEXT")

        if fields.get("order_reference", True):
            pdf.cell(60, 5, f"Order: {order['order_number']}", new_x="LMARGIN", new_y="NEXT")

    y_after_left = pdf.get_y()

    # Right: Bill To
    has_customer = any(fields.get(f, True) for f in ["customer_name", "customer_email", "customer_phone", "customer_address"])
    if has_customer:
        pdf.set_y(y_start)
        pdf.set_x(120)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*theme["muted"])
        pdf.cell(70, 5, "BILL TO", new_x="LEFT", new_y="NEXT")

        if fields.get("customer_name", True):
            pdf.set_x(120)
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*theme["dark"])
            pdf.cell(70, 6, order.get("customer_name", ""), new_x="LEFT", new_y="NEXT")

        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(71, 85, 105)

        if fields.get("customer_address", False) and order.get("customer_address"):
            pdf.set_x(120)
            pdf.cell(70, 5, order["customer_address"], new_x="LEFT", new_y="NEXT")

        if fields.get("customer_email", True) and order.get("customer_email"):
            pdf.set_x(120)
            pdf.cell(70, 5, order["customer_email"], new_x="LEFT", new_y="NEXT")

        if fields.get("customer_phone", True) and order.get("customer_phone"):
            pdf.set_x(120)
            pdf.cell(70, 5, order["customer_phone"], new_x="LEFT", new_y="NEXT")

    y_after_right = pdf.get_y()
    pdf.set_y(max(y_after_left, y_after_right) + 8)

    # ── Items Table ───────────────────────────────────────────────────
    # Build dynamic columns based on visible fields
    col_defs = []
    if fields.get("item_number", True):
        col_defs.append({"key": "number", "header": "#", "width": 12, "align": "C"})
    if fields.get("item_description", True):
        col_defs.append({"key": "item", "header": "Item", "width": 0, "align": "L"})  # width 0 = flexible
    if fields.get("item_quantity", True):
        col_defs.append({"key": "qty", "header": "Qty", "width": 25, "align": "C"})
    if fields.get("item_unit_price", True):
        col_defs.append({"key": "price", "header": "Unit Price", "width": 35, "align": "R"})
    if fields.get("item_line_total", True):
        col_defs.append({"key": "total", "header": "Amount", "width": 40, "align": "R"})

    if not col_defs:
        # At minimum show item and total
        col_defs = [
            {"key": "item", "header": "Item", "width": 0, "align": "L"},
            {"key": "total", "header": "Amount", "width": 40, "align": "R"},
        ]

    # Calculate flexible column width
    fixed_width = sum(c["width"] for c in col_defs)
    available = 190  # page width minus margins
    for c in col_defs:
        if c["width"] == 0:
            c["width"] = available - fixed_width

    # Table header
    pdf.set_fill_color(*theme["dark"])
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)

    for i, col in enumerate(col_defs):
        is_last = i == len(col_defs) - 1
        pdf.cell(col["width"], 8, col["header"], align=col["align"], fill=True,
                 new_x="LMARGIN" if is_last else "RIGHT",
                 new_y="NEXT" if is_last else "TOP")

    # Table rows
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(51, 65, 85)
    fill = False
    show_sku = fields.get("item_sku", True)
    for idx, item in enumerate(items, 1):
        if fill:
            pdf.set_fill_color(248, 250, 252)
        else:
            pdf.set_fill_color(255, 255, 255)

        # Build row values based on visible columns
        item_label = item["product_name"]
        if show_sku:
            item_label = f"{item['product_name']} ({item['sku']})"

        row_values = {}
        row_values["number"] = str(idx)
        row_values["item"] = item_label
        row_values["qty"] = str(item["quantity"])
        row_values["price"] = _format_amount(item["unit_price"], currency, locale)
        row_values["total"] = _format_amount(item["total_price"], currency, locale)

        for i, col in enumerate(col_defs):
            is_last = i == len(col_defs) - 1
            pdf.cell(col["width"], 7, row_values.get(col["key"], ""), align=col["align"], fill=True,
                     new_x="LMARGIN" if is_last else "RIGHT",
                     new_y="NEXT" if is_last else "TOP")
        fill = not fill

    # Bottom border
    pdf.set_draw_color(226, 232, 240)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)

    # ── Totals ────────────────────────────────────────────────────────
    totals_x = 120
    totals_label_w = 40
    totals_val_w = 40

    if fields.get("subtotal", True):
        pdf.set_x(totals_x)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(totals_label_w, 7, "Subtotal", align="R")
        pdf.cell(totals_val_w, 7, _format_amount(invoice_data["subtotal"], currency, locale),
                 align="R", new_x="LMARGIN", new_y="NEXT")

    if fields.get("tax_line", True) and invoice_data["tax_rate"] > 0:
        pdf.set_x(totals_x)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(totals_label_w, 7, f"{tax_label} ({invoice_data['tax_rate']}%)", align="R")
        pdf.cell(totals_val_w, 7, _format_amount(invoice_data["tax_amount"], currency, locale),
                 align="R", new_x="LMARGIN", new_y="NEXT")

    if fields.get("grand_total", True):
        # Total line
        pdf.set_draw_color(*theme["dark"])
        pdf.line(totals_x, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(2)

        pdf.set_x(totals_x)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(*theme["dark"])
        pdf.cell(totals_label_w, 9, "TOTAL", align="R")
        pdf.cell(totals_val_w, 9, _format_amount(invoice_data["total_amount"], currency, locale),
                 align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(10)

    # ── Payment Terms & Notes ─────────────────────────────────────────
    if fields.get("payment_terms", True):
        payment_terms = settings.get("payment_terms", "Net 30")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*theme["muted"])
        pdf.cell(0, 5, "PAYMENT TERMS", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(0, 5, payment_terms, new_x="LMARGIN", new_y="NEXT")

    if fields.get("notes", True) and invoice_data.get("notes"):
        pdf.ln(5)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*theme["muted"])
        pdf.cell(0, 5, "NOTES", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(71, 85, 105)
        pdf.multi_cell(0, 5, invoice_data["notes"])

    # Return PDF as bytes
    return pdf.output()
