#!/usr/bin/env python3
"""Минимальный клиент OpenAI без внешних зависимостей.

Ключ берётся только из окружения или .env (OPENAI_API_KEY). В вывод и в
сообщения об ошибках он не попадает.

  python3 openai_client.py check
  python3 openai_client.py image --scene "светлый офис с документами на столе" --out out/cover.png
  python3 openai_client.py chat --system "..." --user "..."
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_env  # noqa: E402

API = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

NO_TEXT_SUFFIX = (
    " Реалистичная фотография в мягком дневном свете, чистая композиция, "
    "светлый нейтральный фон. В кадре нет текста, букв, цифр, логотипов, вывесок, "
    "надписей и водяных знаков. Нет крупных планов лиц и нет сравнений «до и после»."
)


class OpenAIError(RuntimeError):
    pass


def _key():
    load_env()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise OpenAIError(
            "OPENAI_API_KEY не задан. Добавьте строку OPENAI_API_KEY=... в файл .env "
            "рядом со скиллом (сам ключ в чат не отправляйте)."
        )
    return key


def _post(path, payload, timeout=180, retries=3):
    key = _key()
    data = json.dumps(payload).encode("utf-8")
    delay = 3
    last = ""
    for attempt in range(retries):
        req = urllib.request.Request(
            f"{API}{path}", data=data, method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:500].replace(key, "***")
            last = f"HTTP {e.code}: {body}"
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise OpenAIError(last)
        except (urllib.error.URLError, TimeoutError) as e:
            last = f"сеть: {e}".replace(key, "***")
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise OpenAIError(last)
    raise OpenAIError(last)


def chat(system, user, model=None, max_tokens=None):
    load_env()
    model = model or os.environ.get("OPENAI_TEXT_MODEL", "gpt-4.1")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if max_tokens:
        payload["max_completion_tokens"] = max_tokens
    res = _post("/chat/completions", payload)
    try:
        return res["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        raise OpenAIError("неожиданный ответ chat/completions")


def image(scene, out_path, model=None, size=None):
    """Генерирует обложку без текста и сохраняет файл. Возвращает путь."""
    load_env()
    model = model or os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
    size = size or os.environ.get("OPENAI_IMAGE_SIZE", "1536x1024")
    payload = {"model": model, "prompt": scene.strip() + NO_TEXT_SUFFIX, "size": size, "n": 1}
    if model.startswith("dall-e"):
        payload["response_format"] = "b64_json"
    res = _post("/images/generations", payload, timeout=300)
    try:
        item = res["data"][0]
    except (KeyError, IndexError):
        raise OpenAIError("неожиданный ответ images/generations")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    if item.get("b64_json"):
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(item["b64_json"]))
    elif item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=120) as r, open(out_path, "wb") as f:
            f.write(r.read())
    else:
        raise OpenAIError("в ответе нет изображения")
    return out_path


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    p = sub.add_parser("image")
    p.add_argument("--scene", required=True)
    p.add_argument("--out", required=True)
    c = sub.add_parser("chat")
    c.add_argument("--system", default="")
    c.add_argument("--user", required=True)
    a = ap.parse_args()
    try:
        if a.cmd == "check":
            _key()
            print("OPENAI_API_KEY найден (значение не показываю).")
        elif a.cmd == "image":
            print(image(a.scene, a.out))
        else:
            print(chat(a.system, a.user))
    except OpenAIError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
