
import re

# Regexes tailored to the user's Flipkart-style PDF text
RE_ORDER_ID = re.compile(r"\b(OD[A-Z0-9]{18})\b")
RE_INVOICE = re.compile(r"Invoice\s*No:\s*([A-Za-z0-9\-]+)", re.IGNORECASE)
RE_DATE = re.compile(r"Order\s*Date:\s*([0-3]?\d[-/][01]?\d[-/]\d{2,4})", re.IGNORECASE)
RE_NAME = re.compile(r"Name:\s*([^,\n]+)")
RE_TOTAL_QTY = re.compile(r"TOTAL\s*QTY:\s*(\d{1,2})", re.IGNORECASE)

# SKU appears within "... Purifier | AT0001 | IMEI/SrNo ..."
RE_SKU_IN_CONTEXT = re.compile(r"Purifier\s*\|\s*(AT\d{4})\s*\|\s*IMEI/SrNo", re.IGNORECASE)
RE_SKU_FALLBACK = re.compile(r"\b(AT\d{4})\b")

def normalize_ddmmyyyy(s: str|None) -> str|None:
    if not s:
        return None
    # Accept dd-mm-yyyy or dd/mm/yyyy and normalize to dd/mm/yyyy
    parts = re.split(r"[-/]", s.strip())
    if len(parts) == 3:
        d, m, y = parts
        if len(y) == 2:
            y = ("20" + y) if int(y) < 50 else ("19" + y)
        return f"{int(d):02d}/{int(m):02d}/{int(y):04d}"
    return s

def parse_order_page(text: str):
    if not text:
        return None
    order_id_m = RE_ORDER_ID.search(text)
    date_m = RE_DATE.search(text)
    inv_m = RE_INVOICE.search(text)
    name_m = RE_NAME.search(text)

    # SKUs: prefer context-based; if none found, fallback to any ATdddd
    context_skus = RE_SKU_IN_CONTEXT.findall(text)
    skus = context_skus if context_skus else RE_SKU_FALLBACK.findall(text)

    # Qty: if explicit TOTAL QTY, use that; otherwise count per SKU occurrence
    qty_total = None
    m = RE_TOTAL_QTY.search(text)
    if m:
        qty_total = int(m.group(1))

    items = []
    if skus:
        # If multiple occurrences of same SKU, count occurrences as qty if TOTAL QTY isn't trustworthy per-line
        counts = {}
        for s in skus:
            counts[s] = counts.get(s, 0) + 1
        for s, c in counts.items():
            items.append({"sku": s, "qty": c})
    elif qty_total is not None:
        # No SKU found but qty exists -> create a placeholder
        items.append({"sku": "AT0000", "qty": qty_total})

    if not order_id_m or not items:
        return None

    return {
        "order_id": order_id_m.group(1),
        "invoice_number": inv_m.group(1) if inv_m else None,
        "name": name_m.group(1).strip() if name_m else None,
        "date": date_m.group(1) if date_m else None,
        "items": items,
    }
