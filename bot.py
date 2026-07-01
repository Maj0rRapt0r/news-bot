"""
Профессиональный бот новостей для трейдинга.

Источники: Google News, Yahoo Finance, CNBC, MarketWatch, SEC EDGAR.
Фильтрация: Claude AI оценивает важность каждой новости (1–10), присылает только ≥7.
Сектора: управляются Telegram-командами через commands.py.
"""

import os
import json
import time
import calendar
from urllib.parse import quote

import anthropic
import feedparser
import requests

# ---------------------------------------------------------------------------
# Секреты
# ---------------------------------------------------------------------------
def _clean(v: str) -> str:
    return v.strip().strip('"').strip("'")

TELEGRAM_TOKEN   = _clean(os.environ["TELEGRAM_BOT_TOKEN"])
CHAT_ID          = _clean(os.environ["TELEGRAM_CHAT_ID"])
ANTHROPIC_KEY    = _clean(os.environ["ANTHROPIC_API_KEY"])

if not TELEGRAM_TOKEN or ":" not in TELEGRAM_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN неверного формата (ожидается 123456:AAA...)")
if not CHAT_ID:
    raise SystemExit("TELEGRAM_CHAT_ID пустой")
if not ANTHROPIC_KEY or not ANTHROPIC_KEY.startswith("sk-"):
    raise SystemExit("ANTHROPIC_API_KEY неверного формата (ожидается sk-ant-...)")

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------
CONFIG_FILE        = "config.json"
STATE_FILE         = "seen_links.json"
MAX_STATE_ITEMS    = 6000
MAX_AGE_HOURS      = 4        # игнорировать новости старше N часов
IMPORTANCE_MIN     = 7        # Claude ставит 1–10; присылаем только ≥ этого числа
BATCH_SIZE         = 20       # новостей за один вызов Claude

# ---------------------------------------------------------------------------
# Все доступные секторы
# ---------------------------------------------------------------------------
ALL_SECTORS: dict = {
    "ai": {
        "label": "🤖 ИИ / AI",
        "google": [
            "artificial intelligence stock OR AI chip",
            "AI startup funding OR AI earnings OR AI regulation",
        ],
        "yahoo_tickers": ["NVDA", "MSFT", "GOOGL", "META", "AMD"],
        "cnbc_topic": "technology",
    },
    "tech": {
        "label": "💻 Технологии",
        "google": [
            "technology stock earnings OR tech IPO",
            "tech company acquisition OR antitrust",
        ],
        "yahoo_tickers": ["AAPL", "AMZN", "CRM", "ORCL", "INTC"],
        "cnbc_topic": "technology",
    },
    "oil": {
        "label": "🛢 Нефть",
        "google": [
            "crude oil price stock OR oil company earnings",
            "OPEC production OR oil sanctions OR oil supply",
        ],
        "yahoo_tickers": ["XOM", "CVX", "COP", "BP", "SLB"],
        "cnbc_topic": "energy",
    },
    "gas": {
        "label": "🔥 Газ",
        "google": [
            "natural gas price stock OR LNG earnings",
            "gas pipeline deal OR gas company acquisition",
        ],
        "yahoo_tickers": ["LNG", "KMI", "ET", "EQT"],
        "cnbc_topic": "energy",
    },
    "energy": {
        "label": "⚡ Электроэнергетика",
        "google": [
            "utility stock earnings OR power grid investment",
            "solar wind renewable energy stock OR energy regulation",
        ],
        "yahoo_tickers": ["NEE", "DUK", "SO", "AEP", "PCG"],
        "cnbc_topic": "energy",
    },
    "finance": {
        "label": "🏦 Финансы / Банки",
        "google": [
            "bank earnings OR Fed interest rate decision",
            "financial regulation OR banking crisis OR bank acquisition",
        ],
        "yahoo_tickers": ["JPM", "BAC", "GS", "MS", "WFC", "C"],
        "cnbc_topic": "finance",
    },
    "investments": {
        "label": "💰 Крупные инвестиции",
        "google": [
            "billion dollar investment startup OR venture funding round",
            "acquires company OR major stake purchase OR private equity deal",
        ],
        "yahoo_tickers": ["BRK-B", "BX", "KKR", "APO"],
        "cnbc_topic": "business",
    },
}

# CNBC RSS: темы → URL
CNBC_FEEDS = {
    "technology": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910",
    "energy":     "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19836768",
    "finance":    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",
    "business":   "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",
}

# ---------------------------------------------------------------------------
# Получение новостей из источников
# ---------------------------------------------------------------------------
def _parse_feed(url: str, limit: int = 10) -> list:
    """Безопасный парсинг RSS/Atom — не роняет всё при ошибке одного источника."""
    try:
        feed = feedparser.parse(url)
        return feed.entries[:limit]
    except Exception as e:
        print(f"  Ошибка парсинга {url}: {e}")
        return []

def fetch_google(query: str) -> list:
    url = (
        "https://news.google.com/rss/search"
        f"?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
    )
    return _parse_feed(url, 10)

def fetch_yahoo(ticker: str) -> list:
    url = (
        "https://feeds.finance.yahoo.com/rss/2.0/headline"
        f"?s={ticker}&region=US&lang=en-US"
    )
    return _parse_feed(url, 5)

def fetch_cnbc(topic: str) -> list:
    url = CNBC_FEEDS.get(topic, "")
    if not url:
        return []
    return _parse_feed(url, 8)

def fetch_marketwatch() -> list:
    return _parse_feed(
        "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines", 10
    )

def fetch_sec_edgar() -> list:
    """8-K и другие важные отчёты компаний из SEC EDGAR."""
    return _parse_feed(
        "https://www.sec.gov/cgi-bin/browse-edgar"
        "?action=getcurrent&type=8-K&dateb=&owner=include"
        "&count=20&search_text=&output=atom",
        15,
    )

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
def is_recent(entry) -> bool:
    parsed = getattr(entry, "published_parsed", None)
    if parsed is None:
        return True
    age_hours = (time.time() - calendar.timegm(parsed)) / 3600
    return age_hours <= MAX_AGE_HOURS

def entry_source(entry) -> str:
    try:
        return entry.source.title
    except AttributeError:
        return ""

def load_seen() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_seen(seen: set) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen)[-MAX_STATE_ITEMS:], f)

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"active_sectors": list(ALL_SECTORS.keys())}

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
            print(f"  Telegram error: {data}")
            return False
        return True
    except Exception as e:
        print(f"  Telegram exception: {e}")
        return False

# ---------------------------------------------------------------------------
# Фильтрация через Claude AI
# ---------------------------------------------------------------------------
def filter_with_claude(items: list) -> list:
    """
    Отправляет батч заголовков в Claude, получает оценки 1–10.
    Возвращает только те, у которых score >= IMPORTANCE_MIN.
    """
    if not items:
        return []

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    headlines = "\n".join(
        f"{i + 1}. [{it['sector_label']}] {it['title']} ({it['source']})"
        for i, it in enumerate(items)
    )

    prompt = f"""You are a senior stock market analyst. Rate each news headline by its importance to active stock traders and investors.

Score 1–10:
10: Extremely market-moving (earnings shock, major M&A, Fed rate decision, bankruptcy, regulatory ban)
8–9: Very important (IPO, CEO resignation, major lawsuit outcome, large earnings beat/miss, sector-wide regulation)
6–7: Moderately important (analyst upgrade/downgrade, product launch, minor acquisition, macro data)
1–5: Low importance (routine updates, opinion pieces, minor news)

Headlines:
{headlines}

Reply ONLY with a valid JSON array, no other text, no markdown:
[{{"id":1,"score":8}},{{"id":2,"score":3}}]"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        scores = {s["id"]: s["score"] for s in json.loads(raw)}
        result = [
            it for i, it in enumerate(items)
            if scores.get(i + 1, 0) >= IMPORTANCE_MIN
        ]
        print(f"  Claude: {len(items)} → {len(result)} важных (порог {IMPORTANCE_MIN})")
        return result
    except Exception as e:
        print(f"  Claude ошибка: {e} — возвращаю все {len(items)} новостей без фильтрации")
        return items  # fallback: лучше лишние, чем потерять важное

# ---------------------------------------------------------------------------
# Сбор новостей из всех активных секторов
# ---------------------------------------------------------------------------
def collect_news(active_sectors: list, seen: set) -> tuple[list, set]:
    new_seen = set(seen)
    raw: list = []

    def add(entry, sector_label: str, source_name: str):
        link = getattr(entry, "link", "") or ""
        title = getattr(entry, "title", "") or ""
        if not link or not title:
            return
        if link in seen or link in new_seen:
            return
        if not is_recent(entry):
            return
        new_seen.add(link)
        raw.append({
            "link": link,
            "title": title,
            "source": source_name or entry_source(entry) or "—",
            "sector_label": sector_label,
        })

    for sector_key in active_sectors:
        sector = ALL_SECTORS.get(sector_key)
        if not sector:
            continue
        label = sector["label"]
        print(f"  Сектор: {label}")

        for query in sector.get("google", []):
            for e in fetch_google(query):
                add(e, label, entry_source(e) or "Google News")

        for ticker in sector.get("yahoo_tickers", []):
            for e in fetch_yahoo(ticker):
                add(e, label, f"Yahoo Finance · {ticker}")

        topic = sector.get("cnbc_topic")
        if topic:
            for e in fetch_cnbc(topic):
                add(e, label, "CNBC")

    # Глобальные источники (независимо от секторов)
    print("  MarketWatch…")
    for e in fetch_marketwatch():
        add(e, "📈 MarketWatch", "MarketWatch")

    print("  SEC EDGAR…")
    for e in fetch_sec_edgar():
        add(e, "📋 SEC Filing", "SEC EDGAR")

    # Дедупликация по первым 70 символам заголовка
    seen_titles: set = set()
    deduped: list = []
    for it in raw:
        key = it["title"][:70].lower()
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(it)

    return deduped, new_seen

# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------
def main():
    first_run = not os.path.exists(STATE_FILE)
    config = load_config()
    active = config.get("active_sectors", list(ALL_SECTORS.keys()))
    seen = load_seen()

    if first_run:
        # Первый запуск: просто сохраняем текущее состояние, чтобы не засыпать сообщениями
        _, new_seen = collect_news(active, seen)
        save_seen(new_seen)
        labels = ", ".join(
            ALL_SECTORS[s]["label"] for s in active if s in ALL_SECTORS
        )
        ok = send_telegram(
            "🤖 <b>Профессиональный бот новостей запущен!</b>\n\n"
            f"<b>Активные секторы:</b>\n{labels}\n\n"
            "<b>Команды:</b>\n"
            "/sectors — все доступные секторы\n"
            "/add &lt;сектор&gt; — включить сектор\n"
            "/remove &lt;сектор&gt; — отключить сектор\n"
            "/status — текущие настройки\n"
            "/help — помощь\n\n"
            "Первые важные новости придут в течение ~10 минут."
        )
        print("Первый запуск завершён.")
        if not ok:
            raise SystemExit(
                "Не удалось отправить в Telegram. Проверьте TELEGRAM_BOT_TOKEN "
                "и TELEGRAM_CHAT_ID в Settings → Secrets."
            )
        return

    print(f"Активные секторы: {active}")
    raw, new_seen = collect_news(active, seen)
    save_seen(new_seen)

    if not raw:
        print("Нет новых новостей.")
        return

    print(f"Собрано {len(raw)} уникальных новостей. Фильтрую через Claude AI…")

    important: list = []
    for i in range(0, len(raw), BATCH_SIZE):
        batch = raw[i : i + BATCH_SIZE]
        important.extend(filter_with_claude(batch))
        if i + BATCH_SIZE < len(raw):
            time.sleep(1)

    print(f"Итого важных: {len(important)}. Отправляю в Telegram…")

    for item in important:
        msg = (
            f"📌 <b>{item['sector_label']}</b>\n"
            f"{item['title']}\n"
            f"<i>{item['source']}</i>\n"
            f"{item['link']}"
        )
        send_telegram(msg)
        time.sleep(1.5)

    print("Готово.")


if __name__ == "__main__":
    main()
