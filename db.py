# db.py
# -----------------------------------------------
# CSV "DB" layer + master-data driven behavior.
#
# Files:
#   data/sku_master.csv    -> sku,display_name,type (Compulsory|Loose)
#   data/extras_noscan.csv -> sku
#   data/print_rules.csv   -> sku,labels,invoices (DEFAULT row allowed)
#
# Bulk-print:
#   - Selected SKU is treated as Loose, regardless of master type
#   - Tokens assigned as: BULK-{sku}-{0001..}
# -----------------------------------------------

import csv
import os
from dataclasses import dataclass

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SKU_MASTER_CSV = os.path.join(DATA_DIR, "sku_master.csv")
EXTRAS_NOSCAN_CSV = os.path.join(DATA_DIR, "extras_noscan.csv")
PRINT_RULES_CSV = os.path.join(DATA_DIR, "print_rules.csv")


@dataclass
class SKUInfo:
    sku: str
    display_name: str
    type: str              # "Compulsory" or "Loose"
    noscan: bool = False   # in extras_noscan.csv
    labels: int = 1
    invoices: int = 2


_master_loaded = False
_sku_info: dict[str, SKUInfo] = {}
_default_labels = 1
_default_invoices = 2
_extras_noscan: set[str] = set()


def _load_sku_master():
    global _sku_info
    if not os.path.exists(SKU_MASTER_CSV):
        return

    with open(SKU_MASTER_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = (row.get("sku") or "").strip().upper()
            if not sku:
                continue
            display_name = (row.get("display_name") or sku).strip()
            type_ = (row.get("type") or "Loose").strip().capitalize()
            if type_ not in ("Compulsory", "Loose"):
                type_ = "Loose"

            if sku in _sku_info:
                info = _sku_info[sku]
                info.display_name = display_name or info.display_name
                info.type = type_
            else:
                _sku_info[sku] = SKUInfo(
                    sku=sku,
                    display_name=display_name or sku,
                    type=type_
                )


def _load_extras_noscan():
    global _extras_noscan, _sku_info
    if not os.path.exists(EXTRAS_NOSCAN_CSV):
        return

    with open(EXTRAS_NOSCAN_CSV, newline="", encoding="utf-8") as f:
        for line in f:
            sku = line.strip().upper()
            if not sku or sku.startswith("#"):
                continue
            _extras_noscan.add(sku)
            if sku in _sku_info:
                _sku_info[sku].noscan = True
            else:
                _sku_info[sku] = SKUInfo(
                    sku=sku,
                    display_name=sku,
                    type="Loose",
                    noscan=True
                )


def _load_print_rules():
    global _default_labels, _default_invoices, _sku_info
    if not os.path.exists(PRINT_RULES_CSV):
        return

    with open(PRINT_RULES_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = (row.get("sku") or "").strip().upper()
            if not sku:
                continue

            labels = int(row.get("labels") or 1)
            invoices = int(row.get("invoices") or 2)

            if sku == "DEFAULT":
                _default_labels = labels
                _default_invoices = invoices
                continue

            if sku in _sku_info:
                info = _sku_info[sku]
                info.labels = labels
                info.invoices = invoices
            else:
                _sku_info[sku] = SKUInfo(
                    sku=sku,
                    display_name=sku,
                    type="Loose",
                    labels=labels,
                    invoices=invoices
                )


def load_master_data():
    """Idempotent loader — safe to call many times."""
    global _master_loaded
    if _master_loaded:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    _load_sku_master()
    _load_extras_noscan()
    _load_print_rules()
    _master_loaded = True


def get_sku_info(sku: str) -> SKUInfo:
    """Return SKUInfo; if not present, synthesize a Loose/default one."""
    load_master_data()
    sku = (sku or "").strip().upper()
    if sku in _sku_info:
        info = _sku_info[sku]
        # Fill missing labels/invoices from defaults
        if info.labels <= 0:
            info.labels = _default_labels
        if info.invoices <= 0:
            info.invoices = _default_invoices
        return info

    info = SKUInfo(
        sku=sku,
        display_name=sku,
        type="Loose",
        noscan=(sku in _extras_noscan),
        labels=_default_labels,
        invoices=_default_invoices,
    )
    _sku_info[sku] = info
    return info


def is_noscan_sku(sku: str) -> bool:
    load_master_data()
    return sku.strip().upper() in _extras_noscan


def get_print_counts_for_sku(sku: str) -> tuple[int, int]:
    info = get_sku_info(sku)
    return info.labels or _default_labels, info.invoices or _default_invoices
