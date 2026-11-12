from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

VALID_CATEGORIES = {"campsites", "other", "all"}
MAX_GPX_BYTES = 20 * 1024 * 1024


class TelegramError(RuntimeError):
    pass


@dataclass
class PendingUpload:
    file_id: str
    filename: str
    category: str


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def parse_options(text: str, default_category: str = "campsites") -> tuple[date | None, str]:
    travel_date = None
    category = default_category
    for token in text.replace(",", " ").split():
        normalized = token.strip().casefold()
        if normalized in VALID_CATEGORIES:
            category = normalized
            continue
        try:
            travel_date = date.fromisoformat(normalized)
        except ValueError:
            pass
    return travel_date, category


def parse_allowed_chat_ids(raw: str) -> set[int]:
    result: set[int] = set()
    for value in raw.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            result.add(int(value))
        except ValueError as exc:
            raise ValueError(f"Invalid TELEGRAM_ALLOWED_CHAT_IDS value: {value!r}") from exc
    return result


def build_planner_command(
    script: Path, gpx: Path, output: Path, travel_date: date, category: str,
    use_overpass: bool,
) -> list[str]:
    command = [
        sys.executable, str(script), str(gpx), "--date", travel_date.isoformat(),
        "--type", category, "--output", str(output),
    ]
    if not use_overpass:
        command.append("--no-overpass")
    return command


class TelegramApi:
    def __init__(self, token: str):
        self._api_base = f"https://api.telegram.org/bot{token}"
        self._file_base = f"https://api.telegram.org/file/bot{token}"

    def call(self, method: str, payload: dict[str, Any] | None = None, timeout: int = 45) -> Any:
        request = Request(
            f"{self._api_base}/{method}", data=json.dumps(payload or {}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "camplaner-telegram/0.1"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.load(response)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise TelegramError(f"Telegram {method} request failed: {type(exc).__name__}") from exc
        if not result.get("ok"):
            raise TelegramError(f"Telegram {method} failed: {result.get('description', 'unknown error')}")
        return result.get("result")

    def send_message(self, chat_id: int, text: str) -> None:
        self.call("sendMessage", {"chat_id": chat_id, "text": text})

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": 30, "limit": 20, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return self.call("getUpdates", payload, timeout=40)

    def download_file(self, file_id: str) -> bytes:
        metadata = self.call("getFile", {"file_id": file_id})
        file_path = metadata.get("file_path")
        file_size = metadata.get("file_size", 0)
        if not file_path:
            raise TelegramError("Telegram did not return a download path")
        if file_size and file_size > MAX_GPX_BYTES:
            raise TelegramError("The GPX exceeds Telegram's 20 MB bot download limit")
        request = Request(f"{self._file_base}/{file_path}", headers={"User-Agent": "camplaner-telegram/0.1"})
        try:
            with urlopen(request, timeout=60) as response:
                content = response.read(MAX_GPX_BYTES + 1)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise TelegramError(f"Telegram file download failed: {type(exc).__name__}") from exc
        if len(content) > MAX_GPX_BYTES:
            raise TelegramError("The GPX exceeds Telegram's 20 MB bot download limit")
        return content

    def send_document(self, chat_id: int, filename: str, content: bytes, caption: str) -> None:
        boundary = f"camplaner-{secrets.token_hex(12)}"
        body = b"".join([
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode(),
            (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; "
                f"filename=\"{filename}\"\r\nContent-Type: text/markdown\r\n\r\n"
            ).encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ])
        request = Request(
            f"{self._api_base}/sendDocument", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "camplaner-telegram/0.1"},
        )
        try:
            with urlopen(request, timeout=60) as response:
                result = json.load(response)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise TelegramError(f"Telegram sendDocument failed: {type(exc).__name__}") from exc
        if not result.get("ok"):
            raise TelegramError(f"Telegram sendDocument failed: {result.get('description', 'unknown error')}")


class CamplanerBot:
    def __init__(self, api: TelegramApi, allowed_chat_ids: set[int], tools_dir: Path, use_overpass: bool = False):
        self.api = api
        self.allowed_chat_ids = allowed_chat_ids
        self.tools_dir = tools_dir
        self.use_overpass = use_overpass
        self.pending: dict[int, PendingUpload] = {}
        self.categories: dict[int, str] = {}

    def _help(self) -> str:
        return (
            "Upload a .gpx route and I will return a Markdown accommodation list.\n\n"
            "Put the date and optional category in the file caption, for example:\n"
            "2026-11-13 campsites\n\n"
            "If you omit the date, I will ask for it. Categories: campsites, other, all. "
            "The default is campsites.\n\nCommands: /type campsites|other|all, /cancel, /id"
        )

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        if not isinstance(chat_id, int):
            return
        text = (message.get("text") or "").strip()
        first_word = text.split(maxsplit=1)[0].split("@", 1)[0].casefold() if text else ""
        if first_word == "/id":
            self.api.send_message(chat_id, f"This chat ID is {chat_id}")
            return
        if chat_id not in self.allowed_chat_ids:
            self.api.send_message(chat_id, f"This bot is private. Add {chat_id} to TELEGRAM_ALLOWED_CHAT_IDS and restart it.")
            return
        if text.startswith("/"):
            self._handle_command(chat_id, text)
            return
        document = message.get("document")
        if isinstance(document, dict):
            self._handle_document(chat_id, document, message.get("caption") or "")
            return
        if chat_id in self.pending:
            travel_date, category = parse_options(text, self.pending[chat_id].category)
            if travel_date is None:
                self.api.send_message(chat_id, "Please send a date as YYYY-MM-DD, for example 2026-11-13.")
                return
            upload = self.pending.pop(chat_id)
            self._process(chat_id, upload, travel_date, category)
            return
        self.api.send_message(chat_id, self._help())

    def _handle_command(self, chat_id: int, text: str) -> None:
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].casefold()
        if command in {"/start", "/help"}:
            self.api.send_message(chat_id, self._help())
        elif command == "/cancel":
            self.pending.pop(chat_id, None)
            self.api.send_message(chat_id, "Cancelled.")
        elif command == "/type":
            category = argument.strip().casefold()
            if category not in VALID_CATEGORIES:
                self.api.send_message(chat_id, "Use /type campsites, /type other, or /type all.")
                return
            self.categories[chat_id] = category
            self.api.send_message(chat_id, f"Default accommodation type set to {category}.")
        else:
            self.api.send_message(chat_id, self._help())

    def _handle_document(self, chat_id: int, document: dict[str, Any], caption: str) -> None:
        filename = str(document.get("file_name") or "route.gpx")
        if Path(filename).suffix.casefold() != ".gpx":
            self.api.send_message(chat_id, "Please upload a file ending in .gpx.")
            return
        file_size = document.get("file_size", 0)
        if isinstance(file_size, int) and file_size > MAX_GPX_BYTES:
            self.api.send_message(chat_id, "That file is larger than Telegram's 20 MB bot download limit.")
            return
        file_id = document.get("file_id")
        if not isinstance(file_id, str):
            self.api.send_message(chat_id, "Telegram did not provide a usable file ID.")
            return
        travel_date, category = parse_options(caption, self.categories.get(chat_id, "campsites"))
        upload = PendingUpload(file_id, filename, category)
        if travel_date is None:
            self.pending[chat_id] = upload
            self.api.send_message(chat_id, f"Route received. Send the travel date as YYYY-MM-DD. Type: {category}.")
            return
        self._process(chat_id, upload, travel_date, category)

    def _process(self, chat_id: int, upload: PendingUpload, travel_date: date, category: str) -> None:
        self.api.send_message(chat_id, "Route received. Searching accommodation…")
        try:
            content = self.api.download_file(upload.file_id)
            with tempfile.TemporaryDirectory(prefix="camplaner-telegram-") as directory:
                temp_dir = Path(directory)
                safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(upload.filename).stem).strip(".-") or "route"
                gpx_path = temp_dir / f"{safe_stem}.gpx"
                output_path = temp_dir / f"{safe_stem}-{travel_date.isoformat()}.md"
                gpx_path.write_bytes(content)
                command = build_planner_command(
                    self.tools_dir / "camplan.py", gpx_path, output_path,
                    travel_date, category, self.use_overpass,
                )
                result = subprocess.run(
                    command, cwd=self.tools_dir, capture_output=True, text=True,
                    timeout=240, check=False,
                )
                if result.returncode not in {0, 1} or not output_path.is_file():
                    detail = (result.stderr or result.stdout or "unknown planner error").strip()[-1200:]
                    raise RuntimeError(detail)
                caption = f"{category.title()} near {safe_stem} for {travel_date.isoformat()}"
                if result.returncode == 1:
                    caption += " (partial result; see source warnings)"
                self.api.send_document(chat_id, output_path.name, output_path.read_bytes(), caption)
        except subprocess.TimeoutExpired:
            self.api.send_message(chat_id, "Planning timed out after four minutes. Please try again later.")
        except (OSError, RuntimeError, TelegramError) as exc:
            self.api.send_message(chat_id, f"Could not create the report: {exc}")


def main() -> int:
    tools_dir = Path(__file__).resolve().parents[2]
    load_dotenv(tools_dir / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("error: TELEGRAM_BOT_TOKEN is required in .env", file=sys.stderr)
        return 2
    try:
        allowed = parse_allowed_chat_ids(os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", ""))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not allowed:
        print("warning: no chats authorized; send /id, add it to TELEGRAM_ALLOWED_CHAT_IDS, and restart", file=sys.stderr)
    use_overpass = os.environ.get("TELEGRAM_USE_OVERPASS", "false").casefold() in {"1", "true", "yes"}
    api = TelegramApi(token)
    bot = CamplanerBot(api, allowed, tools_dir, use_overpass)
    print("Camplaner Telegram bot is running. Press Ctrl+C to stop.", file=sys.stderr)
    offset = None
    try:
        while True:
            try:
                updates = api.get_updates(offset)
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    bot.handle_update(update)
            except TelegramError as exc:
                print(f"warning: {exc}; retrying in 3 seconds", file=sys.stderr)
                time.sleep(3)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 0

