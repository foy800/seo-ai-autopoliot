#!/usr/bin/env python3
"""Синхронизация контент-плана между вашим компьютером и сервером агента.

Зачем. Агент живёт на сервере и каждый день работает с серверным content-plan.xlsx. «Да» и правки
заголовков вы вносите у себя локально. Перед запуском и перед КАЖДОЙ публикацией агент подтягивает
ваш файл, видит изменения и только потом решает, что писать. Без «да» в вашем файле статья не уходит.

Что берётся откуда при слиянии (строки сопоставляются по основному ключу):
  из ВАШЕГО файла: Заголовок, Доп. ключи, LSI, Тип, Лимит знаков, Тема, Приоритет, «Одобрено»;
  с СЕРВЕРА остаётся: Статус, Дата публикации, Ссылка, Файл и служебные поля;
  новые строки из вашего файла добавляются; опубликованные строки не переписываются;
  строки, которых нет в вашем файле, не удаляются (удаление только с флагом --prune).
Агент сам «да» не ставит ни при каком слиянии.

Режимы (переменная PLAN_SYNC в .env):
  inbox (по умолчанию)  вы кладёте файл в plan-inbox/content-plan.xlsx на сервере командой push
  git                   файл лежит в закрытом git-репозитории (PLAN_REPO); сервер сам делает pull и
                        после работы отправляет обратно статусы
  off                   не синхронизировать

Команды на сервере (их вызывает pipeline.py сам):
  python3 scripts/sync_plan.py pull         # подтянуть ваши правки и слить в серверный план
  python3 scripts/sync_plan.py push-status  # (режим git) отправить обратно план со статусами

Команды на вашем компьютере (нужны ssh/scp; SYNC_SSH_* в .env или аргументы):
  python3 scripts/sync_plan.py push-local content-plan.xlsx --host 1.2.3.4 --user seoagent --key ~/.ssh/key --dir ~/seo-agent-ru
  python3 scripts/sync_plan.py fetch-local --host ... --user ... --key ... --dir ... [--out файл.xlsx]
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import abspath, load_config, load_env  # noqa: E402
from plan import DEFAULT_ROW, Plan, is_approved  # noqa: E402

HUMAN = ("title", "secondary", "lsi", "kind", "max_chars", "topic", "priority", "approved")
INBOX = "plan-inbox/content-plan.xlsx"
STATE = "state/plan_sync.json"


def _norm(s):
    return " ".join(str(s or "").lower().replace("ё", "е").split())


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def _state():
    import json
    p = abspath(STATE)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def _save_state(d):
    import json
    p = abspath(STATE)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def merge(server_plan, incoming_plan, prune=False):
    """Вливает человеческие поля из incoming в server_plan. Возвращает отчёт."""
    rep = {"updated": [], "added": [], "approved": [], "unapproved": [], "missing": [], "skipped_published": []}
    by = {_norm(r["keyword"]): r for r in server_plan.rows}
    seen = set()
    for r in incoming_plan.rows:
        k = _norm(r["keyword"])
        if not k:
            continue
        seen.add(k)
        cur = by.get(k)
        if cur is None:
            row = dict(DEFAULT_ROW)
            for f in HUMAN + ("keyword",):
                row[f] = r.get(f, "")
            row["status"] = "new"
            row["_row"] = None
            server_plan.rows.append(row)
            by[k] = row
            rep["added"].append(r["keyword"])
            if is_approved(r.get("approved", "")):
                rep["approved"].append(r["keyword"])
            continue
        if cur["status"] in ("published", "ready"):
            rep["skipped_published"].append(cur["keyword"])
            continue
        changed = []
        for f in HUMAN:
            if str(cur.get(f, "")).strip() != str(r.get(f, "")).strip():
                if f == "approved":
                    (rep["approved"] if is_approved(r.get(f, "")) else rep["unapproved"]).append(cur["keyword"])
                cur[f] = r.get(f, "")
                changed.append(f)
        if changed:
            rep["updated"].append(f"{cur['keyword']} ({', '.join(changed)})")
    for r in list(server_plan.rows):
        k = _norm(r["keyword"])
        if k not in seen and r["status"] not in ("published", "ready"):
            if prune:
                for c in range(1, server_plan.ws.max_column + 1):
                    if r.get("_row"):
                        server_plan.ws.cell(r["_row"], c).value = None
                server_plan.rows.remove(r)
            rep["missing"].append(r["keyword"])
    return rep


def _incoming_path(cfg):
    mode = (os.environ.get("PLAN_SYNC") or "inbox").strip().lower()
    if mode == "off":
        return None, mode
    if mode == "git":
        repo = os.environ.get("PLAN_REPO", "").strip()
        if not repo:
            raise RuntimeError("PLAN_SYNC=git, но PLAN_REPO не задан в .env")
        d = abspath("state/plan-repo")
        if not os.path.isdir(os.path.join(d, ".git")):
            subprocess.run(["git", "clone", "--quiet", repo, d], check=True, timeout=120)
        else:
            subprocess.run(["git", "-C", d, "pull", "--quiet", "--ff-only"], check=True, timeout=120)
        p = os.path.join(d, "content-plan.xlsx")
        return (p if os.path.exists(p) else None), mode
    p = abspath(INBOX)
    return (p if os.path.exists(p) else None), mode


def pull(cfg, quiet=False, plan=None):
    """Подтягивает ваш файл и сливает в серверный план. Возвращает отчёт или None, если менять нечего."""
    load_env()
    try:
        path, mode = _incoming_path(cfg)
    except Exception as e:
        print(f"[синхронизация плана] ошибка: {e}", file=sys.stderr)
        return None
    if not path:
        if not quiet and mode != "off":
            print(f"[синхронизация плана] режим {mode}: файла от вас пока нет ({INBOX}). Работаю с серверным планом.")
        return None
    sha = _sha(path)
    st = _state()
    if st.get("incoming_sha") == sha:
        return None
    server = plan if plan is not None else Plan(cfg)
    incoming = Plan(dict(cfg, plan_path=path))
    rep = merge(server, incoming)
    server.save()
    st["incoming_sha"] = sha
    _save_state(st)
    print(f"[синхронизация плана] подтянуты ваши правки: обновлено строк {len(rep['updated'])}, добавлено {len(rep['added'])}, "
          f"одобрено «да»: {len(rep['approved'])}, «да» снято: {len(rep['unapproved'])}; "
          f"в вашем файле отсутствуют (не удалены): {len(rep['missing'])}")
    return rep


def push_status(cfg):
    """Режим git: отправляет серверный план (со статусами) обратно в репозиторий."""
    load_env()
    if (os.environ.get("PLAN_SYNC") or "").strip().lower() != "git":
        return
    d = abspath("state/plan-repo")
    if not os.path.isdir(os.path.join(d, ".git")):
        return
    src = Plan(cfg).path
    dst = os.path.join(d, "content-plan.xlsx")
    shutil.copyfile(src, dst)
    run = lambda *a: subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, timeout=120)  # noqa: E731
    run("add", "content-plan.xlsx")
    if run("diff", "--cached", "--quiet").returncode == 0:
        return
    run("-c", "user.name=seo-agent", "-c", "user.email=seo-agent@localhost", "commit", "-m", "Статусы публикаций от агента")
    r = run("push", "--quiet")
    if r.returncode != 0:  # кто-то успел внести правки: подтянем и сольём, затем повторим
        pull(cfg, quiet=True)
        shutil.copyfile(src, dst)
        run("add", "content-plan.xlsx")
        run("-c", "user.name=seo-agent", "-c", "user.email=seo-agent@localhost", "commit", "-m", "Статусы публикаций от агента (после слияния)")
        run("push", "--quiet")
    st = _state()
    st["incoming_sha"] = _sha(dst)
    _save_state(st)


# ---------- команды для вашего компьютера ----------
def _ssh_args(a):
    host = a.host or os.environ.get("SYNC_SSH_HOST", "")
    user = a.user or os.environ.get("SYNC_SSH_USER", "")
    key = a.key or os.environ.get("SYNC_SSH_KEY", "")
    rdir = a.dir or os.environ.get("SYNC_SSH_DIR", "~/seo-agent-ru")
    if not (host and user):
        sys.exit("Нужен сервер: укажите --host и --user (или SYNC_SSH_HOST, SYNC_SSH_USER в .env).")
    opts = ["-o", "StrictHostKeyChecking=accept-new"]
    if key:
        opts += ["-i", os.path.expanduser(key), "-o", "IdentitiesOnly=yes"]
    return host, user, rdir, opts


def push_local(a):
    host, user, rdir, opts = _ssh_args(a)
    if not os.path.exists(a.file):
        sys.exit(f"Файл не найден: {a.file}")
    subprocess.run(["ssh", *opts, f"{user}@{host}", f"mkdir -p {rdir}/plan-inbox"], check=True)
    subprocess.run(["scp", *opts, a.file, f"{user}@{host}:{rdir}/{INBOX}"], check=True)
    print("План отправлен на сервер. Агент подтянет его перед следующим запуском и перед каждой публикацией.")


def fetch_local(a):
    host, user, rdir, opts = _ssh_args(a)
    out = a.out or "content-plan (с сервера).xlsx"
    subprocess.run(["scp", *opts, f"{user}@{host}:{rdir}/content-plan.xlsx", out], check=True)
    print(f"Серверный план со статусами сохранён: {out}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("pull")
    sub.add_parser("push-status")
    for name in ("push-local", "fetch-local"):
        p = sub.add_parser(name)
        if name == "push-local":
            p.add_argument("file")
        else:
            p.add_argument("--out")
        p.add_argument("--host")
        p.add_argument("--user")
        p.add_argument("--key")
        p.add_argument("--dir")
    a = ap.parse_args()
    if a.cmd == "pull":
        cfg = load_config()
        rep = pull(cfg)
        if rep is None:
            print("Изменений в плане нет.")
        return 0
    if a.cmd == "push-status":
        push_status(load_config())
        return 0
    if a.cmd == "push-local":
        push_local(a)
        return 0
    if a.cmd == "fetch-local":
        fetch_local(a)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
