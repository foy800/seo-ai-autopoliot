#!/usr/bin/env python3
"""Заливает скилл на сервер по SSH, используя доступы из .env (SSH_*).

  python3 scripts/deploy.py --dry-run   # показать, что будет сделано, ничего не отправляя
  python3 scripts/deploy.py             # залить и запустить install.sh на сервере
  python3 scripts/deploy.py --cron      # плюс включить ежедневный запуск

Нужны установленные ssh и tar (в Windows 10/11 и Linux/macOS есть из коробки).
Надёжнее вход по ключу (SSH_KEY_PATH). Вход по паролю (SSH_PASS) работает только
если на этом компьютере есть sshpass. Пароль в командную строку не попадает.
На сервер уходят и рабочие файлы (.env, config.json, очередь), поэтому канал SSH
должен быть вашим. Каталоги .git, out, __pycache__ не загружаются.
"""
import argparse
import os
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, load_env  # noqa: E402

SKIP = (".git", "out", "__pycache__", "state/log.jsonl", "state/cron.log")


def need(name):
    v = os.environ.get(name, "").strip()
    if not v:
        sys.exit(f"В .env не задано {name}. Заполните блок «Доступ к серверу (SSH)».")
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cron", action="store_true")
    a = ap.parse_args()

    load_env()
    host, user = need("SSH_HOST"), need("SSH_USER")
    port = os.environ.get("SSH_PORT", "22").strip() or "22"
    key = os.environ.get("SSH_KEY_PATH", "").strip()
    pw = os.environ.get("SSH_PASS", "").strip()
    remote_dir = os.environ.get("SSH_REMOTE_DIR", "").strip() or "~/seo-agent-ru"
    if not key and not pw:
        sys.exit("Задайте SSH_KEY_PATH (рекомендуется) или SSH_PASS в .env.")

    ssh = ["ssh", "-p", port, "-o", "StrictHostKeyChecking=accept-new"]
    if key:
        ssh += ["-i", os.path.expanduser(key), "-o", "IdentitiesOnly=yes"]
    else:
        ssh += ["-o", "PubkeyAuthentication=no"]
    target = f"{user}@{host}"

    tar_cmd = ["tar", "czf", "-", "-C", ROOT] + [f"--exclude={x}" for x in SKIP] + ["."]
    q = shlex.quote
    rdir = remote_dir if remote_dir.startswith("~") else q(remote_dir)
    install = "bash install.sh" + (" --cron" if a.cron else "")
    remote_cmd = (f"mkdir -p {rdir} && tar xzf - -C {rdir} && cd {rdir} && "
                  f"{{ chmod 600 .env 2>/dev/null || true; }} && {install}")

    env = dict(os.environ)
    prefix = []
    if not key:
        env["SSHPASS"] = pw  # sshpass читает пароль из переменной, не из аргументов
        prefix = ["sshpass", "-e"]
        if not _has("sshpass"):
            sys.exit("Для входа по паролю нужен sshpass. Установите его или задайте SSH_KEY_PATH.")

    print(f"Сервер: {target}:{port}\nПапка: {remote_dir}\nВход: {'по ключу' if key else 'по паролю'}")
    print("Команда на сервере:", remote_cmd)
    if a.dry_run:
        print("Пробный режим: ничего не отправлено.")
        return 0

    tar = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE)
    r = subprocess.run(prefix + ssh + [target, remote_cmd], stdin=tar.stdout, env=env)
    tar.stdout.close()
    tar.wait()
    if r.returncode != 0 or tar.returncode != 0:
        print("Загрузка не удалась. Проверьте SSH_HOST, SSH_USER, порт и ключ.", file=sys.stderr)
        return 1
    print("Готово. На сервере: cd", remote_dir, "&& python3 scripts/openai_client.py check")
    return 0


def _has(cmd):
    from shutil import which
    return which(cmd) is not None


if __name__ == "__main__":
    sys.exit(main())
