# -*- coding: utf-8 -*-
"""One-off extractor: sample the real SKU master xlsx into a compact JSON catalog.

Usage (run from web/backend, with -I because the xlsx is untrusted input):
    python -I scripts/extract_sku_catalog.py

Requires openpyxl (dev-only, NOT added to requirements.txt):
    pip install openpyxl

Input : C:/Users/24540/Desktop/WMS/data/tb_basic_article（Sku数据）.xlsx
Output: C:/Users/24540/Desktop/WMS/web/data/sku_catalog.json
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import openpyxl

BACKEND_DIR = Path(__file__).resolve().parents[1]  # web/backend
PROJECT_ROOT = BACKEND_DIR.parent.parent            # WMS
XLSX_PATH = PROJECT_ROOT / "data" / "tb_basic_article（Sku数据）.xlsx"
OUT_PATH = BACKEND_DIR.parent / "data" / "sku_catalog.json"  # web/data

SAMPLE_SEED = 120
TARGET_TOTAL = 120

# 1-based column index -> output field name, verified against the 字段设计 sheet
# (the data sheet has no header row; the first row is already data).
COLUMNS = {
    1: "skuId",              # article_id
    2: "name",               # article_name
    13: "brand",             # 品牌
    14: "thousandWeight",    # 千支重
    15: "unit",              # 单位
    16: "colorType",         # property 颜色类型 BLACK_TYPE/COLORED_TYPE
    17: "standard",          # 国标
    18: "spec",              # 规格
    19: "grade",             # 等级
    20: "surfaceTreatment",  # 表面处理
    21: "material",          # 材质
    23: "strength",          # 强度等级
    24: "largeCategory",     # 大类
    25: "subclass",          # 小类
}


def _clean(value):
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return int(value)
    return value


def extract() -> dict:
    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True, data_only=True)
    ws = wb.worksheets[0]  # tb_basic_article（Sku数据）

    grouped: dict[str, list[dict]] = {}
    for row in ws.iter_rows(values_only=True):
        if not row or row[0] is None:
            continue
        sku = {}
        for idx, field in COLUMNS.items():
            sku[field] = _clean(row[idx - 1] if len(row) >= idx else None)
        if not sku.get("skuId") or not sku.get("largeCategory"):
            continue
        grouped.setdefault(str(sku["largeCategory"]), []).append(sku)
    wb.close()

    per_category = max(1, round(TARGET_TOTAL / max(1, len(grouped))))
    rng = random.Random(SAMPLE_SEED)
    sampled: list[dict] = []
    for category in sorted(grouped.keys()):
        items = sorted(grouped[category], key=lambda s: str(s["skuId"]))
        rng.shuffle(items)
        sampled.extend(items[:per_category])

    # stable output order for reproducibility
    sampled.sort(key=lambda s: (str(s["largeCategory"]), str(s["skuId"])))

    return {
        "version": 1,
        "source": XLSX_PATH.name,
        "count": len(sampled),
        "skus": sampled,
    }


def main() -> None:
    catalog = extract()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    cats = sorted({s["largeCategory"] for s in catalog["skus"]})
    print(f"WROTE {OUT_PATH}  ({catalog['count']} SKUs, {len(cats)} 大类)")


if __name__ == "__main__":
    main()
