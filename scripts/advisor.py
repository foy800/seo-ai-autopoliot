#!/usr/bin/env python3
"""Советник: находит каннибализацию и уже существующие страницы, но НИЧЕГО не решает за человека.

Что делает:
  - сравнивает ключи плана между собой (одни и те же слова, один интент: риск каннибализации);
  - сравнивает ключ со страницами из context/pages.csv и уже опубликованными статьями;
  - смотрит доп. ключи строки: дубли основного ключа, ключи, которые есть и в другой строке.

Что пишет: колонки «Предупреждение» и «Рекомендация» в плане и лист «Предупреждения» с вариантами
решения (A, B, C). Решение всегда за человеком.

Чего НЕ делает никогда: не меняет, не удаляет, не объединяет и не пропускает ключи и заголовки,
не меняет «Одобрено», не блокирует строки и не группирует ключи в статьи по своему усмотрению.

  python3 scripts/advisor.py            # проверить план, записать предупреждения
  python3 scripts/advisor.py --show     # только показать в консоли
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import abspath, load_config, stems  # noqa: E402
from plan import Plan  # noqa: E402

SAME = 1.0      # те же слова
CLOSE = 0.75    # очень близкие наборы слов
PAGE_MATCH = 0.8


def _set(text):
    return set(stems(text))


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def read_pages(cfg):
    p = abspath(cfg.get("pages_path") or "context/pages.csv")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if (r.get("url") or "").strip()]


def analyze(plan, pages):
    """Возвращает список предупреждений: словари row, kind, found, recommendation, options."""
    out = []
    rows = plan.rows
    sets = [_set(r["keyword"]) for r in rows]
    # 1. каннибализация между ключами плана
    for i, a in enumerate(rows):
        for j in range(i + 1, len(rows)):
            b = rows[j]
            sim = _jaccard(sets[i], sets[j])
            if sim < CLOSE:
                continue
            same = sim >= SAME
            found = (f"Ключи «{a['keyword']}» (строка {i + 1}) и «{b['keyword']}» (строка {j + 1}) "
                     + ("состоят из одних и тех же слов" if same else f"очень близки по словам (сходство {int(sim * 100)}%)")
                     + ": скорее всего, один поисковый интент, статьи будут конкурировать в выдаче.")
            rec = (f"Рекомендуется оставить одну статью на оба ключа: основным взять более частотный, "
                   f"а второй убрать из плана или перенести в доп. ключи первой строки.")
            options = ["A. Оставить одну статью, второй ключ перенести в доп. ключи (рекомендуется).",
                       "B. Оставить обе статьи, но развести интент: у второй добавить уточнение в заголовок и ключ.",
                       "C. Ничего не менять, если вы уверены, что интенты разные."]
            for r, other in ((a, j), (b, i)):
                out.append(dict(row=r, kind="каннибализация", found=found, rec=rec, options=options))
    # 2. уже существующие страницы сайта
    for i, r in enumerate(rows):
        for p in pages:
            ptxt = " ".join(str(p.get(k) or "") for k in ("title", "keywords"))
            pset = _set(ptxt)
            if not pset:
                continue
            inter = len(sets[i] & pset)
            cover = inter / len(sets[i]) if sets[i] else 0.0
            if cover >= PAGE_MATCH and len(sets[i]) >= 2 and r["status"] not in ("published", "ready"):
                found = (f"Тему ключа «{r['keyword']}» уже закрывает страница {p['url']} "
                         f"(«{p.get('title', '')}», совпадение слов {int(cover * 100)}%).")
                rec = ("Рекомендуется сначала решить, чем статья будет отличаться от страницы: "
                       "информационный разбор вместо коммерческого описания или другой интент. "
                       "Если отличия нет, статью лучше не писать, а усилить саму страницу.")
                options = ["A. Писать информационную статью и сослаться на эту страницу в финале (рекомендуется, если интент другой).",
                           "B. Не писать статью, усилить существующую страницу этим ключом.",
                           "C. Ничего не менять и писать как есть."]
                out.append(dict(row=r, kind="страница уже есть", found=found, rec=rec, options=options))
                break
    # 3. доп. ключи
    owners = {}
    for i, r in enumerate(rows):
        owners.setdefault(frozenset(sets[i]), []).append(i)
    for i, r in enumerate(rows):
        for s in [x.strip() for x in r["secondary"].split(",") if x.strip()]:
            ss = _set(s)
            if _jaccard(ss, sets[i]) >= SAME:
                out.append(dict(row=r, kind="доп. ключ", found=f"Доп. ключ «{s}» повторяет основной «{r['keyword']}».",
                                rec=f"Рекомендуется убрать «{s}» из доп. ключей: он не добавляет охвата и повышает риск переспама.",
                                options=["A. Убрать доп. ключ (рекомендуется).", "B. Оставить как есть."]))
            elif frozenset(ss) in owners and any(k != i for k in owners[frozenset(ss)]):
                k = [k for k in owners[frozenset(ss)] if k != i][0]
                out.append(dict(row=r, kind="доп. ключ", found=f"Доп. ключ «{s}» стоит основным ключом в строке {k + 1}.",
                                rec=f"Рекомендуется убрать «{s}» из доп. ключей этой строки или объединить строки {i + 1} и {k + 1}.",
                                options=["A. Убрать из доп. ключей (рекомендуется).", "B. Объединить две статьи в одну.", "C. Ничего не менять."]))
    return out


def write(plan, warns):
    """Пишет только колонки предупреждений и лист «Предупреждения». Остальное не трогает."""
    for r in plan.rows:
        r["warning"], r["recommendation"] = "", ""
    by_row = {}
    for w in warns:
        by_row.setdefault(id(w["row"]), []).append(w)
    for r in plan.rows:
        ws_ = by_row.get(id(r), [])
        if ws_:
            r["warning"] = " | ".join(f"[{w['kind']}] {w['found']}" for w in ws_)[:900]
            r["recommendation"] = " | ".join(w["rec"] for w in ws_)[:900]
    plan.save()
    wb = plan.wb
    name = "Предупреждения"
    if name in wb.sheetnames:
        del wb[name]
    sh = wb.create_sheet(name)
    sh.append(["№ строки", "Основной ключ", "Тип", "Что найдено", "Рекомендация", "Варианты решения (выбирает человек)"])
    idx = {id(r): i + 1 for i, r in enumerate(plan.rows)}
    for w in warns:
        sh.append([idx[id(w["row"])], w["row"]["keyword"], w["kind"], w["found"], w["rec"], "\n".join(w["options"])])
    for col, width in zip("ABCDEF", (10, 34, 18, 70, 60, 70)):
        sh.column_dimensions[col].width = width
    from openpyxl.styles import Alignment, Font
    for c in sh[1]:
        c.font = Font(bold=True)
    for row in sh.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    sh.freeze_panes = "A2"
    try:
        wb.save(plan.path)
    except PermissionError:
        alt = plan.path[:-5] + " (новый).xlsx"
        wb.save(alt)
        print(f"Файл открыт в Excel: предупреждения записаны в «{os.path.basename(alt)}».")


def main():
    cfg = load_config()
    plan = Plan(cfg)
    warns = analyze(plan, read_pages(cfg))
    show = "--show" in sys.argv
    if not show:
        write(plan, warns)
    seen = set()
    for w in warns:
        key = (w["kind"], w["found"])
        if key in seen:
            continue
        seen.add(key)
        print(f"[{w['kind']}] {w['found']}\n   Рекомендация: {w['rec']}")
    print(f"Предупреждений: {len(seen)}. Решение по каждому принимает человек; ключи, заголовки и статусы не менялись.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
