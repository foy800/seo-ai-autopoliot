#!/usr/bin/env bash
# Установка seo-agent-ru на сервер: рабочие файлы из шаблонов, проверка, по желанию cron.
#   bash install.sh          # только подготовить файлы и проверить
#   bash install.sh --cron   # плюс ежедневный запуск в 07:00
set -e
cd "$(dirname "$0")"

command -v python3 >/dev/null 2>&1 || { echo "Нужен python3 (3.9 или новее)."; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "Нужен Python 3.9 или новее."; exit 1; }

mkdir -p state context out

copy_if_missing() {
  if [ ! -e "$2" ]; then cp "$1" "$2"; echo "создан $2"; else echo "уже есть $2, не трогаю"; fi
}
copy_if_missing templates/config.example.json config.json
copy_if_missing templates/.env.example .env
copy_if_missing templates/queue.example.csv state/queue.csv
copy_if_missing templates/pages.example.csv context/pages.csv
copy_if_missing templates/facts.example.md context/facts.md
chmod 600 .env

python3 -c 'import openpyxl' 2>/dev/null || {
  echo "ставлю openpyxl (нужен для Excel)..."
  python3 -m pip install --user openpyxl >/dev/null 2>&1 || python3 -m pip install openpyxl >/dev/null 2>&1 \
    || echo "Не удалось поставить openpyxl автоматически. Выполните: pip install openpyxl"
}

python3 - <<'PY' && echo "скрипты в порядке" || { echo "ошибка в скриптах"; exit 1; }
import ast, glob
for f in glob.glob("scripts/*.py"):
    ast.parse(open(f, encoding="utf-8").read(), f)
PY

if [ "$1" = "--cron" ]; then
  line="0 7 * * * cd $PWD && python3 scripts/pipeline.py >> state/cron.log 2>&1 # seo-agent-ru"
  ( crontab -l 2>/dev/null | grep -v "# seo-agent-ru" ; echo "$line" ) | crontab -
  echo "cron настроен: каждый день в 07:00"
fi

cat <<EOF

Дальше:
  1. Откройте .env (nano .env): впишите OPENAI_API_KEY, FTP_HOST, FTP_USER, FTP_PASS
     и параметры сайта SEO_SITE_URL, SEO_SITE_NAME, SEO_FTP_DIR.
     Ключи и пароли никому не отправляйте.
  2. Заполните context/pages.csv (услуги пометьте cta=yes, добавьте описание), context/facts.md и запросы (в content-plan.xlsx или state/queue.csv).
  3. (Необязательно) config.json: те же параметры, если не хотите держать их в .env.
  4. Проверьте ключ:      python3 scripts/openai_client.py check
  5. Создать таблицу:     python3 scripts/pipeline.py   (появится content-plan.xlsx с заголовками)
     Откройте её, поставьте «да» в колонке «Одобрено» у нужных статей и запустите снова.
  6. Автозапуск:          bash install.sh --cron
EOF
