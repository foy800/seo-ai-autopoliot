# seo-agent-ru

Универсальный автономный SEO-агент под Яндекс: 3 статьи в день, ключи в заголовке
и тексте, перелинковка, таблицы, врезки, CTA в конце, обложка без текста через
OpenAI, чистка от ИИ-слопа. Подходит для любой тематики. Основан на
[claude-yandex-seo](https://github.com/rdsolod-ui/claude-yandex-seo) и
[humanizer-ru](https://github.com/ilyautov/humanizer-ru), оба под лицензией MIT.

Нужен только Python 3.9+, внешних пакетов нет.

## Установка

1. Скопируйте папку на сервер, например в `/opt/seo-agent-ru`.
   Для Claude Code положите её в `~/.claude/skills/seo-agent-ru`.
2. Настройки:
   ```bash
   cp templates/config.example.json config.json
   cp templates/.env.example .env && chmod 600 .env
   cp templates/queue.example.csv state/queue.csv
   mkdir -p context
   cp templates/pages.example.csv context/pages.csv
   cp templates/facts.example.md context/facts.md
   ```
3. Впишите **свой ключ OpenAI в `.env`** (строка `OPENAI_API_KEY=`) прямо на
   сервере. В чат и в репозиторий ключ не отправляйте. Модели меняются
   переменными `OPENAI_TEXT_MODEL` и `OPENAI_IMAGE_MODEL`, актуальные названия
   смотрите в документации OpenAI.
4. Заполните `config.json` (адрес сайта, `cta_url`, автор), `context/pages.csv`
   (страницы для перелинковки) и `context/facts.md` (единственный источник
   цен, имён и сроков).
5. Проверьте ключ: `python3 scripts/openai_client.py check`.
6. Пробный прогон без ключа и без сети: `python3 scripts/pipeline.py --mock`.

## Запуск

```bash
python3 scripts/pipeline.py            # до daily_limit статей за сегодня
python3 scripts/pipeline.py --count 1  # одна статья
```

По cron каждый день в 07:00:

```
0 7 * * * cd /opt/seo-agent-ru && /usr/bin/python3 scripts/pipeline.py >> state/cron.log 2>&1
```

Повторный запуск в тот же день лимит не превысит.

## Как это работает

Очередь `state/queue.csv` → генерация → `article_check.py` → до 3 переделок по
списку ошибок → вычитка и корректура (числа не должны меняться, иначе вычитка
отклоняется) → обложка → выдача.

Статусы очереди: `new`, `ready` (готово, хука публикации нет), `published`,
`failed` (причина в колонке `note`).

## Очередь (задание на статью)

Колонки `state/queue.csv`: `keyword` (основной ключ), `secondary` (доп. ключи через
запятую), `lsi` (LSI-слова), `kind` (`info` или `commercial`), `max_chars` (лимит
для коммерческого текста), `topic`, `priority`, `status`.

## Word

```bash
pip install python-docx
python3 scripts/to_docx.py out/2026-09-30-slug.md
```

Собирает .docx с заголовками, жирными врезками, списками, таблицей и обложкой.

## Публикация

Скилл не знает вашу CMS или структуру сайта. В `config.json` укажите `publish_hook`:
команду, которую агент вызовет после успешной проверки. Ей доступны переменные
`ARTICLE_MD`, `ARTICLE_IMAGE`, `ARTICLE_SLUG`, `ARTICLE_TITLE`,
`ARTICLE_DESCRIPTION`, `ARTICLE_IMAGE_ALT`. Пока хука нет, готовые файлы лежат в `out/`.

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
- Для второго прохода слопа можно поставить полный
  [humanizer-ru](https://github.com/ilyautov/humanizer-ru) и запускать его
  `scan.py` по файлам из `out/`.
- Точных частот запросов скилл не собирает, очередь заполняется вручную.
