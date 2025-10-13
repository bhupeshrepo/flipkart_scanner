# config.py — project 2
from dataclasses import dataclass

@dataclass
class SplitConfig:
    # --- Output page dimensions (POINTS, not mm) ---
    # Set these to the exact numbers you want. Example: 30mm x 70mm ≈ 85.039 x 198.425 pt
    page_width_pt: float = 85.039
    page_height_pt: float = 120.425

    # fractional split of page height where 0.0 is top, 1.0 is bottom
    split_frac: float = 0.465  # .461 for single & .495 for multiple
    
    label_zoom: float = 1.83  # 1.0 = normal, >1 = zoom in, <1 = zoom out

    # LABEL padding (points)
    pad_label_top: float = 0
    pad_label_bottom: float = 0
    pad_label_left: float = 0
    pad_label_right: float = 0
    # LABEL padding (points)
    # pad_label_top: float = 6
    # pad_label_bottom: float = 6
    # pad_label_left: float = 120
    # pad_label_right: float = 140

    # NEW: extra trim from the label top (in points)
    trim_label_top_pt: float = 0  # ↑ increase to cut more from the top
    
    # INVOICE padding (points)
    pad_invoice_top: float = 8
    pad_invoice_bottom: float = 10
    pad_invoice_left: float = 20
    pad_invoice_right: float = 20
    # INVOICE padding (points)
    # pad_invoice_top: float = 0
    # pad_invoice_bottom: float = 40
    # pad_invoice_left: float = 0
    # pad_invoice_right: float = 0

    # Rotation for invoice pages
    rotate_invoice_deg: int = 90

# Single source of truth for Project 2
DEFAULT = SplitConfig()
