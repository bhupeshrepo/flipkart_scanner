
import os
import re
import json
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_file
from processor import PDFOrderProcessor, slice_and_build_order_pdf
from parsers import parse_order_page, normalize_ddmmyyyy

app = Flask(__name__, template_folder="templates", static_folder="static")

STORE_DIR = os.path.join(os.path.dirname(__file__), "out")
DB_PATH = os.path.join(STORE_DIR, "orders.json")
os.makedirs(STORE_DIR, exist_ok=True)

def _load_db():
    if not os.path.exists(DB_PATH):
        return {"orders": []}
    with open(DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_db(db):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload():
    """1) User uploads PDF -> parse per page and store rows for the table."""
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    pdf_path = os.path.join(STORE_DIR, "uploaded.pdf")
    f.save(pdf_path)

    proc = PDFOrderProcessor(pdf_path)
    pages = proc.doc_page_count()

    db = _load_db()

    # Parse every page into an order "envelope"
    orders_by_id = {o["order_id"]: o for o in db["orders"]}

    for page_num in range(pages):
        text = proc.page_text(page_num)
        parsed = parse_order_page(text)

        if not parsed:
            # not fatal; skip page
            continue

        order_id = parsed["order_id"]
        # Build line items from SKUs discovered
        items = []
        for sku_item in parsed["items"]:
            sku = sku_item["sku"]
            qty = sku_item["qty"]
            items.append({
                "sku": sku,
                "qty": qty,
                # Collect product_ids via scanning; store list for qty>1
                "product_ids": []
            })

        order_obj = {
            "order_id": order_id,
            "invoice_number": parsed.get("invoice_number"),
            "customer_name": parsed.get("name"),
            "date": normalize_ddmmyyyy(parsed.get("date")),
            "page_index": page_num,
            "pdf_path": pdf_path,
            "status": "pending",
            "items": items,
        }

        if order_id in orders_by_id:
            # Deduplicate/merge (idempotent)
            # Prefer latest parse for name/date/invoice_number
            existing = orders_by_id[order_id]
            existing.update({k: order_obj[k] for k in ["invoice_number", "customer_name", "date", "page_index", "pdf_path"]})
            # Merge items by SKU and adjust qty if needed
            by_sku = {it["sku"]: it for it in existing["items"]}
            for it in items:
                if it["sku"] in by_sku:
                    by_sku[it["sku"]]["qty"] = max(by_sku[it["sku"]]["qty"], it["qty"])
                else:
                    existing["items"].append(it)
        else:
            db["orders"].append(order_obj)

    _save_db(db)
    return jsonify({"ok": True, "orders": db["orders"]})

@app.route("/orders", methods=["GET"])
def list_orders():
    db = _load_db()
    # shape data for the frontend grid
    rows = []
    for o in db["orders"]:
        for it in o["items"]:
            rows.append({
                "order_id": o["order_id"],
                "invoice_number": o.get("invoice_number"),
                "customer_name": o.get("customer_name"),
                "sku": it["sku"],
                "qty": it["qty"],
                "product_ids": it["product_ids"],
                "status": o["status"]
            })
    return jsonify({"ok": True, "rows": rows})

SCAN_PATTERN = re.compile(r"^(AT\d{4})[-_:]?([A-Za-z0-9]{5})$", re.IGNORECASE)
# sku, product_id = m.group(1).upper(), m.group(2).upper()


@app.route("/scan", methods=["POST"])
def scan():
    """
    3) User scans barcode like 'AT0001-c0001' -> fill product_id into first matching row
       for the first *pending* order that still needs that SKU, FIFO.
    """
    payload = request.get_json(force=True)
    code = payload.get("code","").strip()
    m = SCAN_PATTERN.match(code)
    if not m:
        return jsonify({"ok": False, "error": "Invalid code format. Expect AT0001-c0001"}), 400

    sku, product_id = m.group(1), m.group(2)
    db = _load_db()

    updated = False
    completed_order_id = None
    for o in db["orders"]:
        if o["status"] != "pending":
            continue
        # find sku line
        for it in o["items"]:
            if it["sku"] == sku:
                # if already complete for this item?
                if len(it["product_ids"]) >= int(it["qty"]):
                    continue
                # append if not duplicate
                if product_id not in it["product_ids"]:
                    it["product_ids"].append(product_id)
                    updated = True
                break

        # check if the whole order is complete now
        if updated:
            all_ok = True
            for it in o["items"]:
                if len(it["product_ids"]) != int(it["qty"]):
                    all_ok = False
                    break
            if all_ok:
                # 4) when all SKUs scanned -> slice + build new 3-page PDF (label + invoice x2) -> status ready
                out_pdf = os.path.join(STORE_DIR, f"{o['order_id']}.pdf")
                try:
                    slice_and_build_order_pdf(
                        source_pdf=o["pdf_path"],
                        page_index=o["page_index"],
                        out_pdf=out_pdf
                    )
                    o["status"] = "ready"
                    o["out_pdf"] = out_pdf
                    completed_order_id = o["order_id"]
                except Exception as e:
                    o["status"] = "error"
                    o["error"] = str(e)
            break

    if updated:
        _save_db(db)

    return jsonify({"ok": updated, "completed_order": completed_order_id, "orders": db["orders"]})

@app.route("/download/<order_id>", methods=["GET"])
def download(order_id):
    db = _load_db()
    for o in db["orders"]:
        if o["order_id"] == order_id and o.get("out_pdf") and os.path.exists(o["out_pdf"]):
            return send_file(o["out_pdf"], as_attachment=True)
    return jsonify({"ok": False, "error": "Not found or not ready yet"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
