#!/usr/bin/env python3
"""Дневной конвейер SEO-агента: очередь -> статья -> проверка -> обложка -> выдача.

  python3 pipeline.py                 # до daily_limit статей за сегодня
  python3 pipeline.py --count 1       # одна статья
  python3 pipeline.py --mock          # без OpenAI, для проверки конвейера
  python3 pipeline.py --keyword "..." # конкретный запрос из очереди

Пригоден для cron:  0 7 * * *  cd /path/seo-agent-ru && python3 scripts/pipeline.py
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from article_check import check, load_allowed, parse_article  # noqa: E402
from common import ROOT, abspath, count_phrase, load_config, load_env, slugify, stems  # noqa: E402

QUEUE_FIELDS = ["keyword", "secondary", "lsi", "kind", "max_chars", "topic", "priority",
                "status", "slug", "date", "note"]


# ---------- состояние ----------
def read_queue(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in QUEUE_FIELDS:
            r.setdefault(k, "")
    return rows


def write_queue(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=QUEUE_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def log(event):
    os.makedirs(abspath("state"), exist_ok=True)
    event["ts"] = dt.datetime.now().isoformat(timespec="seconds")
    with open(abspath("state/log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def done_today(rows):
    today = dt.date.today().isoformat()
    return sum(1 for r in rows if r["date"] == today and r["status"] in ("published", "ready"))


# ---------- контекст ----------
def read_text(path):
    p = abspath(path)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def read_pages(path):
    p = abspath(path)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if (r.get("url") or "").strip()]


def pick_link_candidates(pages, keyword, secondary, cfg, limit=8):
    """Страницы, наиболее близкие по словам к теме статьи. CTA сюда не входит."""
    q = set(stems(" ".join([keyword] + secondary)))
    cta = (cfg.get("cta_url") or "").rstrip("/")
    scored = []
    for p in pages:
        if p["url"].rstrip("/") == cta:
            continue
        s = len(q & set(stems((p.get("title") or "") + " " + (p.get("keywords") or ""))))
        scored.append((s, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:limit]]


# ---------- промпты ----------
def system_prompt(cfg):
    rules = read_text("references/humanizer-rules.md")
    spec = read_text("references/article-spec.md")
    guide = read_text("references/writing-guide.md")
    return (
        "Ты редактор и автор SEO-статей для русскоязычного сайта. Пишешь под Яндекс: для людей, "
        "по делу, без воды и без следов нейросети. Отвечаешь только готовым файлом статьи "
        "в заданном формате, без пояснений и без обрамляющих кавычек.\n\n"
        "=== СПЕЦИФИКАЦИЯ СТАТЬИ ===\n" + spec + "\n\n"
        "=== ПРАВИЛА НАПИСАНИЯ ===\n" + guide + "\n\n"
        "=== ПРАВИЛА ПРОТИВ ИИ-СЛОПА ===\n" + rules + "\n"
    )


def user_prompt(row, cfg, facts, cands, past_titles, errors=None, previous=None):
    kw = row["keyword"]
    sec = [s.strip() for s in row["secondary"].split(",") if s.strip()]
    links = "\n".join(f"- {p['url']} | {p.get('title', '')}" for p in cands) or "(список пуст)"
    parts = [
        f"Основной ключ: {kw}",
        f"Дополнительные ключи: {', '.join(sec) or 'нет'}",
        f"LSI-слова (впиши по теме, минимум половину): {row.get('lsi') or 'нет'}",
        f"Тип текста: {'коммерческий, не длиннее ' + str(row.get('max_chars')) + ' знаков' if row.get('kind') == 'commercial' else 'информационный, раскрой тему полно, без воды'}",
        f"Эмодзи для Description (минимум один): {' '.join(cfg.get('description_emojis') or []) or 'не требуются'}",
        f"Тема и интент: {row.get('topic') or 'определи по ключу'}",
        f"Регион: {cfg.get('region') or 'не указан'}",
        f"Объём: не меньше {cfg['min_chars']} знаков с пробелами, цель около {cfg['target_chars']}.",
        f"Автор: {cfg.get('author') or 'не указан, строку автора не добавляй'}",
        f"Ссылка на услугу для финального CTA (обязательна в последнем абзаце): {cfg.get('cta_url')}",
        f"Контакт для CTA: {cfg.get('cta_contact') or 'не указан'}",
        "Страницы для внутренних ссылок (бери только отсюда, "
        f"{cfg['min_internal_links']}-{cfg['max_internal_links']} штук, у каждой свой анкор):\n" + links,
        "Уже опубликованные заголовки (не повторяй темы):\n" + ("\n".join(f"- {t}" for t in past_titles[-30:]) or "нет"),
        "Факты о компании (единственный источник цифр, цен, имён и сроков):\n" + (facts.strip() or "(факты не заданы: не называй цен, сроков и имён)"),
    ]
    out = "\n\n".join(parts)
    if errors:
        out += ("\n\nПредыдущий вариант не прошёл проверку. Исправь именно это и верни файл целиком:\n"
                + "\n".join(f"- {e}" for e in errors) + "\n\nПредыдущий вариант:\n" + (previous or ""))
    return out


def humanize_prompt(draft):
    return (
        "Отредактируй статью по правилам против ИИ-слопа из системного сообщения. Удаляй, не дописывай. "
        "Заодно сделай корректуру: орфография, пунктуация, тавтология, лишние вводные слова. "
        "Все числа, имена, названия, ссылки, таблицы, ключи в H1 и первом абзаце, служебные поля до строки --- "
        "и структуру заголовков сохрани без изменений. Не добавляй фактов. Верни файл целиком.\n\n" + draft
    )


# ---------- генераторы ----------
class OpenAIWriter:
    def __init__(self):
        import openai_client
        self.oc = openai_client

    def write(self, system, user):
        return self.oc.chat(system, user)

    def image(self, scene, out):
        return self.oc.image(scene, out)


# ---------- шаги ----------
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_preserved(before, after):
    return sorted(NUM_RE.findall(before)) == sorted(NUM_RE.findall(after))


def strip_fences(t):
    t = t.strip()
    t = re.sub(r"^```(?:markdown|md)?\s*\n", "", t)
    return re.sub(r"\n```\s*$", "", t).strip()


def produce(row, cfg, writer, facts, pages, past_titles):
    """Возвращает (raw_article, meta, body, result) или (None, ..., result) при провале."""
    sec = [s.strip() for s in row["secondary"].split(",") if s.strip()]
    lsi = [s.strip() for s in row["lsi"].split(",") if s.strip()]
    kind = (row["kind"] or "info").strip().lower()
    max_chars = int(row["max_chars"]) if str(row["max_chars"]).isdigit() else 0
    allowed = {p["url"] for p in pages} | ({cfg["cta_url"]} if cfg.get("cta_url") else set())
    cands = pick_link_candidates(pages, row["keyword"], sec, cfg)
    system = system_prompt(cfg)
    errors, previous, res = None, None, None
    for attempt in range(1, cfg["max_attempts"] + 1):
        raw = strip_fences(writer.write(system, user_prompt(row, cfg, facts, cands, past_titles, errors, previous)))
        meta, body = parse_article(raw)
        res = check(body, meta, row["keyword"], sec, cfg, allowed, lsi=lsi, kind=kind, max_chars=max_chars)
        log({"event": "attempt", "keyword": row["keyword"], "n": attempt, "ok": res["ok"], "errors": res["errors"]})
        if res["ok"]:
            if cfg.get("humanize_pass"):
                edited = strip_fences(writer.write(system, humanize_prompt(raw)))
                m2, b2 = parse_article(edited)
                r2 = check(b2, m2, row["keyword"], sec, cfg, allowed, lsi=lsi, kind=kind, max_chars=max_chars)
                if r2["ok"] and numbers_preserved(body, b2):
                    raw, meta, body, res = edited, m2, b2, r2
                else:
                    log({"event": "humanize_rejected", "keyword": row["keyword"],
                         "errors": r2["errors"], "numbers_ok": numbers_preserved(body, b2)})
            return raw, meta, body, res
        errors, previous = res["errors"], raw
    return None, {}, "", res


def deliver(row, cfg, raw, meta, writer, slug):
    """Сохраняет статью и обложку, вызывает publish_hook, если разрешено."""
    date = dt.date.today().isoformat()
    out_dir = abspath(cfg["out_dir"])
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, f"{date}-{slug}.md")
    img_path = os.path.join(out_dir, f"{date}-{slug}.png")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(raw)
    scene = meta.get("IMAGE_SCENE") or f"тематическая иллюстрация к статье «{row['keyword']}»"
    try:
        writer.image(scene, img_path)
    except Exception as e:  # обложка не должна ронять статью
        log({"event": "image_failed", "keyword": row["keyword"], "error": str(e)[:300]})
        img_path = ""
    return release(cfg, row["keyword"], md_path, img_path, slug, date, meta), md_path, img_path


def release(cfg, keyword, md_path, img_path, slug, date, meta):
    """Публикует статью через publish_hook. Нет хука: статус ready."""
    hook = cfg.get("publish_hook", "").strip()
    if not hook:
        return "ready"
    env = dict(os.environ, ARTICLE_MD=md_path, ARTICLE_IMAGE=img_path, ARTICLE_SLUG=slug,
               ARTICLE_TITLE=meta.get("META_TITLE", ""), ARTICLE_DESCRIPTION=meta.get("META_DESCRIPTION", ""),
               ARTICLE_IMAGE_ALT=meta.get("IMAGE_ALT", ""))
    r = subprocess.run(hook, shell=True, env=env, cwd=ROOT, capture_output=True, text=True, timeout=300)
    log({"event": "publish_hook", "keyword": keyword, "code": r.returncode, "stderr": r.stderr[-300:]})
    return "published" if r.returncode == 0 else "ready"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=None)
    ap.add_argument("--keyword", default=None)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()

    load_env()
    cfg = load_config(a.config)
    qpath = abspath(cfg["queue_path"])
    rows = read_queue(qpath)
    if not rows:
        print(f"Очередь пуста или не найдена: {qpath}", file=sys.stderr)
        return 1

    limit = cfg["daily_limit"] - done_today(rows)
    n = min(a.count if a.count is not None else limit, limit)
    if n <= 0:
        print(f"Дневной лимит ({cfg['daily_limit']}) уже выбран.")
        return 0

    if a.mock:
        from mock_writer import MockWriter
        writer = MockWriter()
    else:
        try:
            writer = OpenAIWriter()
            writer.oc._key()
        except Exception as e:
            print(f"Ошибка: {e}", file=sys.stderr)
            return 2

    facts = read_text(cfg["facts_path"])
    pages = read_pages(cfg["pages_path"])
    pending = [r for r in rows if r["status"] in ("new", "")]
    if a.keyword:
        pending = [r for r in pending if r["keyword"].strip().lower() == a.keyword.strip().lower()]
    pending.sort(key=lambda r: int(r["priority"]) if str(r["priority"]).isdigit() else 999)
    past = [r["keyword"] for r in rows if r["status"] in ("published", "ready")]

    made = 0
    for row in pending[:n]:
        try:
            raw, meta, body, res = produce(row, cfg, writer, facts, pages, past)
        except Exception as e:  # сеть или API: одна статья не должна ронять весь запуск
            raw, res = None, {"errors": [f"сбой генерации: {str(e)[:200]}"]}
        if raw is None:
            row["status"], row["note"] = "failed", "; ".join(res["errors"])[:300]
            log({"event": "failed", "keyword": row["keyword"], "errors": res["errors"]})
        else:
            slug = slugify(row["keyword"])
            status, md, img = deliver(row, cfg, raw, meta, writer, slug)
            row.update(status=status, slug=slug, date=dt.date.today().isoformat(),
                       note=("без обложки" if not img else ""))
            past.append(row["keyword"])
            made += 1
            log({"event": status, "keyword": row["keyword"], "file": md, "stats": res["stats"]})
            print(f"[{status}] {row['keyword']} -> {md}")
        write_queue(qpath, rows)

    print(f"Готово: {made} из {n}. Смотрите state/log.jsonl.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
