#!/usr/bin/env python3
"""Проверка SEO-статьи перед публикацией.

Использование:
  python3 article_check.py статья.md --keyword "жидкие виниры" \
      --secondary "цена,отзывы" [--config config.json] [--pages context/pages.csv]

Код возврата 0 — статья прошла, 1 — есть ошибки. В stdout печатается JSON.
"""
import argparse
import csv
import json
import os
import re
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import abspath, count_phrase, load_config, stems, tokens  # noqa: E402

# Жёсткие запреты из humanizer-ru (сокращённо). (regex, что это)
HARD_BANS = [
    (r"не\s+просто\b[^.!?\n]{0,80}?\bа\b", "«не просто X, а Y»"),
    (r"не\s+только\b[^.!?\n]{0,80}?\bно\s+и\b", "«не только X, но и Y»"),
    (r"в\s+современном\s+мире", "«в современном мире»"),
    (r"в\s+условиях\s+\w+(ого|ой|ых|ого|ем)\b", "«в условиях …»"),
    (r"стоит\s+отметить", "«стоит отметить»"),
    (r"важно\s+(понимать|помнить|отметить|учитывать)", "«важно понимать/помнить»"),
    (r"\bданн(ый|ая|ое|ого|ой|ому)\b", "«данный»"),  # без «данные», «по данным»: это существительное
    (r"игра(ет|ют|ла|ли)\s+(важную|ключевую|большую|значительную)\s+роль", "«играет важную роль»"),
    (r"можно\s+с\s+уверенностью\s+сказать", "«можно с уверенностью сказать»"),
    (r"подводя\s+итог", "«подводя итог»"),
    (r"(^|[.!?]\s+)таким\s+образом,", "«таким образом,» в начале"),
    (r"погрузимся|давайте\s+(разберём|посмотрим|разберемся)", "анонс вместо сути"),
    (r"самое\s+интересное", "«самое интересное»"),
    (r"раскры(ть|вает|ваем)\s+потенциал", "«раскрыть потенциал»"),
    (r"на\s+новый\s+уровень", "«на новый уровень»"),
    (r"откры(вает|ть)\s+нов(ые|ых)\s+(горизонт|перспектив|возможност)", "«открывает новые горизонты»"),
    (r"комплексн(ый|ого|ым|ое)\s+(подход|решени)", "«комплексный подход»"),
    (r"в\s+связи\s+с\s+этим", "«в связи с этим»"),
    (r"надеюсь,?\s+(это\s+)?(помог|был)", "артефакт чата"),
    (r"эксперты\s+(считают|говорят|отмечают)|исследования\s+показывают", "размытый авторитет"),
    (r"в\s+заключени[еи]\b", "«в заключение»"),
]

CTA_VERB_RE = re.compile(
    r"запис(аться|ать|ывайтесь|ь)|позвоните|звоните|обратитесь|напишите|перейдите|узнайте|"
    r"заказать|закажите|оставьте\s+заявк|запросите|получите\s+консультаци|свяжитесь|"
    r"приходите|консультаци",
    re.I,
)
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")

# \u0423\u0441\u0438\u043B\u0438\u0442\u0435\u043B\u0438 \u0438 \u0432\u0432\u043E\u0434\u043D\u044B\u0435 \u0441\u043B\u043E\u0432\u0430: \u0432\u043E\u0434\u0430 \u043F\u043E \u043F\u0440\u0430\u0432\u0438\u043B\u0430\u043C \u0440\u0435\u0434\u0430\u043A\u0446\u0438\u0438.
FILLER_WORDS = [
    "\u043E\u0447\u0435\u043D\u044C", "\u043A\u0440\u0430\u0439\u043D\u0435", "\u0447\u0440\u0435\u0437\u0432\u044B\u0447\u0430\u0439\u043D\u043E", "\u043D\u0435\u0432\u0435\u0440\u043E\u044F\u0442\u043D\u043E", "\u0431\u0435\u0437\u0443\u0441\u043B\u043E\u0432\u043D\u043E", "\u043D\u0435\u0441\u043E\u043C\u043D\u0435\u043D\u043D\u043E",
    "\u0440\u0430\u0437\u0443\u043C\u0435\u0435\u0442\u0441\u044F", "\u0435\u0441\u0442\u0435\u0441\u0442\u0432\u0435\u043D\u043D\u043E", "\u043F\u043E\u0436\u0430\u043B\u0443\u0439", "\u043D\u0430\u0432\u0435\u0440\u043D\u043E\u0435", "\u043A\u0430\u043A \u0438\u0437\u0432\u0435\u0441\u0442\u043D\u043E",
    "\u043D\u0430 \u0441\u0430\u043C\u043E\u043C \u0434\u0435\u043B\u0435", "\u043F\u043E \u0441\u0443\u0442\u0438", "\u0432 \u0446\u0435\u043B\u043E\u043C", "\u0432 \u043F\u0435\u0440\u0432\u0443\u044E \u043E\u0447\u0435\u0440\u0435\u0434\u044C", "\u043F\u0440\u0435\u0436\u0434\u0435 \u0432\u0441\u0435\u0433\u043E",
    "\u043A \u0441\u043E\u0436\u0430\u043B\u0435\u043D\u0438\u044E", "\u043A \u0441\u0447\u0430\u0441\u0442\u044C\u044E", "\u0443\u043D\u0438\u043A\u0430\u043B\u044C\u043D", "\u0438\u0434\u0435\u0430\u043B\u044C\u043D", "\u043F\u043E\u0442\u0440\u044F\u0441\u0430\u044E\u0449", "\u0432\u0435\u043B\u0438\u043A\u043E\u043B\u0435\u043F\u043D",
]

# \u0422\u0438\u043F\u0438\u0447\u043D\u044B\u0435 \u043E\u0440\u0444\u043E\u0433\u0440\u0430\u0444\u0438\u0447\u0435\u0441\u043A\u0438\u0435 \u0438 \u043F\u0443\u043D\u043A\u0442\u0443\u0430\u0446\u0438\u043E\u043D\u043D\u044B\u0435 \u043E\u0448\u0438\u0431\u043A\u0438.
SPELLING = [
    (r"\b\u043F\u043E\s+\u043E\u043A\u043E\u043D\u0447\u0430\u043D\u0438\u044E\b", "\u00AB\u043F\u043E \u043E\u043A\u043E\u043D\u0447\u0430\u043D\u0438\u044E\u00BB \u2192 \u00AB\u043F\u043E \u043E\u043A\u043E\u043D\u0447\u0430\u043D\u0438\u0438\u00BB"),
    (r"\b\u0432\s+\u0442\u0435\u0447\u0435\u043D\u0438\u0438\s+(\d+|\u0434\u0432\w+|\u0442\u0440\w+|\u0447\u0435\u0442\u044B\u0440\w+|\u043D\u0435\u0441\u043A\u043E\u043B\u044C\u043A\w+|\u0441\u0443\u0442\w+|\u0434\u043D\u044F|\u0434\u043D\u0435\u0439|\u043D\u0435\u0434\u0435\u043B\w+|\u043C\u0435\u0441\u044F\u0446\w*|\u0433\u043E\u0434\w*|\u0447\u0430\u0441\w*|\u0441\u0440\u043E\u043A\w+)", "\u00AB\u0432 \u0442\u0435\u0447\u0435\u043D\u0438\u0438\u00BB \u2192 \u00AB\u0432 \u0442\u0435\u0447\u0435\u043D\u0438\u0435\u00BB"),
    (r"\b\u0434\u043B\u044F\s+\u0442\u043E\u0433\u043E,\s+\u0447\u0442\u043E\u0431\u044B\b", "\u00AB\u0434\u043B\u044F \u0442\u043E\u0433\u043E, \u0447\u0442\u043E\u0431\u044B\u00BB \u2192 \u00AB\u0434\u043B\u044F \u0442\u043E\u0433\u043E \u0447\u0442\u043E\u0431\u044B\u00BB"),
    (r"\b\w+[\u0430\u044F\u0443]\u0435\u0442\u044C\u0441\u044F\b", "\u00AB-\u0435\u0442\u044C\u0441\u044F\u00BB \u2192 \u00AB-\u0435\u0442\u0441\u044F\u00BB"),
    (r"\b\u043F\u043E-\u044D\u0442\u043E\u043C\u0443\b|\b\u0442\u0430\u043A-\u0436\u0435\b|\b\u0442\u0430\u043A-\u043A\u0430\u043A\b", "\u0441\u043B\u0438\u0442\u043D\u043E\u0435/\u0440\u0430\u0437\u0434\u0435\u043B\u044C\u043D\u043E\u0435 \u043D\u0430\u043F\u0438\u0441\u0430\u043D\u0438\u0435"),
    (r"\b\u043F\u043E\s+\u0441\u0440\u0435\u0434\u0441\u0442\u0432\u0430\u043C\b", "\u00AB\u043F\u043E\u0441\u0440\u0435\u0434\u0441\u0442\u0432\u043E\u043C\u00BB \u043F\u0438\u0448\u0435\u0442\u0441\u044F \u0441\u043B\u0438\u0442\u043D\u043E"),
    (r"\b\u0432\s+\u0441\u043B\u0435\u0434\u0441\u0442\u0432\u0438\u0438\b", "\u00AB\u0432\u0441\u043B\u0435\u0434\u0441\u0442\u0432\u0438\u0435\u00BB \u043F\u0438\u0448\u0435\u0442\u0441\u044F \u0441\u043B\u0438\u0442\u043D\u043E"),
    (r"\s[,;:!?](?=\s|$)", "\u043F\u0440\u043E\u0431\u0435\u043B \u043F\u0435\u0440\u0435\u0434 \u0437\u043D\u0430\u043A\u043E\u043C \u043F\u0440\u0435\u043F\u0438\u043D\u0430\u043D\u0438\u044F"),
    (r"[,;:!?][\u0410-\u042F\u0401\u0430-\u044F\u0451]", "\u043D\u0435\u0442 \u043F\u0440\u043E\u0431\u0435\u043B\u0430 \u043F\u043E\u0441\u043B\u0435 \u0437\u043D\u0430\u043A\u0430 \u043F\u0440\u0435\u043F\u0438\u043D\u0430\u043D\u0438\u044F"),
]

FIRST_PERSON_RE = re.compile(
    r"\b(\u043C\u044B|\u043D\u0430\u0448\w*|\u043D\u0430\u043C|\u043D\u0430\u0441|\u043D\u0430\u043C\u0438|\u044F|\u043C\u043D\u0435|\u043C\u0435\u043D\u044F|\u043C\u043D\u043E\u0439|\u0432\u044B|\u0432\u0430\u043C|\u0432\u0430\u0441|\u0432\u0430\u043C\u0438|\u0432\u0430\u0448\w*)\b", re.I)
COMMERCIAL_ROOTS = {"\u043E\u0444\u043E\u0440\u043C": "\u043E\u0444\u043E\u0440\u043C\u0438\u0442\u044C", "\u043F\u043E\u043B\u0443\u0447": "\u043F\u043E\u043B\u0443\u0447\u0438\u0442\u044C", "\u043A\u0443\u043F\u0438": "\u043A\u0443\u043F\u0438\u0442\u044C", "\u043A\u0443\u043F\u043B": "\u043A\u0443\u043F\u0438\u0442\u044C", "\u0437\u0430\u043A\u0430\u0437": "\u0437\u0430\u043A\u0430\u0437\u0430\u0442\u044C"}
CALLOUT_RE = re.compile(r"\*\*\s*(\u0412\u0430\u0436\u043D\u043E!?|\u041E\u0431\u0440\u0430\u0442\u0438\u0442\u0435 \u0432\u043D\u0438\u043C\u0430\u043D\u0438\u0435!?|\u0414\u043E\u043F\u043E\u043B\u043D\u0438\u0442\u0435\u043B\u044C\u043D\u0430\u044F \u0438\u043D\u0444\u043E\u0440\u043C\u0430\u0446\u0438\u044F)[^*]*\*\*", re.I)
CONCLUSION_RE = re.compile(r"^(\u0432\u044B\u0432\u043E\u0434|\u0432\u044B\u0432\u043E\u0434\u044B|\u0438\u0442\u043E\u0433|\u0438\u0442\u043E\u0433\u0438)\s*$", re.I)


def sentence_count(text):
    return len([s for s in re.split(r"(?<=[.!?\u2026])\s+", visible_text(text).strip()) if len(tokens(s)) > 1])


def parse_article(raw):
    """Делит файл на служебные поля (до ---) и тело статьи."""
    meta, body = {}, raw
    m = re.search(r"^---\s*$", raw, re.M)
    if m and re.match(r"^\s*(META_TITLE|META_DESCRIPTION|IMAGE_SCENE|IMAGE_ALT)", raw):
        head, body = raw[: m.start()], raw[m.end():]
        for line in head.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
    return meta, body.strip()


def visible_text(md):
    """Текст без разметки, таблиц-разделителей и адресов ссылок."""
    t = re.sub(r"^\s*\|?[\s:\-|]+\|[\s:\-|]*$", "", md, flags=re.M)  # разделитель таблицы
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"[*_`>|]", " ", t)
    t = re.sub(r"^\s*[-+]\s+", "", t, flags=re.M)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def load_allowed(pages_path):
    urls = set()
    if pages_path and os.path.exists(pages_path):
        with open(pages_path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                u = (row.get("url") or "").strip().rstrip("/")
                if u:
                    urls.add(u)
    return urls


def norm_url(u):
    u = u.split("#")[0].strip()
    return u.rstrip("/")


def find_tables(md):
    """Возвращает число таблиц: шапка, разделитель и минимум 2 строки данных."""
    lines = md.splitlines()
    count, i = 0, 0
    sep = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
    while i < len(lines):
        if "|" in lines[i] and i + 1 < len(lines) and sep.match(lines[i + 1]):
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                j += 1
            if j - (i + 2) >= 2:
                count += 1
            i = j
        else:
            i += 1
    return count


def check(md, meta, keyword, secondary, cfg, allowed_urls,
          lsi=None, kind="info", max_chars=0):
    errors, warnings = [], []
    lsi = [x for x in (lsi or []) if x]
    text = visible_text(md)
    lower = text.lower()
    words = tokens(text)
    st = stems(text)
    chars = len(text)

    # Объём
    if chars < cfg["min_chars"]:
        errors.append(f"объём {chars} знаков, нужно не меньше {cfg['min_chars']}")
    elif chars < cfg.get("target_chars", 0) and kind != "commercial":
        warnings.append(f"объём {chars} знаков, целевой {cfg['target_chars']}")
    if kind == "commercial" and max_chars and chars > max_chars:
        errors.append(f"коммерческий текст {chars} знаков, лимит задания {max_chars}")

    # Заголовки
    h1s = re.findall(r"^#\s+(.+)$", md, re.M)
    h2s = re.findall(r"^##\s+(.+)$", md, re.M)
    if len(h1s) != 1:
        errors.append(f"H1 должен быть один, найдено {len(h1s)}")
    if len(h2s) < 3:
        errors.append(f"H2 нужно минимум 3, найдено {len(h2s)}")
    if re.search(r"^###\s", md, re.M) and not h2s:
        errors.append("H3 без H2")
    if re.search(r"^#{4,}\s", md, re.M):
        warnings.append("уровни глубже H3 не нужны")

    # Ключи
    if keyword:
        if h1s and count_phrase(stems(h1s[0]), keyword) == 0:
            errors.append(f"основной ключ «{keyword}» не найден в H1")
        first_par = next(
            (p for p in re.split(r"\n\s*\n", md)
             if p.strip() and not p.lstrip().startswith("#")
             and not re.match(r"\s*(автор|рецензент)\s*:", p, re.I)),
            "",
        )
        if count_phrase(stems(first_par), keyword) == 0:
            errors.append("основной ключ не найден в первом абзаце")
        body_hits = count_phrase(st, keyword)
        if body_hits < cfg["min_keyword_in_body"]:
            errors.append(f"ключ встречается {body_hits} раз, нужно не меньше {cfg['min_keyword_in_body']}")
        if keyword.lower() not in lower:
            warnings.append("точная форма ключа не встречается ни разу (только склонения)")
        kw_words = max(1, len(tokens(keyword)))
        density = body_hits * kw_words / max(1, len(words)) * 100
        if density > cfg["max_keyword_density_pct"]:
            errors.append(f"плотность ключа {density:.1f}% больше {cfg['max_keyword_density_pct']}%: переспам")
        if not any(count_phrase(stems(h), keyword) for h in h2s):
            warnings.append("основного ключа нет ни в одном H2 (допустимо, но проверь смысл)")
    # Лимиты повторов по длине ключа: 2 слова до 5, 3 слова до 2, от 4 слов до 1
    for phrase in [k for k in [keyword] + list(secondary) if k]:
        n_words, hits = len(tokens(phrase)), count_phrase(st, phrase)
        cap = 5 if n_words <= 2 else (2 if n_words == 3 else 1)
        if hits > cap:
            errors.append(f"ключ «{phrase}» ({n_words} сл.) встречается {hits} раз, максимум {cap}")
    sec_missing = [s for s in secondary if s and count_phrase(st, s) == 0]
    if secondary and len(sec_missing) > len([s for s in secondary if s]) / 2:
        errors.append("больше половины дополнительных ключей отсутствует: " + ", ".join(sec_missing))
    elif sec_missing:
        warnings.append("нет доп. ключей: " + ", ".join(sec_missing))

    # Таблица
    tables = find_tables(md)
    if tables < 1:
        errors.append("нет таблицы (нужна шапка, разделитель и минимум 2 строки данных)")

    # Ссылки
    links = re.findall(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)", md)
    site_host = urlparse(cfg.get("site_url", "")).netloc
    cta_url = norm_url(cfg.get("cta_url", ""))
    internal = []
    for anchor, url in links:
        u = norm_url(url)
        host = urlparse(u).netloc
        is_internal = (not host) or (site_host and host == site_host)
        if is_internal:
            internal.append((anchor, u))
    body_internal = [x for x in internal if norm_url(x[1]) != cta_url]
    if len(body_internal) < cfg["min_internal_links"]:
        errors.append(f"внутренних ссылок {len(body_internal)}, нужно от {cfg['min_internal_links']}")
    if len(body_internal) > cfg["max_internal_links"]:
        errors.append(f"внутренних ссылок {len(body_internal)}, больше {cfg['max_internal_links']}")
    if allowed_urls:
        for anchor, u in internal:
            full = u if urlparse(u).netloc else (cfg.get("site_url", "").rstrip("/") + "/" + u.lstrip("/"))
            if norm_url(full) not in {norm_url(x) for x in allowed_urls} and norm_url(u) != cta_url:
                errors.append(f"ссылка на страницу вне списка: {u}")
    urls_seen = [u for _, u in body_internal]
    if len(urls_seen) != len(set(urls_seen)):
        warnings.append("одна страница указана в тексте больше одного раза")
    stuffed = [a for a, _ in links if len(tokens(a)) > 8]
    if stuffed:
        warnings.append("слишком длинный анкор: " + stuffed[0][:60])

    # CTA в конце
    blocks = [b for b in re.split(r"\n\s*\n", md.strip()) if b.strip()]
    last = blocks[-1] if blocks else ""
    if last.lstrip().startswith("#") or "|" in last[:3]:
        errors.append("после последнего H2 нет финального абзаца с CTA")
    else:
        cta_link = re.search(r"\]\(([^)\s]+)\)", last)
        if not cta_link:
            errors.append("в финальном абзаце нет ссылки на услугу (CTA)")
        elif cta_url and norm_url(cta_link.group(1)) != cta_url:
            errors.append(f"CTA ведёт не на {cfg['cta_url']}")
        if not CTA_VERB_RE.search(last):
            errors.append("в финале нет призыва к действию (запишитесь, позвоните, обратитесь)")

    # Структура: вступление, вывод, врезки, списки
    first_p = next(
        (p for p in blocks
         if not p.lstrip().startswith("#")
         and not re.match(r"\s*(автор|рецензент)\s*:", p, re.I)),
        "",
    )
    n_intro = sentence_count(first_p)
    if not 2 <= n_intro <= 3:
        errors.append(f"вступление: {n_intro} предложений, нужно 2-3")
    keys_first = [k for k in [keyword] + list(secondary) if k and count_phrase(stems(first_p), k)]
    if len(keys_first) > 2:
        errors.append("в первом абзаце больше двух ключей: " + ", ".join(keys_first))
    concl = re.search(r"^##\s+(вывод\w*|итог\w*)\s*\n(.*?)(?=^##\s|\Z)", md, re.M | re.S | re.I)
    if not concl:
        errors.append("нет раздела «Вывод»")
    else:
        cblocks = [b for b in re.split(r"\n\s*\n", concl.group(2).strip()) if b.strip()]
        if cblocks and cblocks[-1] == last:
            cblocks = cblocks[:-1]  # последний блок это CTA
        n_concl = sentence_count("\n\n".join(cblocks))
        if not 2 <= n_concl <= 3:
            errors.append(f"вывод: {n_concl} предложений, нужно 2-3")
    if not CALLOUT_RE.search(md):
        errors.append("нет врезки жирным: **Важно!**, **Обратите внимание!** или **Дополнительная информация**")
    if not re.search(r"^\s*(?:[-*+]|\d+[.)])\s+\S", md, re.M):
        errors.append("нет маркированного или нумерованного списка")

    # Стиль: третье лицо, вода, тавтология, орфография
    body_wo_cta = visible_text("\n\n".join(blocks[:-1]))
    fp = FIRST_PERSON_RE.findall(body_wo_cta)
    if fp:
        errors.append("текст не от 3-го лица: " + ", ".join(sorted({(m if isinstance(m, str) else m[0]).lower() for m in fp}))[:80])
    fillers = [w for w in FILLER_WORDS if w in lower]
    if fillers:
        errors.append("вода (усилители, вводные слова): " + ", ".join(fillers))
    if re.search(r"\b\w+(ейш|айш)(ий|ая|ее|ие|ого|ей|им|их)\b", lower):
        warnings.append("превосходная степень прилагательных")
    dup = re.search(r"\b([а-яё]{3,})[ \t]+\1\b", lower)
    if dup:
        errors.append(f"тавтология, слово подряд дважды: «{dup.group(1)}»")
    for sent in re.split(r"(?<=[.!?…])\s+", text):
        ss = [s for s in stems(sent) if len(s) >= 5]
        rep = {s for s in ss if ss.count(s) >= 3}
        if rep:
            warnings.append(f"однокоренные слова повторяются в одном предложении: {sent[:60]}...")
            break
    for rx, label in SPELLING:
        if re.search(rx, text, re.I):
            errors.append(f"ошибка написания: {label}")
    # Коммерческие слова: каждое не больше 4 раз, считая однокоренные
    comm = {}
    for s in tokens(text):
        for root, name in COMMERCIAL_ROOTS.items():
            if s.startswith(root):
                comm[name] = comm.get(name, 0) + 1
    for name, n in comm.items():
        if n > 4:
            errors.append(f"коммерческое слово «{name}» и однокоренные: {n} раз, максимум 4")

    # LSI-слова из задания
    if lsi:
        lsi_missing = [x for x in lsi if count_phrase(st, x) == 0]
        if len(lsi_missing) > len(lsi) / 2:
            errors.append("вписано меньше половины LSI-слов, нет: " + ", ".join(lsi_missing))
        elif lsi_missing:
            warnings.append("нет LSI-слов: " + ", ".join(lsi_missing))

    # Слоп
    for rx, label in HARD_BANS:
        if re.search(rx, text, re.I | re.M):
            errors.append(f"запрещённая конструкция: {label}")
    dash = len(re.findall(r"—|(?<=\s)[–-](?=\s)", text))
    if dash:
        errors.append(f"тире как знак препинания: {dash} шт. (замени запятой, двоеточием, точкой)")
    is_count = len(re.findall(r"\bявляется\b|\bявляются\b|\bявляясь\b", lower))
    if is_count > max(1, len(words) // 500 + 1):
        errors.append(f"«является» {is_count} раз, слишком много")
    if EMOJI_RE.search(text):
        errors.append("эмодзи в тексте")

    # Ритм: слишком ровные предложения выдают шаблон
    sents = [s for s in re.split(r"(?<=[.!?])\s+", text) if len(tokens(s)) > 1]
    lens = [len(tokens(s)) for s in sents]
    if len(lens) >= 8:
        mean = sum(lens) / len(lens)
        sd = (sum((x - mean) ** 2 for x in lens) / len(lens)) ** 0.5
        if sd < 3.5:
            warnings.append(f"предложения одной длины (σ={sd:.1f}), текст читается как шаблон")
        if mean > 24:
            warnings.append(f"предложения длинные (среднее {mean:.0f} слов)")

    # Стёртые заголовки
    for h in h2s + h1s:
        if re.fullmatch(r"\s*(заключение|введение|ключевые\s+преимущества)\s*", h, re.I):
            warnings.append(f"стёртый заголовок «{h.strip()}»")

    # Служебные поля
    if meta:
        if len(meta.get("META_TITLE", "")) > 70:
            warnings.append("META_TITLE длиннее 70 знаков")
        if not meta.get("META_DESCRIPTION"):
            warnings.append("нет META_DESCRIPTION")
        else:
            desc = meta["META_DESCRIPTION"]
            lo, hi = cfg.get("description_min", 160), cfg.get("description_max", 200)
            if not lo <= len(desc) <= hi:
                errors.append(f"META_DESCRIPTION {len(desc)} знаков, нужно {lo}-{hi}")
            if keyword and desc.lower().count(keyword.lower()) != 1:
                errors.append("основной ключ должен быть в META_DESCRIPTION ровно один раз, в исходной форме")
            emo = cfg.get("description_emojis") or []
            if emo and not any(e in desc for e in emo):
                errors.append("в META_DESCRIPTION нет эмодзи из списка задания: " + " ".join(emo))
        if not meta.get("IMAGE_SCENE"):
            warnings.append("нет IMAGE_SCENE для обложки")
    else:
        warnings.append("нет служебных полей META_* и IMAGE_*")

    stats = {
        "chars": chars, "words": len(words), "h2": len(h2s), "tables": tables,
        "internal_links": len(body_internal),
        "keyword_hits": count_phrase(st, keyword) if keyword else None,
    }
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--keyword", default="")
    ap.add_argument("--secondary", default="")
    ap.add_argument("--config", default=None)
    ap.add_argument("--pages", default=None)
    ap.add_argument("--lsi", default="")
    ap.add_argument("--kind", default="info", choices=["info", "commercial"])
    ap.add_argument("--max-chars", type=int, default=0)
    a = ap.parse_args()
    cfg = load_config(a.config)
    with open(a.file, encoding="utf-8") as f:
        raw = f.read()
    meta, body = parse_article(raw)
    allowed = load_allowed(abspath(a.pages or cfg["pages_path"]))
    sec = [s.strip() for s in a.secondary.split(",") if s.strip()]
    lsi = [x.strip() for x in a.lsi.split(",") if x.strip()]
    res = check(body, meta, a.keyword, sec, cfg, allowed, lsi=lsi, kind=a.kind, max_chars=a.max_chars)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
