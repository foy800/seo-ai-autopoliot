# seo-agent-ru

Универсальный автономный SEO-агент под Яндекс. Каждый день пишет 3 статьи по очереди
ключей: ключи в заголовке и тексте, перелинковка, таблицы, врезки, CTA в конце,
обложка без текста через OpenAI, чистка от ИИ-слопа. Публикует статьи на обычный сайт
без CMS по FTP. Подходит для любой тематики.

## Установка на сервер

```bash
git clone https://github.com/foy800/seo-ai-autopoliot.git ~/seo-agent-ru && cd ~/seo-agent-ru && bash install.sh
```

Скрипт создаёт `config.json`, `.env` и рабочие файлы из шаблонов. Нужен только Python 3.9+,
внешних пакетов нет. Чтобы сразу включить ежедневный запуск в 07:00:

```bash
bash install.sh --cron
```

Обновление: `cd ~/seo-agent-ru && git pull`. Ваши `config.json`, `.env`, очередь и
логи остаются на месте, в репозиторий они не попадают.

## Добавить в Claude

Claude Code (macOS, Linux):

```bash
git clone https://github.com/foy800/seo-ai-autopoliot.git ~/.claude/skills/seo-agent-ru
```

Claude Code (Windows, PowerShell):

```powershell
git clone https://github.com/foy800/seo-ai-autopoliot.git "$env:USERPROFILE\.claude\skills\seo-agent-ru"
```

Перезапустите Claude Code и скажите: «запусти SEO-агента» или «напиши SEO-статью».
Скилл подхватится автоматически. Обновление: `git pull` в той же папке.

Для проекта, а не для всех: клонируйте в `.claude/skills/seo-agent-ru` внутри папки проекта.

## Настройка

Все секреты и параметры лежат в одном файле **`.env`** (шаблон `templates/.env.example`,
установщик копирует его сам). Откройте и заполните:

```bash
nano .env
```

- **Секреты:** `OPENAI_API_KEY`, `FTP_HOST`, `FTP_USER`, `FTP_PASS`. В чат и в репозиторий
  их не отправляйте.
- **Сайт:** `SEO_SITE_URL`, `SEO_SITE_NAME`, `SEO_CTA_URL` (страница услуги для CTA),
  `SEO_CTA_CONTACT`, `SEO_FTP_DIR` (папка сайта на сервере, например `/domain.ru/public_html`).
- **Объём и качество:** `SEO_DAILY_LIMIT`, `SEO_MIN_CHARS`, лимиты ссылок и ключей.
  Каждая переменная `SEO_<ПАРАМЕТР>` перекрывает одноимённое значение в `config.json`.
  Пустая строка означает «взять из `config.json` или значение по умолчанию».
- Модели OpenAI меняются переменными `OPENAI_TEXT_MODEL` и `OPENAI_IMAGE_MODEL`,
  актуальные названия смотрите в документации OpenAI.

Остальные файлы:

1. **`context/pages.csv`:** страницы сайта для перелинковки.
2. **`context/facts.md`:** единственный источник цен, имён и сроков.
3. **`state/queue.csv`:** очередь запросов.
4. Проверка ключа: `python3 scripts/openai_client.py check`.
5. Пробный прогон без ключа и без сети: `python3 scripts/pipeline.py --mock`
   (при `SEO_PUBLISH_MODE=ftp` без FTP статья получит статус `ready`, это нормально).

`config.json` можно не править: он нужен, только если вы хотите держать параметры не в `.env`.

## Запуск

```bash
python3 scripts/pipeline.py            # до daily_limit статей за сегодня
python3 scripts/pipeline.py --count 1  # одна статья
```

Повторный запуск в тот же день лимит не превысит.

## Как это работает

Очередь `state/queue.csv` → генерация → `article_check.py` → до 3 переделок по
списку ошибок → вычитка и корректура (числа не должны меняться, иначе вычитка
отклоняется) → обложка → публикация на сайт.

Статусы очереди: `new`, `ready` (готово, но не опубликовано: сбой FTP или нет доступа),
`published`, `failed` (причина в колонке `note`). Статьи со статусом `ready`
публикуются повторно при следующем запуске.

## Очередь (задание на статью)

Колонки `state/queue.csv`: `keyword` (основной ключ), `secondary` (доп. ключи через
запятую), `lsi` (LSI-слова), `kind` (`info` или `commercial`), `max_chars` (лимит
для коммерческого текста), `topic`, `priority`, `status`.

## Публикация на сайт без CMS

При `publish_mode: "ftp"` для каждой статьи создаются:

- страница `/blog/<slug>/index.html` с разметкой Article, canonical и Open Graph;
- обложка `/blog/<slug>/cover.png`;
- список статей `/blog/index.html` (отключается `update_blog_index: false`);
- карта `/blog/sitemap-blog.xml`.

Файлы заливаются по FTP в `ftp_dir`. Режим `"local"` только собирает файлы в `out/site`
без загрузки. После первой публикации добавьте `sitemap-blog.xml` в Яндекс.Вебмастер и в
`robots.txt`. Оформление страниц берётся из `templates/page.template.html`: замените шапку и
подвал на разметку вашего сайта, метки `{{...}}` не трогайте. Ссылки на блог в меню сайта
добавьте вручную один раз.

Если публикацию нужно делать другим способом, укажите в конфиге `publish_hook`:
команду, которой доступны `ARTICLE_MD`, `ARTICLE_IMAGE`, `ARTICLE_SLUG`,
`ARTICLE_TITLE`, `ARTICLE_DESCRIPTION`, `ARTICLE_IMAGE_ALT`. Она работает, когда
`publish_mode` не задан.

## Проверка вручную

```bash
python3 scripts/article_check.py out/файл.md --keyword "основной ключ" --secondary "доп1,доп2" --lsi "слово1,слово2"
```

Код возврата 0 — проходит, 1 — есть ошибки (JSON в stdout).

## Ограничения

- Скрипт проверяет форму, а не истинность фактов. Для тем, где ошибка опасна
  (здоровье, деньги, право), проверку человеком организуйте сами.
- Определение вхождений ключей идёт по грубой основе слова, редкие формы могут
  не засчитаться.
- Орфография проверяется по списку типичных ошибок, остальное закрывает корректура
  в вычитке.
- FTP без шифрования передаёт пароль открытым текстом: если сервер поддерживает FTPS,
  поставьте `FTP_TLS=1`.
- Для второго прохода слопа можно поставить полный
  [humanizer-ru](https://github.com/ilyautov/humanizer-ru) и запускать его
  `scan.py` по файлам из `out/`.
- Точных частот запросов скилл не собирает, очередь заполняется вручную.

## Лицензия и источники

MIT. Идеи и правила из [claude-yandex-seo](https://github.com/rdsolod-ui/claude-yandex-seo)
и [humanizer-ru](https://github.com/ilyautov/humanizer-ru), подробности в `NOTICE.md`.
