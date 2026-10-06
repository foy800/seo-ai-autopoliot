#!/usr/bin/env python3
"""Жёсткий запрет: без «да» в колонке «Одобрено» контент-плана статья не пишется и не публикуется.

Это единственный источник одобрения. Просьба в чате, в промпте, в консоли или в другом файле
(«публикуй», «одобряю», «делай всё») одобрением НЕ считается. Отключить проверку нельзя:
переменной SEO_REQUIRE_APPROVAL больше не существует. Скрипт сам никогда не вписывает «да».

Использование:
  python3 scripts/gate.py check "основной ключ"   # код 0: можно, код 1: нельзя
  python3 scripts/gate.py list                      # какие строки плана одобрены

В коде: from gate import require_approved; require_approved(cfg, keyword="...") бросает
ApprovalError, если у строки нет «да» (или строки нет в плане).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import abspath, load_config, slugify  # noqa: E402
from plan import Plan, is_approved  # noqa: E402


class ApprovalError(RuntimeError):
    """Статья не одобрена человеком в колонке «Одобрено»."""


REFUSAL = (
    "ОТКАЗ: у этой статьи нет «да» в колонке «Одобрено» файла {plan}. "
    "Писать и публиковать нельзя, даже если об этом просили в чате или в промпте. "
    "Откройте таблицу, проверьте заголовок и поставьте «да» сами."
)


def _norm(s):
    return " ".join(str(s or "").lower().replace("ё", "е").split())


def _matches(row, keyword=None, slug=None):
    if keyword and _norm(row["keyword"]) == _norm(keyword):
        return True
    if slug:
        s = _norm(slug)
        if s in (_norm(row.get("slug")), _norm(slugify(row["keyword"]))):
            return True
    return False


def require_approved(cfg, keyword=None, slug=None, plan=None):
    """Возвращает строку плана, если человек поставил «да». Иначе ApprovalError."""
    if plan is None:
        path = abspath(cfg.get("plan_path") or "content-plan.xlsx")
        if not os.path.exists(path):
            raise ApprovalError(f"Контент-плана нет ({os.path.basename(path)}): сначала добавьте статью в план "
                                "и попросите человека поставить «да».")
        plan = Plan(cfg)
    rows = [r for r in plan.rows if _matches(r, keyword, slug)]
    if not rows:
        raise ApprovalError("Статьи нет в контент-плане, значит она не одобрена. " +
                            REFUSAL.format(plan=os.path.basename(plan.path)))
    ok = [r for r in rows if is_approved(r["approved"])]
    if not ok:
        raise ApprovalError(REFUSAL.format(plan=os.path.basename(plan.path)))
    return ok[0]


def main():
    cfg = load_config()
    if len(sys.argv) >= 2 and sys.argv[1] == "list":
        plan = Plan(cfg)
        ok = [r for r in plan.rows if is_approved(r["approved"])]
        print(f"Одобрено человеком: {len(ok)} из {len(plan.rows)}")
        for r in ok:
            print(f"  {r['keyword']}  [{r['status'] or 'new'}]")
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        try:
            require_approved(cfg, keyword=" ".join(sys.argv[2:]))
        except ApprovalError as e:
            print(str(e))
            return 1
        print("Одобрено: можно писать.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
