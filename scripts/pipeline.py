#!/usr/bin/env python3
"""Дневной конвейер SEO-агента: контент-план Excel -> одобрение -> статья -> проверка -> обложка -> публикация.

  python3 pipeline.py                 # до daily_limit одобренных статей за сегодня
  python3 pipeline.py --count 1       # одна статья
  python3 pipeline.py --mock          # без OpenAI, для проверки конвейера
  python3 pipeline.py --keyword "..." # конкретный запрос из плана

Контент-план: content-plan.xlsx в папке проекта (создаётся автоматически). Агент подбирает заголовки,
вы ставите «да» в колонке «Одобрено», и только тогда статья пишется и публикуется.
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
from plan import Plan, is_approved  # noqa: E402
import sync_plan  # noqa: E402
from gate import ApprovalError, require_approved  # noqa: E402
from advisor import analyze as advise, read_pages as advisor_pages, write as advisor_write  # noqa: E402


# ---------- состояние ----------
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


def pick_cta(pages, keyword, secondary):
    """Страница услуги для CTA: среди страниц с cta=yes (или всех) ближайшая по словам к теме."""
    flagged = [p for p in pages if str(p.get("cta", "")).strip().lower() in ("1", "yes", "true", "да", "y")]
    pool = flagged or pages
    q = set(stems(" ".join([keyword] + secondary)))

    def score(p):
        text = " ".join(str(p.get(k) or "") for k in ("title", "keywords", "description"))
        return len(q & set(stems(text)))

    return max(pool, key=score, default=None)


# ---------- заголовки ----------
def fallback_title(row):
    kw = row["keyword"].strip()
    base = kw[:1].upper() + kw[1:]
    topic = (row.get("topic") or "").strip()
    return f"{base}: {topic}" if topic else f"{base}: что важно знать"


def propose_title(writer, row):
    """Заголовок H1 для плана: через модель, с проверкой; при сбое запасной вариант."""
    fb = fallback_title(row)
    if writer is None:
        return fb
    prompt = (
        f"Придумай заголовок H1 для SEO-статьи.\nКлюч: {row['keyword']}\n"
        f"Тема: {row.get('topic') or 'по ключу'}\n"
        "Требования: ключ в исходной форме, 40-80 знаков, информативно, без кликбейта, без вводных слов, "
        "без тире и эмодзи. Верни одну строку с заголовком."
    )
    try:
        t = writer.write("Ты редактор SEO-статей. Отвечаешь только заголовком.", prompt)
        t = t.strip().splitlines()[0].strip().strip("«»\"'").strip()
    except Exception:
        return fb
    ok = 15 <= len(t) <= 90 and count_phrase(stems(t), row["keyword"]) > 0 and "—" not in t and " - " not in t
    return t if ok else fb


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
        f"Заголовок H1 (одобрен, используй дословно): {row['title']}",
        "Ключи и заголовок заданы человеком: не заменяй, не добавляй и не убирай ключи самостоятельно. Если ключ не вписывается по смыслу, не втискивай его и не придумывай замену.",
        f"Основной ключ: {kw}",
        f"Дополнительные ключи: {', '.join(sec) or 'нет'}",
        f"LSI-слова (впиши по теме, минимум половину): {row.get('lsi') or 'нет'}",
        f"Тип текста: {'коммерческий, не длиннее ' + str(row.get('max_chars')) + ' знаков' if row.get('kind') == 'commercial' else 'информационный, раскрой тему полно, без воды'}",
        f"Эмодзи для Description (минимум один): {' '.join(cfg.get('description_emojis') or []) or 'не требуются'}",
        f"Тема и интент: {row.get('topic') or 'определи по ключу'}",
        f"Регион: {cfg.get('region') or 'не указан'}",
        f"Объём: не меньше {cfg['min_chars']} знаков с пробелами, цель около {cfg['target_chars']}.",
        f"Страница услуги для финального CTA: {cfg.get('cta_url')} | {cfg.get('cta_title')} | "
        f"{cfg.get('cta_description') or 'описание не задано, опиши услугу только по названию'}",
        "Финальный абзац (CTA): 2-3 предложения. Опиши услугу «" + str(cfg.get("cta_title")) + "» по описанию выше, "
        "без выдуманных фактов, призови к действию и поставь ссылку на эту страницу. "
        "Телефон, адрес, имена и регалии не указывай.",
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
        "Все числа, имена, названия, ссылки, таблицы, заголовок H1, ключи в первом абзаце, служебные поля до строки --- "
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


def make_writer(mock):
    """Возвращает (writer, ошибка). Без ключа writer=None: заголовки берутся запасные, статьи не пишутся."""
    if mock:
        from mock_writer import MockWriter
        return MockWriter(), None
    try:
        w = OpenAIWriter()
        w.oc._key()
        return w, None
    except Exception as e:
        return None, str(e)


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
    cta = pick_cta(pages, row["keyword"], sec)
    if not cta:
        raise RuntimeError("нет страниц в context/pages.csv: не из чего выбрать услугу для CTA")
    cfg = dict(cfg, cta_url=cta["url"], cta_title=cta.get("title", ""), cta_description=cta.get("description", ""),
               h1_exact=row["title"])
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


def failed_counts():
    """Сколько раз каждый ключ не прошёл проверку (по state/log.jsonl)."""
    out = {}
    p = abspath("state/log.jsonl")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("event") == "failed":
                out[d.get("keyword", "")] = out.get(d.get("keyword", ""), 0) + 1
    return out


def advise_failure(errors):
    """Превращает ошибки проверки в рекомендацию человеку. Ключи сам не меняет и не убирает."""
    recs = []
    for e in errors:
        if "дополнительных ключей отсутствует" in e or "нет доп. ключей" in e:
            recs.append("Рекомендуется убрать из доп. ключей те, что не вписываются по смыслу: " + e.split(":", 1)[-1].strip())
        elif "максимум" in e and "встречается" in e:
            recs.append("Ключ повторяется чаще допустимого: рекомендуется заменить длинный ключ более короткой формой или убрать его из доп. ключей. " + e)
        elif "LSI" in e:
            recs.append("Рекомендуется сократить список LSI-слов до тех, что подходят теме: " + e)
        else:
            recs.append(e)
    return " | ".join(recs)[:800]


def deliver(row, cfg, raw, meta, writer, slug):
    """Сохраняет статью и обложку и публикует. Возвращает (статус, url, путь к .md, есть ли обложка)."""
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
    status, url = release(cfg, row["keyword"], md_path, img_path, slug, date, meta)
    return status, url, md_path, bool(img_path)


def release(cfg, keyword, md_path, img_path, slug, date, meta):
    """Публикует статью: на сайт без CMS (publish_mode local/ftp) или через publish_hook.
    Возвращает (статус, url). Не вышло или способ не задан: «ready», публикация повторится при следующем запуске.
    Перед публикацией всегда проверяется «да» в колонке «Одобрено»: без него публикация невозможна."""
    require_approved(cfg, keyword=keyword)
    mode = (cfg.get("publish_mode") or "").strip().lower()
    if mode in ("local", "ftp"):
        from publish_static import publish
        try:
            url = publish(cfg, md_path, img_path, slug, date, meta)
        except Exception as e:
            log({"event": "publish_failed", "keyword": keyword, "mode": mode, "error": str(e)[:300]})
            return "ready", ""
        log({"event": "site_published", "keyword": keyword, "mode": mode, "url": url})
        return "published", url
    hook = cfg.get("publish_hook", "").strip()
    if not hook:
        return "ready", ""
    env = dict(os.environ, ARTICLE_MD=md_path, ARTICLE_IMAGE=img_path, ARTICLE_SLUG=slug,
               ARTICLE_TITLE=meta.get("META_TITLE", ""), ARTICLE_DESCRIPTION=meta.get("META_DESCRIPTION", ""),
               ARTICLE_IMAGE_ALT=meta.get("IMAGE_ALT", ""))
    r = subprocess.run(hook, shell=True, env=env, cwd=ROOT, capture_output=True, text=True, timeout=300)
    log({"event": "publish_hook", "keyword": keyword, "code": r.returncode, "stderr": r.stderr[-300:]})
    return ("published" if r.returncode == 0 else "ready"), ""


def mark_published(row, status, url):
    row["status"] = status
    if status == "published":
        row["published_at"] = dt.date.today().isoformat()
        row["url"] = url or row.get("url", "")


def retry_unpublished(cfg, plan):
    """Повторяет публикацию статей со статусом ready (например, после сбоя FTP)."""
    if (cfg.get("publish_mode") or "").strip().lower() not in ("local", "ftp") and not cfg.get("publish_hook"):
        return
    for row in plan.rows:
        if row["status"] != "ready" or not row["slug"]:
            continue
        date = row["date"] or dt.date.today().isoformat()
        md_path = os.path.join(abspath(cfg["out_dir"]), f"{date}-{row['slug']}.md")
        if not os.path.exists(md_path):
            continue
        meta, _ = parse_article(open(md_path, encoding="utf-8").read())
        status, url = release(cfg, row["keyword"], md_path, md_path[:-3] + ".png", row["slug"], date, meta)
        mark_published(row, status, url)
        plan.save()
        print(f"[повтор публикации: {status}] {row['keyword']}")


LAST = {"created": 0, "published": 0, "failed": 0, "articles": [], "errors": []}


def write_last_run(cfg_ok=True, pipeline_failed=False):
    """Результат запуска в JSON для внешних потребителей (Telegram-бот): state/last_run.json."""
    try:
        cfg = load_config()
        data = dict(LAST, project=(cfg.get("site_url") or "").replace("https://", "").replace("http://", "").strip("/"),
                    date=dt.date.today().isoformat(), pipeline_failed=pipeline_failed,
                    finished_at=dt.datetime.now().isoformat(timespec="seconds"))
        path = abspath("state/last_run.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def main():
    LAST.update(created=0, published=0, failed=0, articles=[], errors=[])
    try:
        code = _main()
    except SystemExit:
        write_last_run(pipeline_failed=True)
        raise
    except Exception:
        write_last_run(pipeline_failed=True)
        raise
    write_last_run(pipeline_failed=code not in (0, 1))
    return code


def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=None)
    ap.add_argument("--keyword", default=None)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()

    load_env()
    cfg = load_config(a.config)
    sync_plan.pull(cfg)  # подтянуть ваши правки (в т.ч. «да») до любой работы
    try:
        plan = Plan(cfg)
    except RuntimeError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 2
    added = plan.sync_csv(abspath(cfg["queue_path"]))
    if not plan.rows:
        plan.save()
        print(f"Контент-план пуст. Добавьте запросы в {os.path.basename(plan.path)} (колонка «Основной ключ»).")
        return 1

    writer, werr = make_writer(a.mock)

    # 1. Заголовки к новым запросам: человек видит их в таблице и решает, одобрять ли
    titled = 0
    for row in plan.rows:
        if cfg["titles_per_run"] and titled >= cfg["titles_per_run"]:
            break  # 0 = без лимита: заголовок получает каждый ключ
        if not row["title"] and row["status"] in ("new", ""):
            row["title"] = propose_title(writer, row)
            titled += 1
    plan.save()
    if added or titled:
        print(f"План обновлён: новых запросов {added}, подобрано заголовков {titled}. Файл: {plan.path}")

    # 1б. Советник: только предупреждения и рекомендации, ключи и заголовки не меняются
    warns = advise(plan, advisor_pages(cfg))
    advisor_write(plan, warns)
    if warns:
        print(f"Советник: предупреждений {len({(w['kind'], w['found']) for w in warns})} (лист «Предупреждения»). "
              "Это рекомендации: решение принимаете вы.")

    # 2. Повтор публикации того, что уже написано
    retry_unpublished(cfg, plan)

    # 3. Лимит и одобрение
    limit = cfg["daily_limit"] - done_today(plan.rows)
    n = min(a.count if a.count is not None else limit, limit)
    if n <= 0:
        print(f"Дневной лимит ({cfg['daily_limit']}) уже выбран.")
        return 0

    failed_runs = failed_counts()
    # статья, не прошедшая проверку, берётся в работу снова при следующем запуске (до max_failed_runs раз)
    retry = [r for r in plan.rows if r["status"] == "failed" and failed_runs.get(r["keyword"], 0) < cfg["max_failed_runs"]]
    waiting = [r for r in plan.rows if r["status"] in ("new", "")] + retry
    # Жёсткое правило: только строки с «да» от человека. Отключить нельзя.
    pending = [r for r in waiting if is_approved(r["approved"])]
    if a.keyword:
        pending = [r for r in pending if r["keyword"].strip().lower() == a.keyword.strip().lower()]
    if not pending:
        if waiting:
            print(f"Нет одобренных статей. Откройте {os.path.basename(plan.path)}, проверьте заголовки "
                  f"и поставьте «да» в колонке «Одобрено» (ожидают: {len(waiting)}).")
        else:
            print("В плане нет статей для написания.")
        return 0
    if writer is None:
        print(f"Ошибка: {werr}", file=sys.stderr)
        return 2

    facts = read_text(cfg["facts_path"])
    pages = read_pages(cfg["pages_path"])
    pending.sort(key=lambda r: int(r["priority"]) if str(r["priority"]).isdigit() else 999)
    past = [r["title"] or r["keyword"] for r in plan.rows if r["status"] in ("published", "ready")]

    made = 0
    for row in pending[:n]:
        # перед КАЖДОЙ статьёй заново смотрим ваш файл: «да» могли снять или поправить заголовок
        sync_plan.pull(cfg, quiet=True, plan=plan)
        if not is_approved(row["approved"]) or row["status"] not in ("new", "", "failed"):
            print(f"[пропуск] {row['keyword']}: в вашем файле «да» снято или статус изменился")
            continue
        try:
            require_approved(cfg, keyword=row["keyword"], plan=plan)
        except ApprovalError as e:
            print(f"[пропуск] {row['keyword']}: {e}")
            continue
        if row.get("warning"):
            print(f"[внимание] {row['keyword']}: {row['warning'][:300]}")
            print(f"   Рекомендация: {row.get('recommendation', '')[:300]}")
            print("   Пишу, потому что вы поставили «да».")
        try:
            raw, meta, body, res = produce(row, cfg, writer, facts, pages, past)
        except Exception as e:  # сеть или API: одна статья не должна ронять весь запуск
            raw, res = None, {"errors": [f"сбой генерации: {str(e)[:200]}"]}
        if raw is None:
            row["status"], row["note"] = "failed", "; ".join(res["errors"])[:300]
            row["recommendation"] = advise_failure(res["errors"])
            log({"event": "failed", "keyword": row["keyword"], "errors": res["errors"]})
            LAST["failed"] += 1
            LAST["errors"].append(f"{row['keyword']}: {'; '.join(res['errors'])[:160]}")
        else:
            slug = slugify(row["keyword"])
            status, url, md, has_img = deliver(row, cfg, raw, meta, writer, slug)
            h1 = re.search(r"^#\s+(.+)$", body, re.M)
            row.update(slug=slug, date=dt.date.today().isoformat(), file=os.path.relpath(md, ROOT),
                       title=h1.group(1).strip() if h1 else row["title"],
                       note="" if has_img else "без обложки")
            mark_published(row, status, url)
            past.append(row["title"])
            made += 1
            LAST["created"] += 1
            LAST["published"] += 1 if status == "published" else 0
            LAST["articles"].append({"keyword": row["keyword"], "title": row["title"], "url": url or "",
                                     "status": status})
            log({"event": status, "keyword": row["keyword"], "file": md, "stats": res["stats"]})
            print(f"[{status}] {row['keyword']} -> {md}")
        plan.save()

    sync_plan.push_status(cfg)
    print(f"Готово: {made} из {n}. Таблица: {plan.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
