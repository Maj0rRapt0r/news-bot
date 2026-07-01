"""
Обработчик Telegram-команд с inline-кнопками для выбора секторов.
Запускается GitHub Actions каждые 5 минут.
"""

import os
import json
import requests

# ---------------------------------------------------------------------------
# Секреты
# ---------------------------------------------------------------------------
def _clean(v: str) -> str:
    return v.strip().strip('"').strip("'")

TELEGRAM_TOKEN = _clean(os.environ["TELEGRAM_BOT_TOKEN"])
CHAT_ID        = _clean(os.environ["TELEGRAM_CHAT_ID"])

CONFIG_FILE = "config.json"
OFFSET_FILE = "tg_offset.json"

# ---------------------------------------------------------------------------
# Все секторы (должны совпадать с bot.py)
# ---------------------------------------------------------------------------
ALL_SECTORS = {
    "ai":          "🤖 ИИ / AI",
    "tech":        "💻 Технологии",
    "oil":         "🛢 Нефть",
    "gas":         "🔥 Газ",
    "energy":      "⚡ Электроэнергетика",
    "finance":     "🏦 Финансы / Банки",
    "investments": "💰 Крупные инвестиции",
}

# ---------------------------------------------------------------------------
# Config / Offset
# ---------------------------------------------------------------------------
def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"active_sectors": list(ALL_SECTORS.keys())}

def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def load_offset() -> int:
    if os.path.exists(OFFSET_FILE):
        with open(OFFSET_FILE) as f:
            return json.load(f).get("offset", 0)
    return 0

def save_offset(offset: int) -> None:
    with open(OFFSET_FILE, "w") as f:
        json.dump({"offset": offset}, f)

# ---------------------------------------------------------------------------
# Telegram API
# ---------------------------------------------------------------------------
BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def api(method: str, **kwargs) -> dict:
    try:
        r = requests.post(f"{BASE}/{method}", json=kwargs, timeout=15)
        return r.json()
    except Exception as e:
        print(f"  API {method} ошибка: {e}")
        return {}

def get_updates(offset: int) -> list:
    try:
        r = requests.get(
            f"{BASE}/getUpdates",
            params={"offset": offset, "timeout": 5},
            timeout=15,
        )
        return r.json().get("result", [])
    except Exception as e:
        print(f"  getUpdates ошибка: {e}")
        return []

# ---------------------------------------------------------------------------
# Inline-клавиатура с секторами
# ---------------------------------------------------------------------------
def sectors_keyboard(active: list) -> dict:
    """Строит inline-клавиатуру: каждый сектор — кнопка с ✅/❌."""
    buttons = []
    row = []
    for i, (key, label) in enumerate(ALL_SECTORS.items()):
        icon = "✅" if key in active else "❌"
        row.append({
            "text": f"{icon} {label}",
            "callback_data": f"toggle:{key}",
        })
        # по 2 кнопки в ряд
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # кнопка "Готово"
    buttons.append([{"text": "✔ Готово", "callback_data": "done"}])
    return {"inline_keyboard": buttons}

def sectors_text(active: list) -> str:
    count = len(active)
    return (
        f"📊 <b>Выбор секторов</b> (активно: {count})\n\n"
        "Нажми кнопку, чтобы включить или выключить сектор.\n"
        "✅ — получаю новости   ❌ — не получаю"
    )

# ---------------------------------------------------------------------------
# Обработка команд (текстовые сообщения)
# ---------------------------------------------------------------------------
def handle_message(text: str, cfg: dict) -> None:
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lstrip("/").lower().split("@")[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    active = cfg.get("active_sectors", [])

    if cmd in ("start", "help"):
        api("sendMessage",
            chat_id=CHAT_ID,
            text=(
                "👋 <b>Бот новостей для трейдинга</b>\n\n"
                "Я слежу за важными новостями по выбранным секторам "
                "и присылаю только то, что реально может двигать рынок.\n\n"
                "<b>Команды:</b>\n"
                "/sectors — выбрать секторы (с кнопками)\n"
                "/status — текущие активные секторы\n"
                "/help — это сообщение"
            ),
            parse_mode="HTML",
        )

    elif cmd in ("sectors", "меню", "выбор"):
        api("sendMessage",
            chat_id=CHAT_ID,
            text=sectors_text(active),
            parse_mode="HTML",
            reply_markup=sectors_keyboard(active),
        )

    elif cmd == "status":
        if not active:
            api("sendMessage", chat_id=CHAT_ID,
                text="❌ Нет активных секторов.\nОткрой /sectors чтобы включить.")
        else:
            lines = [f"• {ALL_SECTORS.get(s, s)}" for s in active]
            api("sendMessage", chat_id=CHAT_ID,
                text="✅ <b>Активные секторы:</b>\n\n" + "\n".join(lines),
                parse_mode="HTML")

    else:
        api("sendMessage",
            chat_id=CHAT_ID,
            text=f"❓ Неизвестная команда «/{cmd}».\n\nПомощь: /help",
            parse_mode="HTML",
        )

# ---------------------------------------------------------------------------
# Обработка нажатий на inline-кнопки (callback_query)
# ---------------------------------------------------------------------------
def handle_callback(callback: dict, cfg: dict) -> None:
    cq_id      = callback["id"]
    data       = callback.get("data", "")
    message_id = callback["message"]["message_id"]
    active     = cfg.get("active_sectors", [])

    if data == "done":
        # Убираем клавиатуру, показываем итог
        labels = ", ".join(ALL_SECTORS.get(s, s) for s in active) or "нет"
        api("editMessageText",
            chat_id=CHAT_ID,
            message_id=message_id,
            text=f"✔ <b>Сохранено.</b>\n\nАктивные секторы:\n{labels}",
            parse_mode="HTML",
        )
        api("answerCallbackQuery", callback_query_id=cq_id, text="Настройки сохранены")
        return

    if data.startswith("toggle:"):
        key = data.split(":", 1)[1]
        if key not in ALL_SECTORS:
            api("answerCallbackQuery", callback_query_id=cq_id, text="Неизвестный сектор")
            return

        if key in active:
            active.remove(key)
            note = f"❌ Выключен: {ALL_SECTORS[key]}"
        else:
            active.append(key)
            note = f"✅ Включён: {ALL_SECTORS[key]}"

        cfg["active_sectors"] = active
        save_config(cfg)

        # Обновляем то же сообщение с новой клавиатурой
        api("editMessageText",
            chat_id=CHAT_ID,
            message_id=message_id,
            text=sectors_text(active),
            parse_mode="HTML",
            reply_markup=sectors_keyboard(active),
        )
        api("answerCallbackQuery", callback_query_id=cq_id, text=note)

# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------
def main() -> None:
    offset  = load_offset()
    updates = get_updates(offset)

    if not updates:
        print("Нет новых событий.")
        return

    cfg = load_config()

    for upd in updates:
        offset = max(offset, upd["update_id"] + 1)

        # Текстовые команды
        if "message" in upd:
            msg     = upd["message"]
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if chat_id != CHAT_ID:
                continue
            text = msg.get("text", "")
            if text.startswith("/"):
                print(f"  Команда: {text!r}")
                handle_message(text, cfg)

        # Нажатия на inline-кнопки
        elif "callback_query" in upd:
            cb      = upd["callback_query"]
            chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
            if chat_id != CHAT_ID:
                continue
            print(f"  Callback: {cb.get('data')!r}")
            handle_callback(cb, cfg)

    save_offset(offset)
    print(f"Обработано: {len(updates)} обновлений.")


if __name__ == "__main__":
    main()
