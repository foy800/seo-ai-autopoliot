#!/usr/bin/env python3
"""Импорт семантики в контент-план: КАЖДЫЙ запрос становится отдельной строкой.

Скрипт ничего не группирует, не объединяет, не отбрасывает и не выбирает за человека.
Заголовки к строкам подбирает pipeline.py (ко всем сразу). Предупреждения о каннибализации
и существующих страницах пишет advisor.py: они только предупреждают, решает человек.
Одобрение («да») не ставится никогда.

  python3 scripts/import_semantics.py семантика.xlsx            # столбец «Запрос» или первый столбец
  python3 scripts/import_semantics.py семантика.csv --col keyword
  python3 scripts/import_semantics.py семантика.xlsx --sheet "Лист1" --kind info

Частотность и другие числовые столбцы источника попадают в «Примечание» строки как справка.
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_config  # noqa: E402
from plan import DEFAULT_ROW, KIND_RU, Plan  # noqa: E402


def read_source(path, sheet=None):
    if path.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb[sheet] if sheet else wb.active
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.reader(f)]
    if not rows:
        return [], []
    return [str(x or "").strip() for x in rows[0]], rows[1:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--col", default=None, help="название столбца с запросами (по умолчанию «Запрос»/keyword/первый)")
    ap.add_argument("--kind", default="info", choices=["info", "commercial"])
    a = ap.parse_args()
    head, data = read_source(a.file, a.sheet)
    names = [h.lower() for h in head]
    col = 0
    wanted = [a.col.lower()] if a.col else ["запрос", "keyword", "ключ", "основной ключ"]
    for w in wanted:
        if w in names:
            col = names.index(w)
            break
    cfg = load_config()
    plan = Plan(cfg)
    have = {r["keyword"].strip().lower() for r in plan.rows}
    added = skipped = 0
    for r in data:
        kw = str(r[col] or "").strip() if col < len(r) else ""
        if not kw:
            continue
        if kw.lower() in have:
            skipped += 1
            continue
        extra = []
        for i, v in enumerate(r):
            if i != col and v not in (None, "") and isinstance(v, (int, float)):
                extra.append(f"{head[i] if i < len(head) else i}: {int(v) if float(v).is_integer() else v}")
        row = dict(DEFAULT_ROW)
        row.update(keyword=kw, kind=a.kind if a.kind in KIND_RU else "info", status="new", _row=None,
                   note=("Источник: " + os.path.basename(a.file) + ("; " + "; ".join(extra[:4]) if extra else "")))
        plan.rows.append(row)
        have.add(kw.lower())
        added += 1
    plan.save()
    print(f"Добавлено строк: {added}, уже были в плане: {skipped}. Каждый запрос в своей строке, ничего не объединялось и не отбрасывалось.")
    print("Дальше: python3 scripts/pipeline.py (подберёт заголовки ко всем строкам), python3 scripts/advisor.py (предупреждения).")


if __name__ == "__main__":
    main()
