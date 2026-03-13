"""
openpod.agent -- Agent loop. Ties Pod + Memory + Skills + Channels together.

The UFO-class agent: sees, thinks (K-routed), remembers, acts, communicates.

Usage:
    from openpod.agent import Agent
    from openpod.channels import telegram, ntfy

    agent = Agent("my-agent", skills_dir="~/.openpod/skills")

    @agent.on_message
    def handle(msg):
        context = agent.memory.context_block(msg.text)
        response = agent.think(msg.text, context=context)
        agent.memory.remember(f"Q: {msg.text[:100]}")
        return response

    agent.connect(ntfy(topic="my-agent"))
    agent.connect(telegram(token="BOT_TOKEN"))
    agent.run()  # blocks

Standalone (no channels — just think):
    agent = Agent("solo")
    print(agent.think("What is the capital of France?"))
"""

import sys
import threading
from typing import Callable, List, Optional

from openpod.core import Pod
from openpod.memory import Memory
from openpod.skills import SkillLoader
from openpod.channels import BaseChannel, ChannelMessage


class Agent:
    """
    OpenPod Agent — multi-channel, memory-backed, K-routed AI agent.

    Components:
        .pod        -- Pod message bus (JSONL, inter-agent comms)
        .memory     -- Persistent memory (markdown files, BM25/cosine search)
        .skills     -- Hot-reloading skill directory
        .channels   -- List of connected channel adapters

    K-routing (optional, baked in if klaw-router installed):
        agent.think(query) -- routes via K-104, cheapest capable model
        Falls back to raw API call or echo if klaw-router not installed.
    """

    def __init__(
        self,
        name: str,
        bus_dir: str = None,
        memory_dir: str = None,
        skills_dir: str = None,
        model: str = "auto",
        byok: str = None,
    ):
        self.name = name
        self.model = model
        self.byok = byok

        self.pod = Pod(name, bus_dir=bus_dir, roles=[name])
        self.memory = Memory(memory_dir)
        self.skills = SkillLoader(skills_dir)
        self.skills.load_all()

        self.channels: List[BaseChannel] = []
        self._handler: Optional[Callable] = None
        self._router = None  # lazy-loaded klaw router

    # ── K-routing (UFO class differentiator) ──────────────────

    def _get_router(self):
        if self._router is None:
            try:
                from klaw import KlawRouter
                self._router = KlawRouter()
            except ImportError:
                self._router = False  # mark unavailable
        return self._router if self._router is not False else None

    def think(
        self,
        query: str,
        context: str = "",
        system_prompt: str = "",
        byok: str = None,
    ) -> str:
        """
        Route a query through K-104 to the cheapest capable model.

        If klaw-router is installed: K-104 cascade (template → local → cheap → cloud).
        Falls back to direct Ollama or raw API if klaw-router unavailable.

        Args:
            query: The question/instruction
            context: Optional context block (e.g. from memory.context_block())
            system_prompt: Optional system prompt override
            byok: Bring-your-own API key (overrides agent default)

        Returns:
            Response string
        """
        full_query = f"{context}\n\n{query}".strip() if context else query
        key = byok or self.byok

        router = self._get_router()
        if router:
            result = router.route(
                full_query,
                system_prompt=system_prompt,
                byok=key,
            )
            return result.get("response", "")

        # Fallback: Ollama direct
        try:
            from openpod.models import LocalModels
            lm = LocalModels()
            if lm.is_available():
                return lm.ask(full_query, system=system_prompt)
        except Exception:
            pass

        # Last resort: echo
        return f"[no model] {query}"

    # ── Skills integration ────────────────────────────────────

    def call_skill(self, name: str, args: dict = None) -> str:
        """Call a loaded skill by name."""
        try:
            result = self.skills.call(name, args or {})
            return str(result) if result is not None else ""
        except Exception as e:
            return f"[skill error] {e}"

    def available_skills(self) -> List[str]:
        return self.skills.names()

    # ── Channel management ────────────────────────────────────

    def connect(self, channel: BaseChannel) -> "Agent":
        """Add a channel adapter. Returns self for chaining."""
        self.channels.append(channel)
        return self

    def on_message(self, fn: Callable) -> Callable:
        """
        Decorator to register a message handler.

        Handler receives a ChannelMessage and returns a string (or None).

            @agent.on_message
            def handle(msg):
                return agent.think(msg.text)
        """
        self._handler = fn
        return fn

    def _default_handler(self, msg: ChannelMessage) -> str:
        return self.think(msg.text)

    def _dispatch(self, msg: ChannelMessage) -> Optional[str]:
        handler = self._handler or self._default_handler
        try:
            return handler(msg)
        except Exception as e:
            return f"[agent error] {e}"

    # ── Run loop ──────────────────────────────────────────────

    def run(self, watch_skills: bool = True) -> None:
        """
        Start listening on all connected channels. Blocks forever.

        Each channel runs in its own daemon thread.
        Skills are hot-reloaded + cron-checked in the background.
        """
        if not self.channels:
            print(f"[{self.name}] No channels connected. Use agent.connect(channel).", file=sys.stderr)
            return

        if watch_skills:
            self.skills.watch()

        threads = []
        for ch in self.channels:
            print(f"[{self.name}] Listening on {ch.name}...")
            t = ch.listen_async(self._dispatch)
            threads.append(t)

        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            print(f"\n[{self.name}] Stopped.")
            self.skills.stop_watch()

    def send(self, text: str, title: str = "", priority: str = "default") -> None:
        """Broadcast a message on all connected channels."""
        for ch in self.channels:
            ch.send(text, title=title, priority=priority)

    # ── Convenience ───────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "name": self.name,
            "memory": self.memory.stats(),
            "skills": self.available_skills(),
            "channels": [ch.name for ch in self.channels],
            "router": "klaw" if self._get_router() else "fallback",
        }

    def __repr__(self) -> str:
        return (
            f"Agent(name={self.name!r}, "
            f"channels={[ch.name for ch in self.channels]}, "
            f"skills={self.available_skills()})"
        )
