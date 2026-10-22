"""Homework list in Telegram, plus a reminder one day before the deadline.

The same process polls Telegram and checks deadlines at 16:00 Moscow time (13:00 UTC).
Secrets come from TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.

Local check without sending:
  python main.py --dry-run
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOMEWORK_PATH = ROOT / "homework.json"
LOCAL_ENV = Path(r"C:\ForMe\telegram\.env")
MOSCOW = timezone(timedelta(hours=3))
LOCK = threading.Lock()
HELP = (
    "Команды:\n"
    "/homework — весь список\n"
    "/add Предмет | Тема | ГГГГ-ММ-ДД | номер\n"
    "Номер можно не писать."
)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip().lstrip("\ufeff"), value.strip().strip('"').strip("'"))


def load_homework() -> list[dict]:
    with LOCK:
        data = json.loads(HOMEWORK_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("homework.json must be a list")
    return data


def save_homework(items: list[dict]) -> None:
    text = json.dumps(items, ensure_ascii=False, indent=2) + "\n"
    with LOCK:
        HOMEWORK_PATH.write_text(text, encoding="utf-8")


def due_tomorrow(items: list[dict], today: date) -> list[dict]:
    due = []
    for item in items:
        deadline = datetime.strptime(item["deadline"], "%Y-%m-%d").date()
        if (deadline - today).days == 1:
            due.append(item)
    return due


def reminder_text(item: dict) -> str:
    lines = [
        "Завтра срок домашки.",
        f"Предмет: {item['subject']}",
        f"Тема: {item.get('topic') or '—'}",
        f"Срок: {item['deadline']}",
    ]
    number = item.get("number")
    if number not in (None, ""):
        lines.append(f"Номер: {number}")
    return "\n".join(lines)


def list_text(items: list[dict]) -> str:
    if not items:
        return "Список домашки пуст."
    lines = ["Домашка:"]
    for index, item in enumerate(items, start=1):
        number = item.get("number")
        extra = f", №{number}" if number not in (None, "") else ""
        topic = item.get("topic") or "—"
        lines.append(
            f"{index}. {item['subject']} — {topic} — {item['deadline']}{extra}"
        )
    return "\n".join(lines)


def parse_add(text: str) -> tuple[dict | None, str | None]:
    body = text.split(maxsplit=1)
    if len(body) < 2:
        return None, "Формат: /add Предмет | Тема | ГГГГ-ММ-ДД | номер"
    parts = [part.strip() for part in body[1].split("|")]
    if len(parts) not in (3, 4) or not all(parts[:3]):
        return None, "Нужны предмет, тема и дата через |. Номер по желанию."
    try:
        datetime.strptime(parts[2], "%Y-%m-%d")
    except ValueError:
        return None, "Дата должна быть в виде ГГГГ-ММ-ДД."
    item: dict = {"subject": parts[0], "topic": parts[1], "deadline": parts[2]}
    if len(parts) == 4 and parts[3]:
        if not parts[3].isdigit():
            return None, "Номер должен быть числом."
        item["number"] = int(parts[3])
    return item, None


def api(token: str, method: str, payload: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None if payload is None else urllib.parse.urlencode(payload).encode()
    request = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(request, timeout=40) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed")
    return body


def send(token: str, chat_id: str, text: str, dry_run: bool) -> None:
    if dry_run:
        print("--- dry-run, not sent ---")
        print(text)
        print("---")
        return
    api(token, "sendMessage", {"chat_id": chat_id, "text": text})


def allowed(message: dict, chat_id: str) -> bool:
    chat = message.get("chat") or {}
    return str(chat.get("id", "")) == str(chat_id)


def handle(token: str, chat_id: str, message: dict, dry_run: bool) -> None:
    if not allowed(message, chat_id):
        return
    text = (message.get("text") or "").strip()
    if text.startswith("/homework"):
        send(token, chat_id, list_text(load_homework()), dry_run)
        return
    if text.startswith("/add"):
        item, error = parse_add(text)
        if error:
            send(token, chat_id, error, dry_run)
            return
        items = load_homework()
        items.append(item)
        if not dry_run:
            save_homework(items)
        send(token, chat_id, "Добавлено.\n" + list_text([item]), dry_run)
        return
    if text.startswith("/start") or text.startswith("/help"):
        send(token, chat_id, HELP, dry_run)


def check_deadlines(token: str, chat_id: str, today: date, dry_run: bool) -> int:
    due = due_tomorrow(load_homework(), today)
    for item in due:
        send(token, chat_id, reminder_text(item), dry_run)
    return len(due)


def reminder_loop(token: str, chat_id: str) -> None:
    while True:
        now = datetime.now(MOSCOW)
        target = now.replace(hour=16, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        time.sleep((target - now).total_seconds())
        try:
            count = check_deadlines(token, chat_id, datetime.now(MOSCOW).date(), False)
            print(f"Morning check sent {count}")
        except Exception as error:
            print(f"Morning check failed: {error}")


def poll(token: str, chat_id: str) -> None:
    offset = None
    while True:
        payload: dict[str, str | int] = {"timeout": 30}
        if offset is not None:
            payload["offset"] = offset
        try:
            updates = api(token, "getUpdates", payload).get("result", [])
        except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
            print(f"Poll error: {error}")
            time.sleep(5)
            continue
        for update in updates:
            offset = int(update["update_id"]) + 1
            message = update.get("message") or {}
            try:
                handle(token, chat_id, message, False)
            except Exception as error:
                print(f"Handler error: {error}")


def dry_run(token: str, chat_id: str) -> int:
    items = load_homework()
    print(list_text(items))
    sample = "/add Введение в специальность | Кибербезопасность и хакинг | 2026-10-30 | 5173"
    item, error = parse_add(sample)
    print("add parse:", "ok" if item and not error else error)
    today = date.today()
    print(f"due tomorrow from {today}: {len(due_tomorrow(items, today))}")
    if items:
        preview_day = datetime.strptime(items[0]["deadline"], "%Y-%m-%d").date() - timedelta(days=1)
        print(f"reminder preview for {preview_day}:")
        send(token, chat_id, reminder_text(items[0]), True)
    print("Dry-run finished. No Telegram request was made.")
    return 0


def main() -> int:
    load_env(LOCAL_ENV)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    dry = "--dry-run" in sys.argv
    if dry:
        return dry_run(token, chat_id)
    if not token or token.startswith("your_bot_token") or not chat_id:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return 1
    threading.Thread(target=reminder_loop, args=(token, chat_id), daemon=True).start()
    poll(token, chat_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
