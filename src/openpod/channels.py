"""
openpod.channels -- Channel adapters for OpenPod agents.

Adapters handle inbound/outbound comms across platforms.
All adapters share the same handler interface.

Usage:
    from openpod.channels import ntfy, telegram

    agent = Agent("my-agent")
    agent.connect(ntfy(topic="my-topic"))         # push notifications
    agent.connect(telegram(token="BOT_TOKEN"))    # Telegram bot

    @agent.on_message
    async def handle(msg):
        return f"Echo: {msg.text}"

    agent.run()

Channels available:
    ntfy      -- ntfy.sh push (outbound + inbound commands). Zero deps.
    telegram  -- Telegram bot. Requires: pip install python-telegram-bot
    discord   -- Discord bot. Requires: pip install discord.py
    webhook   -- Generic HTTP webhook (inbound + outbound). Requires: fastapi, uvicorn

Each channel implements:
    channel.send(text, title="", priority="default") -> bool
    channel.listen(handler)  -- blocking loop
"""

import json
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import urlencode


# ── Message envelope shared across channels ────────────────────────────────

@dataclass
class ChannelMessage:
    text: str
    sender: str = ""
    channel: str = ""
    raw: dict = None

    def __post_init__(self):
        if self.raw is None:
            self.raw = {}


# ── Base adapter ──────────────────────────────────────────────────────────

class BaseChannel:
    name: str = "base"

    def send(self, text: str, title: str = "", priority: str = "default") -> bool:
        raise NotImplementedError

    def listen(self, handler: Callable[[ChannelMessage], Optional[str]]) -> None:
        """Blocking listen loop. handler(msg) -> optional reply string."""
        raise NotImplementedError

    def listen_async(self, handler: Callable, daemon: bool = True) -> threading.Thread:
        """Start listen() in a background thread."""
        t = threading.Thread(target=self.listen, args=(handler,), daemon=daemon)
        t.start()
        return t


# ── ntfy adapter ──────────────────────────────────────────────────────────

class NtfyChannel(BaseChannel):
    """
    ntfy.sh push notifications. Zero deps.

    Outbound: POST to ntfy.sh/{topic}
    Inbound: SSE stream from ntfy.sh/{command_topic}/json

    Send:
        ch = ntfy(topic="my-pod")
        ch.send("Build complete", title="K-Cell", priority="high")

    Listen (phone → agent):
        ch = ntfy(topic="my-pod", command_topic="my-pod-commands")
        ch.listen(lambda msg: f"Got: {msg.text}")
    """
    name = "ntfy"

    PRIORITY_MAP = {
        "min": "min", "low": "low", "default": "default",
        "high": "high", "urgent": "urgent", "max": "urgent",
        1: "min", 2: "low", 3: "default", 4: "high", 5: "urgent",
    }

    def __init__(
        self,
        topic: str,
        command_topic: str = "",
        server: str = "https://ntfy.sh",
        token: str = "",
    ):
        self.topic = topic
        self.command_topic = command_topic or f"{topic}-commands"
        self.server = server.rstrip("/")
        self.token = token

    def send(self, text: str, title: str = "", priority: str = "default") -> bool:
        """Push a notification to the topic."""
        url = f"{self.server}/{self.topic}"
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "X-Priority": self.PRIORITY_MAP.get(priority, "default"),
        }
        if title:
            headers["X-Title"] = title
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            req = Request(url, data=text.encode("utf-8"), headers=headers, method="POST")
            with urlopen(req, timeout=10):
                return True
        except Exception:
            return False

    def listen(self, handler: Callable[[ChannelMessage], Optional[str]]) -> None:
        """
        SSE listen loop on command_topic.
        Calls handler(msg) for each message received.
        If handler returns a string, sends it back to the main topic.
        """
        url = f"{self.server}/{self.command_topic}/json"
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        while True:
            try:
                req = Request(url, headers=headers)
                with urlopen(req, timeout=90) as resp:
                    for raw_line in resp:
                        line = raw_line.decode("utf-8").strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            if data.get("event") == "message":
                                text = data.get("message", "")
                                if text:
                                    msg = ChannelMessage(
                                        text=text,
                                        sender=data.get("title", "phone"),
                                        channel="ntfy",
                                        raw=data,
                                    )
                                    reply = handler(msg)
                                    if reply:
                                        self.send(str(reply))
                        except (json.JSONDecodeError, KeyError):
                            continue
            except URLError:
                time.sleep(5)
            except Exception:
                time.sleep(5)


def ntfy(
    topic: str,
    command_topic: str = "",
    server: str = "https://ntfy.sh",
    token: str = "",
) -> NtfyChannel:
    """Create an ntfy channel adapter."""
    return NtfyChannel(topic=topic, command_topic=command_topic, server=server, token=token)


# ── Telegram adapter ──────────────────────────────────────────────────────

class TelegramChannel(BaseChannel):
    """
    Telegram bot adapter.

    Requires: pip install python-telegram-bot

    Usage:
        ch = telegram(token="BOT_TOKEN")
        ch.send("Hello!")
        ch.listen(lambda msg: f"Echo: {msg.text}")
    """
    name = "telegram"

    def __init__(self, token: str, chat_id: str = ""):
        self.token = token
        self.chat_id = chat_id  # optional default target
        self._offset = 0

    def _api(self, method: str, data: dict = None) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        body = json.dumps(data or {}).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def send(self, text: str, title: str = "", priority: str = "default", chat_id: str = "") -> bool:
        """Send a message to a chat. Falls back to self.chat_id."""
        target = chat_id or self.chat_id
        if not target:
            print("[telegram] No chat_id set — message not sent", file=sys.stderr)
            return False
        full_text = f"**{title}**\n{text}" if title else text
        result = self._api("sendMessage", {"chat_id": target, "text": full_text})
        return result.get("ok", False)

    def listen(self, handler: Callable[[ChannelMessage], Optional[str]]) -> None:
        """Long-poll getUpdates loop."""
        while True:
            try:
                result = self._api("getUpdates", {
                    "offset": self._offset,
                    "timeout": 30,
                    "allowed_updates": ["message"],
                })
                if not result.get("ok"):
                    time.sleep(5)
                    continue
                for update in result.get("result", []):
                    self._offset = update["update_id"] + 1
                    msg_data = update.get("message", {})
                    text = msg_data.get("text", "")
                    if not text:
                        continue
                    chat_id = str(msg_data.get("chat", {}).get("id", ""))
                    sender = msg_data.get("from", {}).get("username", "")
                    if not self.chat_id:
                        self.chat_id = chat_id  # auto-set on first message
                    msg = ChannelMessage(
                        text=text,
                        sender=sender,
                        channel="telegram",
                        raw=msg_data,
                    )
                    try:
                        reply = handler(msg)
                        if reply:
                            self.send(str(reply), chat_id=chat_id)
                    except Exception as e:
                        self.send(f"[error] {e}", chat_id=chat_id)
            except Exception:
                time.sleep(5)


def telegram(token: str, chat_id: str = "") -> TelegramChannel:
    """Create a Telegram channel adapter."""
    return TelegramChannel(token=token, chat_id=chat_id)


# ── Discord adapter ────────────────────────────────────────────────────────

class DiscordChannel(BaseChannel):
    """
    Discord bot adapter via webhook (outbound) + bot token (inbound).

    Outbound only (webhook):
        ch = discord(webhook_url="https://discord.com/api/webhooks/...")
        ch.send("Build complete!")

    Full duplex (bot token):
        ch = discord(token="BOT_TOKEN", channel_id="12345")
        ch.listen(lambda msg: f"Echo: {msg.text}")

    Requires discord.py for full duplex: pip install discord.py
    """
    name = "discord"

    def __init__(self, token: str = "", webhook_url: str = "", channel_id: str = ""):
        self.token = token
        self.webhook_url = webhook_url
        self.channel_id = channel_id

    def send(self, text: str, title: str = "", priority: str = "default") -> bool:
        """Send via webhook if available, else REST API."""
        content = f"**{title}**\n{text}" if title else text
        if self.webhook_url:
            try:
                body = json.dumps({"content": content}).encode("utf-8")
                req = Request(
                    self.webhook_url, data=body,
                    headers={"Content-Type": "application/json"}, method="POST"
                )
                with urlopen(req, timeout=10):
                    return True
            except Exception:
                return False
        if self.token and self.channel_id:
            url = f"https://discord.com/api/v10/channels/{self.channel_id}/messages"
            body = json.dumps({"content": content}).encode("utf-8")
            req = Request(
                url, data=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bot {self.token}"},
                method="POST",
            )
            try:
                with urlopen(req, timeout=10):
                    return True
            except Exception:
                return False
        return False

    def listen(self, handler: Callable[[ChannelMessage], Optional[str]]) -> None:
        """Requires discord.py: pip install discord.py"""
        try:
            import discord
        except ImportError:
            print("[discord] Install discord.py: pip install discord.py", file=sys.stderr)
            return

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_message(message):
            if message.author == client.user:
                return
            if self.channel_id and str(message.channel.id) != self.channel_id:
                return
            msg = ChannelMessage(
                text=message.content,
                sender=str(message.author),
                channel="discord",
            )
            try:
                reply = handler(msg)
                if reply:
                    await message.channel.send(str(reply))
            except Exception as e:
                await message.channel.send(f"[error] {e}")

        client.run(self.token)


def discord(token: str = "", webhook_url: str = "", channel_id: str = "") -> DiscordChannel:
    """Create a Discord channel adapter."""
    return DiscordChannel(token=token, webhook_url=webhook_url, channel_id=channel_id)
