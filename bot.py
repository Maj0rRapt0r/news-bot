"""
Бот для мониторинга важных новостей по выбранным секторам рынка
и отправки их в Telegram.

Источник новостей: Google News RSS (бесплатно, без API-ключа).
Запускается по расписанию через GitHub Actions (см. .github/workflows/news_bot.yml).
"""

import os
import json
import time
from urllib.parse import quote

import feedparser
import requests

def _clean_secret(value: str) -> str:
    """Убирает случайные пробелы/кавычки, которые иногда попадают при
    вставке значения в GitHub Secrets."""
    return value.strip().strip('"').strip("'")


TELEGRAM_TOKEN = _clean_secret(os.environ["TELEGRAM_BOT_TOKEN"])
CHAT_ID = _clean_secret(os.environ["TELEGRAM_CHAT_ID"])

STATE_FILE = "seen_links.json"
MAX_STATE_ITEMS = 3000           # сколько ссылок храним в истории
ENTRIES_PER_QUERY = 15           # сколько свежих новостей смотрим за раз на каждый сектор

# ---------------------------------------------------------------------------
# 1. СЕКТОРА И ПОИСКОВЫЕ ЗАПРОСЫ
#    Можно свободно редактировать / добавлять свои запросы.
# ---------------------------------------------------------------------------
SECTOR_QUERIES = {
    "🤖 ИИ": "artificial intelligence stock OR AI chip OR AI startup funding",
    "💻 Технологии": "tech company stock OR technology earnings",
    "🛢 Нефть": "oil price OR oil company stock OR OPEC",
    "🔥 Газ": "natural gas price OR gas company stock",
    "⚡ Электроэнергетика": "energy utility stock OR power grid investment",
    "🏦 Финансы/Банки": "bank earnings OR financial stocks OR rate decision",
    "💰 Крупные инвестиции": "billion investment startup OR acquires startup OR venture funding",
}

# ---------------------------------------------------------------------------
# 2. КЛЮЧЕВЫЕ СЛОВА "ВАЖНОСТИ"
#    Новость отправляется, только если заголовок содержит хотя бы одно слово.
#    Это фильтр от шума (мелкие заметки, аналитика "для галочки" и т.п.)
# ---------------------------------------------------------------------------
IMPORTANT_KEYWORDS = [
    "acquisition", "acquire", "acquires", "merger", "ipo", "earnings",
    "lawsuit", "fda", "bankrupt", "investment", "funding", "raises $",
    "partnership", "surge", "plunge", "soars", "tumbles", "ceo", "resign",
    "breakthrough", "regulation", "antitrust", "recall", "hack", "breach",
    "sanction", "strike", "layoff", "record high", "record low", "billion",
    "stake", "buyback", "dividend", "guidance", "upgrade", "downgrade",
    "fine", "probe", "ban", "deal", "stock jump", "stock drop", "halt",
    "default", "rate cut", "rate hike", "approval", "approved", "rejects",
]


def load_seen() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set) -> None:
    trimmed = list(seen)[-MAX_STATE_ITEMS:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f)


def is_important(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in IMPORTANT_KEYWORDS)


def fetch_news(query: str):
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
    feed = feedparser.parse(url)
    return feed.entries[:ENTRIES_PER_QUERY]


def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        data = resp.json()
        if not data.get("ok"):
            print(f"Telegram API вернул ошибку: {data}")
            return False
        return True
    except requests.RequestException as e:
        print(f"Сетевая ошибка при отправке в Telegram: {e}")
        return False


def get_source_name(entry) -> str:
    try:
        return entry.source.title
    except AttributeError:
        return ""


def main():
    first_run = not os.path.exists(STATE_FILE)
    seen = load_seen()
    new_seen = set(seen)
    sent_count = 0

    for sector, query in SECTOR_QUERIES.items():
        entries = fetch_news(query)
        for entry in entries:
            link = entry.link
            title = entry.title

            if link in seen:
                continue
            new_seen.add(link)

            if first_run:
                # В первый запуск просто запоминаем текущие новости,
                # чтобы не вывалить вам сразу сотню сообщений.
                continue

            if is_important(title):
                source = get_source_name(entry)
                msg = f"📌 <b>{sector}</b>\n{title}\n<i>{source}</i>\n{link}"
                send_telegram(msg)
                sent_count += 1
                time.sleep(1)  # не спамим Telegram API

    save_seen(new_seen)

    if first_run:
        ok = send_telegram(
            "🤖 Бот запущен и настроен.\n"
            "С этого момента я буду присылать важные новости по вашим секторам "
            "каждые ~10 минут."
        )
        print("Первый запуск: история новостей сохранена.")
        if not ok:
            raise SystemExit(
                "Не удалось отправить сообщение в Telegram. Проверьте секреты "
                "TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в настройках репозитория "
                "(Settings → Secrets and variables → Actions) — смотрите подробности "
                "ошибки выше в логах."
            )
    else:
        print(f"Отправлено новостей: {sent_count}")


if __name__ == "__main__":
    main()
