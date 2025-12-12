
import os
import re
import json
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_file
from processor import PDFOrderProcessor, slice_and_build_order_pdf
from parsers import parse_order_page, normalize_ddmmyyyy
from PyPDF2 import PdfReader, PdfWriter
from db import (
    load_master_data,
    get_sku_info,
    is_noscan_sku,
    get_print_counts_for_sku,
)

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
    load_master_data()
    db = _load_db()
    rows = []
    for o in db["orders"]:
        for it in o["items"]:
            info = get_sku_info(it["sku"])
            rows.append({
                "order_id": o["order_id"],
                "invoice_number": o.get("invoice_number"),
                "customer_name": o.get("customer_name"),
                "sku": it["sku"],
                "display_name": info.display_name,
                "qty": it["qty"],
                "product_ids": it["product_ids"],
                "status": o["status"]
            })
    return jsonify({"ok": True, "rows": rows})

SCAN_PATTERN = re.compile(r"^(AT\d{4})[-_:]?([A-Za-z0-9]{5})$", re.IGNORECASE)
# sku, product_id = m.group(1).upper(), m.group(2).upper()

def _is_order_complete(order_obj) -> bool:
    """Completion: all Compulsory/Loose items have full product_ids.
       Extras (NoScan SKUs) are ignored in this check."""
    load_master_data()
    for it in order_obj.get("items", []):
        sku = it["sku"].upper()
        info = get_sku_info(sku)

        if is_noscan_sku(sku):
            # Extras: never block completion
            continue

        qty = int(it["qty"])
        filled = len(it["product_ids"])
        if filled < qty:
            return False
    return True

@app.route("/scan", methods=["POST"])
def scan():
    """
    Scan handler:
    - Loose: 'AT0001' is valid (one scan = +1 unit)
    - Compulsory: 'AT0001-A0001' (or A001) required; normalized to A0001
    - NoScan (extras): never need scanning; ignored in completion check
    """
    try:
        load_master_data()
        payload = request.get_json(force=True)
        code_raw = (payload.get("code") or "").strip().upper()

        if not code_raw:
            return jsonify({"ok": False, "error": "No barcode provided"}), 400

        # Try pure-SKU first (for Loose SKUs)
        sku_only_match = re.match(r"^(AT\d{4})$", code_raw, re.IGNORECASE)

        sku = None
        product_id = None

        if sku_only_match:
            sku = sku_only_match.group(1).upper()
            info = get_sku_info(sku)
            if info.type == "Compulsory":
                return jsonify({
                    "ok": False,
                    "error": f"SKU {sku} is Compulsory — scan with product id (e.g. {sku}-A0001)"
                }), 400
            # Loose: we'll create an internal token below
        else:
            # Compulsory (or explicit loose) with product-id
            m = re.match(r"^(AT\d{4})[-_:]?([A-Z])(\d{1,4})$", code_raw, re.IGNORECASE)
            if not m:
                return jsonify({
                    "ok": False,
                    "error": "Invalid code format. Expected AT0001 or AT0001-A001 / AT0001-A0001"
                }), 400

            sku, letter, digits = m.groups()
            sku = sku.upper()
            product_id = f"{letter}{int(digits):04d}"

        info = get_sku_info(sku)

        db = _load_db()
        updated = False
        completed_order_id = None

        for o in db["orders"]:
            if o["status"] != "pending":
                continue

            for it in o["items"]:
                if it["sku"].upper() != sku:
                    continue

                # Extras/NoScan: ignore scans
                if is_noscan_sku(sku):
                    continue

                qty = int(it["qty"])

                # Ensure product_ids list exists
                if "product_ids" not in it or it["product_ids"] is None:
                    it["product_ids"] = []

                # If already full, skip
                if len(it["product_ids"]) >= qty:
                    continue

                if info.type == "Loose" and product_id is None:
                    # Loose + bare SKU → auto-generate token
                    token_idx = len(it["product_ids"]) + 1
                    token = f"SCAN-{sku}-{token_idx:04d}"
                    it["product_ids"].append(token)
                    updated = True
                else:
                    # Compulsory or explicit product-id
                    if product_id not in it["product_ids"]:
                        it["product_ids"].append(product_id)
                        updated = True

                if updated:
                    break  # stop scanning items for this order

            if updated:
                if _is_order_complete(o):
                    labels, invoices = get_print_counts_for_sku(sku)
                    out_pdf = os.path.join(STORE_DIR, f"{o['order_id']}.pdf")
                    try:
                        slice_and_build_order_pdf(
                            source_pdf=o["pdf_path"],
                            page_index=o["page_index"],
                            out_pdf=out_pdf,
                            labels=labels,
                            invoices=invoices,
                        )
                        o["status"] = "ready"
                        o["out_pdf"] = out_pdf
                        completed_order_id = o["order_id"]
                    except Exception as e:
                        o["status"] = "error"
                        o["error"] = str(e)
                break  # we handled this scan

        if updated:
            _save_db(db)
        else:
            print(f"[SCAN WARN] SKU {sku} not found or already complete.")

        return jsonify({"ok": updated, "completed_order": completed_order_id, "orders": db["orders"]})

    except Exception as e:
        print(f"[SCAN FATAL ERROR] {e}")
        return jsonify({"ok": False, "error": f"Unexpected error: {e}"}), 500

@app.route("/bulk_print", methods=["POST"])
def bulk_print():
    """
    Bulk print for a single SKU:
    - Treat selected SKU as Loose, regardless of master type
    - Auto-assign product_ids: BULK-{sku}-{0001..}
    - Only considers orders that contain exactly one SKU (single-SKU orders)
    - Marks those orders as 'ready' and generates PDFs
    - Merges them into one bulk PDF and returns download URL
    """
    try:
        load_master_data()
        payload = request.get_json(force=True)
        sku = (payload.get("sku") or "").strip().upper()

        if not sku:
            return jsonify({"ok": False, "error": "SKU is required"}), 400

        db = _load_db()

        pdf_paths: list[str] = []
        bulk_count = 0

        for o in db["orders"]:
            items = o.get("items", [])
            if len(items) != 1:
                continue  # bulk-print is only for single-SKU orders

            it = items[0]
            if it["sku"].upper() != sku:
                continue

            qty = int(it["qty"])
            if "product_ids" not in it or it["product_ids"] is None:
                it["product_ids"] = []

            # Treat as Loose irrespective of master type
            # Fill product_ids up to qty using BULK tokens
            while len(it["product_ids"]) < qty:
                token_idx = len(it["product_ids"]) + 1
                token = f"BULK-{sku}-{token_idx:04d}"
                if token not in it["product_ids"]:
                    it["product_ids"].append(token)

            # Now treat as complete and generate per-order PDF
            labels, invoices = get_print_counts_for_sku(sku)
            out_pdf = os.path.join(STORE_DIR, f"{o['order_id']}.pdf")

            try:
                slice_and_build_order_pdf(
                    source_pdf=o["pdf_path"],
                    page_index=o["page_index"],
                    out_pdf=out_pdf,
                    labels=labels,
                    invoices=invoices,
                )
                o["status"] = "ready"
                o["out_pdf"] = out_pdf
                pdf_paths.append(out_pdf)
                bulk_count += 1
            except Exception as e:
                o["status"] = "error"
                o["error"] = str(e)

        if bulk_count == 0:
            _save_db(db)
            return jsonify({
                "ok": False,
                "error": f"No single-SKU orders found for {sku}"
            }), 404

        # Merge all PDFs into one
        bulk_pdf_path = os.path.join(STORE_DIR, f"bulk_{sku}.pdf")
        writer = PdfWriter()
        for path in pdf_paths:
            try:
                reader = PdfReader(path)
                for p in reader.pages:
                    writer.add_page(p)
            except Exception as e:
                print(f"[BULK MERGE ERROR] {path}: {e}")

        with open(bulk_pdf_path, "wb") as f:
            writer.write(f)

        _save_db(db)

        return jsonify({
            "ok": True,
            "sku": sku,
            "count": bulk_count,
            "bulk_pdf_url": f"/bulk_download/{sku}"
        })

    except Exception as e:
        print(f"[BULK FATAL ERROR] {e}")
        return jsonify({"ok": False, "error": f"Unexpected error: {e}"}), 500


@app.route("/bulk_download/<sku>", methods=["GET"])
def bulk_download(sku):
    sku = (sku or "").strip().upper()
    bulk_pdf_path = os.path.join(STORE_DIR, f"bulk_{sku}.pdf")
    if os.path.exists(bulk_pdf_path):
        return send_file(bulk_pdf_path, as_attachment=True)
    return jsonify({"ok": False, "error": "Bulk file not found"}), 404

@app.route("/clear_all", methods=["POST"])
def clear_all():
    """
    Deletes all generated PDFs inside /out except orders.json
    and clears frontend view.
    """
    try:
        keep_files = {"orders.json"}  # you can also add "uploaded.pdf" here if needed

        for fname in os.listdir(STORE_DIR):
            if fname.lower().endswith(".pdf") and fname not in keep_files:
                os.remove(os.path.join(STORE_DIR, fname))

        # Optionally also clear rows from DB:
        # Uncomment if you want full reset:
        # _save_db({"orders": []})

        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/download/<order_id>", methods=["GET"])
def download(order_id):
    db = _load_db()
    for o in db["orders"]:
        if o["order_id"] == order_id and o.get("out_pdf") and os.path.exists(o["out_pdf"]):
            return send_file(o["out_pdf"], as_attachment=True)
    return jsonify({"ok": False, "error": "Not found or not ready yet"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
