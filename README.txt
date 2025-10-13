
Label + Invoice 3‑Page Slicer (Flask)

Flow implemented
----------------
1) Upload combined PDF where each page holds {label + invoice}.
2) App parses per page and creates an "order" with table columns: invoice_number, customer_name, sku, qty, product_id(s).
3) Scan codes like "AT0001-c0001"; first 6 are SKU, last 5 are product_id. Scans populate the first matching row that still needs IDs.
4) When all SKUs for an order have the correct number of product_ids (== qty), we slice and build a new 3‑page PDF:
   - Page 1: Label — tight-cropped left/right, auto-zoomed to EXACT 70mm × 30mm.
   - Page 2: Rotated invoice (90°)
   - Page 3: Rotated invoice (90°) [duplicate]
5) The output is saved as out/<ORDER_ID>.pdf. You can click "Download PDF".
6) Hook up printing by calling your Windows PDF2Printer in app.py after /scan completes a full order.

Parsing assumptions
-------------------
- order_id: starts with "OD" and is 20 chars total (e.g., ODxxxxxxxxxxxxxxxxxx)
- date: after "Order Date:"; accepts dd/mm/yyyy or dd-mm-yyyy; normalized to dd/mm/yyyy
- name: between "Name:" and the first comma
- sku: "ATdddd" (prefers the context "Purifier | AT0001 | IMEI/SrNo"); falls back to any ATdddd match
- qty: if line-wise counts are present, we count SKU occurrences; also looks for "TOTAL QTY: <n>"

Cropping/zoom logic
-------------------
- We detect the label area by taking the top ~48% of the page and unioning all text blocks; then pad 4pt and scale into a page of 70mm × 30mm (198.425 × 85.039 pt).
- You can fine-tune:
  LABEL_BOTTOM_FRAC in processor.py  (e.g., 0.42–0.52)
  CROP_PAD in processor.py (e.g., 0–10)
- For invoices, we rotate the full original page. If your invoice strictly sits below the label, you can clip only the bottom region in processor.py.

Run locally
-----------
python -m pip install flask PyMuPDF PyPDF2
python app.py

Then open http://localhost:8000
