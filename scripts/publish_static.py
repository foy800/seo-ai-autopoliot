#!/usr/bin/env python3
"""Публикация статьи на обычный сайт без CMS: статичные HTML-страницы + FTP.

Что делает publish():
  1. Превращает статью .md в страницу <blog_path>/<slug>/index.html по шаблону.
  2. Кладёт рядом обложку cover.png.
  3. Пересобирает список статей <blog_path>/index.html и карту <blog_path>/sitemap-blog.xml.
  4. publish_mode "local": файлы остаются в site_dir. "ftp": то же самое плюс загрузка по FTP.

Реквизиты FTP берутся только из .env: FTP_HOST, FTP_USER, FTP_PASS
(по желанию FTP_PORT и FTP_TLS=1). В конфиг и в репозиторий пароль не пишется.

Ручной запуск (пересборка и заливка готовой статьи):
  python3 scripts/publish_static.py out/2026-09-30-slug.md
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from article_check import parse_article  # noqa: E402
from common import abspath, load_config, load_env  # noqa: E402
from gate import require_approved  # noqa: E402

DEFAULT_TEMPLATE = "templates/page.template.html"


# ---------- Markdown -> HTML ----------
def inline(text):
    t = html.escape(text, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', t)
    return t


def md_to_html(body):
    lines = body.splitlines()
    out, i = [], 0
    sep = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
        elif ln.startswith("#"):
            lvl = min(len(ln) - len(ln.lstrip("#")), 4)
            out.append(f"<h{lvl}>{inline(ln.lstrip('#').strip())}</h{lvl}>")
            i += 1
        elif "|" in ln and i + 1 < len(lines) and sep.match(lines[i + 1]):
            rows, j = [ln], i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                rows.append(lines[j])
                j += 1
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            head = "".join(f"<th>{inline(c)}</th>" for c in cells[0])
            trs = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in cells[1:])
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{trs}</tbody></table>")
            i = j
        elif re.match(r"^\s*[-*+]\s+", ln) or re.match(r"^\s*\d+[.)]\s+", ln):
            ordered = bool(re.match(r"^\s*\d+[.)]\s+", ln))
            rx = r"^\s*\d+[.)]\s+" if ordered else r"^\s*[-*+]\s+"
            items = []
            while i < len(lines) and re.match(rx, lines[i]):
                items.append(f"<li>{inline(re.sub(rx, '', lines[i]))}</li>")
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
        else:
            par = [ln.strip()]
            i += 1
            while (i < len(lines) and lines[i].strip()
                   and not re.match(r"^(#|\s*[-*+]\s|\s*\d+[.)]\s)", lines[i]) and "|" not in lines[i]):
                par.append(lines[i].strip())
                i += 1
            text = " ".join(par)
            cls = ' class="author"' if re.match(r"(Автор|Рецензент):", text) else ""
            out.append(f"<p{cls}>{inline(text)}</p>")
    return "\n".join(out)


# ---------- страницы ----------
def read_template(cfg):
    with open(abspath(cfg.get("site_template") or DEFAULT_TEMPLATE), encoding="utf-8") as f:
        return f.read()


def fill(tpl, values):
    for k, v in values.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def site_root(cfg):
    return cfg.get("site_url", "").rstrip("/")


def blog_url(cfg, *parts):
    return "/" + "/".join([cfg.get("blog_path", "blog").strip("/")] + list(parts))


def render_article(cfg, meta, body, slug, date, has_image):
    h1 = (re.search(r"^#\s+(.+)$", body, re.M) or [None, slug])[1]
    canonical = site_root(cfg) + blog_url(cfg, slug) + "/"
    image_url = site_root(cfg) + blog_url(cfg, slug, "cover.png") if has_image else ""
    content = md_to_html(body)
    if has_image:  # обложка сразу после H1
        img = (f'<img class="cover" src="{blog_url(cfg, slug, "cover.png")}" '
               f'alt="{html.escape(meta.get("IMAGE_ALT", ""), quote=True)}" width="1536" height="1024">')
        content = re.sub(r"(</h1>)", lambda m: m.group(1) + "\n" + img, content, count=1)
    jsonld = {
        "@context": "https://schema.org", "@type": "Article", "headline": h1,
        "description": meta.get("META_DESCRIPTION", ""), "datePublished": date,
        "mainEntityOfPage": canonical, **({"image": image_url} if image_url else {}),
    }
    return fill(read_template(cfg), {
        "title": html.escape(meta.get("META_TITLE") or h1, quote=True),
        "description": html.escape(meta.get("META_DESCRIPTION", ""), quote=True),
        "canonical": canonical, "image_url": image_url,
        "site_name": html.escape(cfg.get("site_name", ""), quote=True),
        "date": date, "content": content,
        "jsonld": json.dumps(jsonld, ensure_ascii=False).replace("</", "<\\/"),
    })


def render_index(cfg, items):
    lis = "\n".join(
        f'<li><a href="{blog_url(cfg, it["slug"])}/">{html.escape(it["title"])}</a>'
        f'<time datetime="{it["date"]}"> {it["date"]}</time>'
        f'<p>{html.escape(it["description"])}</p></li>'
        for it in sorted(items, key=lambda x: x["date"], reverse=True)
    )
    title = cfg.get("blog_title") or "Статьи"
    content = f'<h1>{html.escape(title)}</h1>\n<ul class="posts">\n{lis}\n</ul>'
    return fill(read_template(cfg), {
        "title": html.escape(title, quote=True),
        "description": html.escape(cfg.get("blog_description", ""), quote=True),
        "canonical": site_root(cfg) + blog_url(cfg) + "/", "image_url": "",
        "site_name": html.escape(cfg.get("site_name", ""), quote=True),
        "date": "", "content": content, "jsonld": "{}",
    })


def render_sitemap(cfg, items):
    urls = "".join(
        f"  <url><loc>{site_root(cfg)}{blog_url(cfg, it['slug'])}/</loc><lastmod>{it['date']}</lastmod></url>\n"
        for it in items
    )
    idx = f"  <url><loc>{site_root(cfg)}{blog_url(cfg)}/</loc></url>\n"
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + idx + urls + "</urlset>\n")


# ---------- FTP ----------
def ftp_upload(files, cfg):
    """files: список (локальный путь, путь относительно корня сайта на сервере)."""
    import ftplib
    load_env()
    host, user, pw = (os.environ.get(k, "").strip() for k in ("FTP_HOST", "FTP_USER", "FTP_PASS"))
    if not (host and user and pw):
        raise RuntimeError("FTP_HOST, FTP_USER и FTP_PASS должны быть заданы в .env (не в чате и не в конфиге)")
    port = int(os.environ.get("FTP_PORT", "21"))
    use_tls = os.environ.get("FTP_TLS", "").strip().lower() in ("1", "true", "yes")
    ftp = ftplib.FTP_TLS() if use_tls else ftplib.FTP()
    ftp.connect(host, port, timeout=60)
    ftp.login(user, pw)
    if use_tls:
        ftp.prot_p()
    root = cfg.get("ftp_dir", "").strip("/")
    known = set()

    def cd_make(path):
        ftp.cwd("/")
        cur = ""
        for part in [p for p in path.split("/") if p]:
            cur += "/" + part
            if cur not in known:
                try:
                    ftp.cwd(cur)
                except ftplib.error_perm:
                    ftp.mkd(cur)
                    ftp.cwd(cur)
                known.add(cur)
            else:
                ftp.cwd(cur)

    try:
        for local, remote in files:
            full = "/".join(x for x in (root, remote.strip("/")) if x)
            folder, _, name = full.rpartition("/")
            cd_make(folder)
            with open(local, "rb") as f:
                ftp.storbinary(f"STOR {name}", f)
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


# ---------- главная функция ----------
def publish(cfg, md_path, image_path, slug, date, meta=None):
    """Собирает страницы и, если publish_mode == 'ftp', заливает их. Возвращает URL статьи.
    Без «да» в колонке «Одобрено» контент-плана бросает ApprovalError: публикация невозможна."""
    require_approved(cfg, slug=slug)
    meta2, body = parse_article(open(md_path, encoding="utf-8").read())
    meta = meta or meta2
    site_dir = abspath(cfg.get("site_dir", "out/site"))
    blog_rel = cfg.get("blog_path", "blog").strip("/")
    art_dir = os.path.join(site_dir, blog_rel, slug)
    os.makedirs(art_dir, exist_ok=True)

    has_image = bool(image_path) and os.path.exists(image_path) and os.path.getsize(image_path) > 100
    files = []
    page = os.path.join(art_dir, "index.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write(render_article(cfg, meta, body, slug, date, has_image))
    files.append((page, f"{blog_rel}/{slug}/index.html"))
    if has_image:
        cover = os.path.join(art_dir, "cover.png")
        with open(image_path, "rb") as src, open(cover, "wb") as dst:
            dst.write(src.read())
        files.append((cover, f"{blog_rel}/{slug}/cover.png"))

    reg_path = abspath("state/published.json")
    reg = json.load(open(reg_path, encoding="utf-8")) if os.path.exists(reg_path) else []
    h1 = (re.search(r"^#\s+(.+)$", body, re.M) or [None, slug])[1]
    reg = [r for r in reg if r["slug"] != slug] + [
        {"slug": slug, "title": h1, "description": meta.get("META_DESCRIPTION", ""), "date": date}]

    sitemap = os.path.join(site_dir, blog_rel, "sitemap-blog.xml")
    with open(sitemap, "w", encoding="utf-8") as f:
        f.write(render_sitemap(cfg, reg))
    files.append((sitemap, f"{blog_rel}/sitemap-blog.xml"))
    if cfg.get("update_blog_index", True):
        index = os.path.join(site_dir, blog_rel, "index.html")
        with open(index, "w", encoding="utf-8") as f:
            f.write(render_index(cfg, reg))
        files.append((index, f"{blog_rel}/index.html"))

    if cfg.get("publish_mode") == "ftp":
        ftp_upload(files, cfg)
    # реестр сохраняем только после успешной загрузки: сбой FTP не должен вносить статью в список блога
    os.makedirs(os.path.dirname(reg_path), exist_ok=True)
    with open(reg_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    return site_root(cfg) + blog_url(cfg, slug) + "/"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("--image", default=None)
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    load_env()
    cfg = load_config(a.config)
    base = os.path.basename(a.md)
    m = re.match(r"(\d{4}-\d{2}-\d{2})-(.+)\.md$", base)
    date, slug = (m.group(1), m.group(2)) if m else (dt.date.today().isoformat(), base[:-3])
    print(publish(cfg, a.md, a.image or re.sub(r"\.md$", ".png", a.md), slug, date))


if __name__ == "__main__":
    main()
