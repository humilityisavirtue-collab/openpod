"""
openpod.memory -- Session-persistent memory for AI agents.

Markdown-based. Zero deps for core (BM25 keyword search).
Optional numpy for cosine similarity.

Files:
    ~/.openpod/MEMORY.md              long-term memory (manual + compacted)
    ~/.openpod/memory/YYYY-MM-DD.md   daily session summaries

Usage:
    from openpod.memory import Memory

    mem = Memory()
    mem.remember("Kit prefers dark themes")
    results = mem.search("theme")          # ["Kit prefers dark themes"]
    summary = mem.compact(messages)        # summarize + write daily .md
    today = mem.today()                    # read today's summary
    block = mem.context_block(query)       # prompt injection block
"""

import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional


class Memory:
    """
    Persistent agent memory backed by markdown files.

    Long-term store: MEMORY.md (append-only, one bullet per memory)
    Daily summaries: memory/YYYY-MM-DD.md (compact output)

    Retrieval: BM25-lite keyword search + optional cosine (numpy).
    """

    def __init__(self, base_dir: str = None):
        self.base_dir = Path(base_dir) if base_dir else Path.home() / ".openpod"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = self.base_dir / "MEMORY.md"
        self.daily_dir = self.base_dir / "memory"
        self.daily_dir.mkdir(parents=True, exist_ok=True)

    # ── Write ─────────────────────────────────────────────────

    def remember(self, text: str, tags: List[str] = None) -> None:
        """Append a memory to the long-term store."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        with open(self.memory_file, "a", encoding="utf-8") as f:
            f.write(f"- [{ts}]{tag_str} {text.strip()}\n")

    def forget(self, pattern: str) -> int:
        """Remove memories matching a regex pattern. Returns count removed."""
        if not self.memory_file.exists():
            return 0
        lines = self.memory_file.read_text(encoding="utf-8").split("\n")
        before = len([l for l in lines if l.strip()])
        kept = [l for l in lines if not re.search(pattern, l, re.IGNORECASE)]
        self.memory_file.write_text("\n".join(kept), encoding="utf-8")
        after = len([l for l in kept if l.strip()])
        return before - after

    # ── Read ──────────────────────────────────────────────────

    def recall(self, n: int = 20) -> List[str]:
        """Return the last n memories."""
        if not self.memory_file.exists():
            return []
        lines = [l.strip() for l in self.memory_file.read_text(encoding="utf-8").split("\n") if l.strip()]
        return lines[-n:]

    def search(self, query: str, top_k: int = 5) -> List[str]:
        """Hybrid search: cosine (numpy) if available, BM25-lite fallback."""
        if not self.memory_file.exists():
            return []
        lines = [l.strip() for l in self.memory_file.read_text(encoding="utf-8").split("\n") if l.strip()]
        if not lines:
            return []
        try:
            import numpy as np
            results = self._cosine_search(query, lines, top_k, np)
            if results:
                return results
        except ImportError:
            pass
        return self._bm25_search(query, lines, top_k)

    def today(self) -> str:
        """Read today's daily summary."""
        daily = self.daily_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        return daily.read_text(encoding="utf-8") if daily.exists() else ""

    def context_block(self, query: str = "", max_chars: int = 2000) -> str:
        """
        Return a formatted context block for prompt injection.
        Includes today's summary + query-relevant memories.
        """
        parts = []
        today = self.today()
        if today:
            parts.append(f"## Today\n{today[:600]}")
        relevant = self.search(query, top_k=5) if query else self.recall(10)
        if relevant:
            parts.append("## Memory\n" + "\n".join(relevant))
        return "\n\n".join(parts)[:max_chars]

    # ── Compact ───────────────────────────────────────────────

    def compact(
        self,
        messages: List[dict],
        summarizer: Optional[Callable] = None,
    ) -> str:
        """
        Summarize a conversation turn and write to today's daily file.

        Args:
            messages: list of {"role": str, "content": str}
            summarizer: callable(messages) -> str — use Ollama/KLAW here.
                        Defaults to extractive (last 5 user messages).

        Returns:
            The summary string written.
        """
        if summarizer:
            summary = summarizer(messages)
        else:
            user_msgs = [m["content"] for m in messages if m.get("role") == "user"][-5:]
            summary = "\n".join(f"- {m[:200]}" for m in user_msgs)

        daily = self.daily_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        ts = datetime.now().strftime("%H:%M")
        with open(daily, "a", encoding="utf-8") as f:
            f.write(f"\n## {ts}\n{summary}\n")
        return summary

    # ── Stats ─────────────────────────────────────────────────

    def stats(self) -> dict:
        total = 0
        if self.memory_file.exists():
            total = sum(1 for l in self.memory_file.read_text(encoding="utf-8").split("\n") if l.strip())
        daily_files = list(self.daily_dir.glob("*.md"))
        return {
            "total_memories": total,
            "daily_files": len(daily_files),
            "base_dir": str(self.base_dir),
        }

    # ── Internal ──────────────────────────────────────────────

    def _bm25_search(self, query: str, lines: List[str], top_k: int) -> List[str]:
        query_words = set(re.findall(r'\w+', query.lower()))
        scores = []
        for line in lines:
            count = Counter(re.findall(r'\w+', line.lower()))
            score = sum(count.get(w, 0) for w in query_words)
            if score > 0:
                scores.append((score, line))
        scores.sort(reverse=True)
        return [line for _, line in scores[:top_k]]

    def _cosine_search(self, query: str, lines: List[str], top_k: int, np) -> List[str]:
        all_text = [query] + lines
        all_words = sorted(set(w for t in all_text for w in re.findall(r'\w+', t.lower())))
        if not all_words:
            return []
        idx = {w: i for i, w in enumerate(all_words)}

        def vec(text):
            v = np.zeros(len(all_words))
            for w in re.findall(r'\w+', text.lower()):
                if w in idx:
                    v[idx[w]] += 1.0
            return v

        q = vec(query)
        qn = np.linalg.norm(q)
        if qn == 0:
            return []
        q /= qn

        scores = []
        for line in lines:
            v = vec(line)
            n = np.linalg.norm(v)
            if n == 0:
                continue
            scores.append((float(np.dot(q, v / n)), line))
        scores.sort(reverse=True)
        return [line for score, line in scores[:top_k] if score > 0.05]
