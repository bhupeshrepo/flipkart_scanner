# processor.py — project 2 (config-driven)

import io
import fitz  # PyMuPDF
import PyPDF2
from config import DEFAULT as CFG  # ← uses the new config with page_width_pt / page_height_pt

# ---------------------------------------------------------
# Fixed output page size (ALL pages will use this size)
# ---------------------------------------------------------
PAGE_W_PT = CFG.page_width_pt
PAGE_H_PT = CFG.page_height_pt

class PDFOrderProcessor:
    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.doc = fitz.open(pdf_path)

    def doc_page_count(self) -> int:
        return self.doc.page_count

    def page_text(self, page_index: int) -> str:
        return self.doc.load_page(page_index).get_text('text')

def _compute_label_invoice_rects(page_rect: fitz.Rect, cfg=CFG) -> tuple[fitz.Rect, fitz.Rect]:
    y_split = page_rect.y0 + page_rect.height * cfg.split_frac

    label = fitz.Rect(
        page_rect.x0 + cfg.pad_label_left,
        page_rect.y0 + cfg.pad_label_top,
        page_rect.x1 - cfg.pad_label_right,
        y_split - cfg.pad_label_bottom,
    )

    # 🔧 Trim extra white space from label top
    label.y0 += cfg.trim_label_top_pt
    
    invoice = fitz.Rect(
        page_rect.x0 + cfg.pad_invoice_left,
        y_split + cfg.pad_invoice_top,
        page_rect.x1 - cfg.pad_invoice_right,
        page_rect.y1 - cfg.pad_invoice_bottom,
    )

    # Clamp just in case
    label = label & page_rect
    invoice = invoice & page_rect
    return label, invoice

def _render_clip_to_fixed_page(src_doc: fitz.Document, page_index: int, clip_rect: fitz.Rect) -> fitz.Document:
    clip_w, clip_h = clip_rect.width, clip_rect.height
    scale = min(PAGE_W_PT / clip_w, PAGE_H_PT / clip_h)
    
    # 🟩 Apply label zoom from config
    scale *= CFG.label_zoom
    
    dest_w = clip_w * scale
    dest_h = clip_h * scale
    dx = (PAGE_W_PT - dest_w) / 2.0
    dy = (PAGE_H_PT - dest_h) / 2.0

    out = fitz.open()
    p = out.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
    dest = fitz.Rect(dx, dy, dx + dest_w, dy + dest_h)
    p.show_pdf_page(dest, src_doc, page_index, clip=clip_rect, rotate=0, keep_proportion=True, overlay=True, oc=0)
    return out

def _render_rotated_clip_to_fixed_page(src_doc: fitz.Document, page_index: int, clip_rect: fitz.Rect, rotation_deg: int) -> fitz.Document:
    clip_w, clip_h = clip_rect.width, clip_rect.height
    if rotation_deg % 180 == 90:
        content_w, content_h = clip_h, clip_w
    else:
        content_w, content_h = clip_w, clip_h

    scale = min(PAGE_W_PT / content_w, PAGE_H_PT / content_h)
    dest_w = content_w * scale
    dest_h = content_h * scale
    dx = (PAGE_W_PT - dest_w) / 2.0
    dy = (PAGE_H_PT - dest_h) / 2.0

    out = fitz.open()
    p = out.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
    dest = fitz.Rect(dx, dy, dx + dest_w, dy + dest_h)
    p.show_pdf_page(dest, src_doc, page_index, clip=clip_rect, rotate=rotation_deg, keep_proportion=True, overlay=True, oc=0)
    return out

def slice_and_build_order_pdf(source_pdf: str, page_index: int, out_pdf: str):
    src = fitz.open(source_pdf)
    page = src.load_page(page_index)
    page_rect = page.rect

    label_clip, invoice_clip = _compute_label_invoice_rects(page_rect, cfg=CFG)

    # Page 1: label (fixed size from config)
    label_doc = _render_clip_to_fixed_page(src, page_index, label_clip)

    # Pages 2 & 3: invoice (fixed size + rotation from config)
    inv_doc = _render_rotated_clip_to_fixed_page(src, page_index, invoice_clip, rotation_deg=CFG.rotate_invoice_deg)

    writer = PyPDF2.PdfWriter()
    label_reader = PyPDF2.PdfReader(io.BytesIO(label_doc.tobytes()))
    writer.add_page(label_reader.pages[0])

    inv_reader = PyPDF2.PdfReader(io.BytesIO(inv_doc.tobytes()))
    writer.add_page(inv_reader.pages[0])  # invoice page
    writer.add_page(inv_reader.pages[0])  # duplicate invoice page

    with open(out_pdf, 'wb') as f:
        writer.write(f)
