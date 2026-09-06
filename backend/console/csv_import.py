"""Pure CSV → catalogue-row parser for bulk SKU import.

Merchant-friendly: prices are given in RUPEES (what a seller types), converted to
integer paise here. Validation is per-row — one bad line is reported with its line
number and never aborts the whole import. No DB, no LLM: fully unit-testable.

Expected header (case-insensitive, order-independent):
    sku, title, category, list_price, cost, stock, return_days, shipping_days,
    effective_price (optional), promo_code (optional), description (optional)
"""
from __future__ import annotations

import csv
import io

REQUIRED = ("sku", "title", "category", "list_price", "cost", "return_days", "shipping_days")

TEMPLATE = (
    "sku,title,category,list_price,cost,stock,return_days,shipping_days,effective_price,promo_code,description\n"
    "SKU-001,Cloud Strider,running_shoes,4299,3000,25,20,2,,,Lightweight daily trainer\n"
    "SKU-002,Sunday Long-Run,running_shoes,5499,3700,15,20,2,4799,SUMMER,On promo\n"
)


def _to_paise(val: str, field: str) -> int:
    v = float(str(val).replace(",", "").replace("\u20b9", "").strip())
    if v < 0:
        raise ValueError(f"{field} must be >= 0")
    return int(round(v * 100))


def _to_int(val: str, field: str, *, minimum: int = 0) -> int:
    n = int(float(str(val).strip()))
    if n < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return n


def parse_catalog_csv(text: str) -> tuple[list[dict], list[dict]]:
    """Return (valid_rows, errors).

    valid_rows: dicts ready to upsert (paise + attributes + description).
    errors: [{"line": n, "sku": s, "error": msg}] — line numbers are 1-based with
    the header as line 1, so the first data row is line 2 (matches a spreadsheet).
    """
    rows: list[dict] = []
    errors: list[dict] = []

    reader = csv.DictReader(io.StringIO(text or ""))
    if not reader.fieldnames:
        return [], [{"line": 0, "sku": "", "error": "empty file or no header row"}]

    norm = {(h or "").strip().lower(): h for h in reader.fieldnames}
    missing = [c for c in REQUIRED if c not in norm]
    if missing:
        return [], [{"line": 1, "sku": "", "error": f"missing required columns: {', '.join(missing)}"}]

    for i, raw in enumerate(reader, start=2):
        def g(col: str) -> str:
            return (raw.get(norm.get(col, ""), "") or "").strip()

        sku = g("sku")
        try:
            if not sku:
                raise ValueError("sku is required")
            category = g("category")
            if not category:
                raise ValueError("category is required")
            list_price = _to_paise(g("list_price"), "list_price")
            if list_price <= 0:
                raise ValueError("list_price must be > 0")
            cost = _to_paise(g("cost"), "cost")
            stock = _to_int(g("stock"), "stock") if g("stock") else 10
            return_days = _to_int(g("return_days"), "return_days")
            shipping_days = _to_int(g("shipping_days"), "shipping_days")

            attrs: dict = {}
            if g("effective_price"):
                attrs["effective_price_paise"] = _to_paise(g("effective_price"), "effective_price")
            if g("promo_code"):
                attrs["promo_code"] = g("promo_code")

            rows.append(dict(
                sku=sku, title=g("title") or sku, category=category,
                list_price_paise=list_price, cost_paise=cost, stock=stock,
                return_days=return_days, shipping_days=shipping_days,
                attributes=attrs, description=g("description"),
            ))
        except (ValueError, TypeError) as exc:
            errors.append({"line": i, "sku": sku, "error": str(exc)})

    return rows, errors
