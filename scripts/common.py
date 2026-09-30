"""Общие функции: .env, config.json, простая русская нормализация."""
import json
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_CONFIG = {
    "site_url": "",
    "region": "",
    "daily_limit": 3,
    "min_chars": 2000,
    "target_chars": 3500,
    "min_internal_links": 2,
    "max_internal_links": 6,
    "min_keyword_in_body": 1,
    "max_keyword_density_pct": 3.0,
    "description_min": 160,
    "description_max": 200,
    "description_emojis": [],
    "publish_mode": "",
    "site_name": "",
    "site_dir": "out/site",
    "blog_path": "blog",
    "blog_title": "Статьи",
    "blog_description": "",
    "site_template": "templates/page.template.html",
    "ftp_dir": "",
    "update_blog_index": True,
    "cta_url": "",
    "cta_contact": "",
    "humanize_pass": True,
    "max_attempts": 3,
    "author": "",
    "queue_path": "state/queue.csv",
    "pages_path": "context/pages.csv",
    "facts_path": "context/facts.md",
    "out_dir": "out",
    "publish_hook": "",
}


def load_env(path=None):
    """Читает .env в os.environ. Существующие переменные окружения не затирает."""
    path = path or os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), val)


def load_config(path=None):
    path = path or os.path.join(ROOT, "config.json")
    load_env()
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    # Переменные SEO_<ПАРАМЕТР> из .env или окружения перекрывают config.json
    for key, default in DEFAULT_CONFIG.items():
        raw = os.environ.get("SEO_" + key.upper())
        if raw is None or raw.strip() == "":
            continue
        raw = raw.strip()
        if isinstance(default, bool):
            cfg[key] = raw.lower() in ("1", "true", "yes", "да")
        elif isinstance(default, int):
            cfg[key] = int(raw)
        elif isinstance(default, float):
            cfg[key] = float(raw)
        elif isinstance(default, list):
            cfg[key] = [x.strip() for x in raw.split(",") if x.strip()]
        else:
            cfg[key] = raw
    return cfg


def abspath(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def stem(word):
    """Грубая основа русского слова: хватает, чтобы ловить смену падежа."""
    w = word.lower().replace("ё", "е")
    n = len(w)
    if n <= 3:
        return w
    if n <= 5:
        return w[: n - 1]
    return w[:5]


def tokens(text):
    return re.findall(r"[а-яёa-z0-9]+", text.lower())


def stems(text):
    return [stem(t) for t in tokens(text)]


def count_phrase(text_stems, phrase):
    """Сколько раз фраза встречается подряд по основам слов."""
    p = stems(phrase)
    if not p:
        return 0
    n, hits = len(p), 0
    for i in range(len(text_stems) - n + 1):
        if text_stems[i : i + n] == p:
            hits += 1
    return hits


def slugify(text):
    tr = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    out = "".join(tr.get(c, c) for c in text.lower())
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out[:70] or "article"
