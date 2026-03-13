"""
openpod.skills -- Hot-reloading skill framework for OpenPod agents.

Skills are plain Python files. Type-annotated functions become tools.
Auto-discovered from a directory, hot-reloaded on change, cron-schedulable.

Usage:
    from openpod.skills import SkillLoader, skill

    loader = SkillLoader("~/.openpod/skills")
    loader.load_all()

    # Call a skill
    result = loader.call("get_weather", {"city": "London"})

    # Get OpenAI-compatible tool schemas
    schemas = loader.tool_schemas()

    # Serve as MCP server on stdio
    loader.serve_mcp()

    # Background: hot-reload + cron
    loader.watch()  # starts background thread

Skill file example (~/.openpod/skills/weather.py):

    from openpod.skills import skill

    @skill(cron="0 8 * * *", description="Morning weather briefing")
    def morning_briefing(city: str = "local") -> str:
        \"\"\"Get the morning weather briefing for a city.\"\"\"
        return f"Weather in {city}: partly cloudy, 72F"
"""

import ast
import importlib.util
import inspect
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ── Decorator ─────────────────────────────────────────────────────────────

_SKILL_REGISTRY: Dict[str, dict] = {}  # name -> {fn, cron, description}


def skill(
    cron: str = "",
    description: str = "",
    tags: List[str] = None,
):
    """
    Decorator to register a function as a skill.

    @skill(cron="0 8 * * *", description="Run every morning at 8am")
    def morning_briefing() -> str:
        return "Good morning!"
    """
    def decorator(fn: Callable) -> Callable:
        name = fn.__name__
        _SKILL_REGISTRY[name] = {
            "fn": fn,
            "cron": cron,
            "description": description or (fn.__doc__ or "").strip().split("\n")[0],
            "tags": tags or [],
        }
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)
        wrapper._skill_meta = _SKILL_REGISTRY[name]
        return wrapper
    return decorator


# ── Schema generation ─────────────────────────────────────────────────────

_TYPE_MAP = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}


def _fn_to_schema(fn: Callable) -> dict:
    """Convert a typed Python function to an OpenAI-compatible tool schema."""
    sig = inspect.signature(fn)
    doc = inspect.getdoc(fn) or ""
    first_line = doc.split("\n")[0].strip()

    props = {}
    required = []

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue

        ann = param.annotation
        type_str = "string"
        if ann != inspect.Parameter.empty:
            type_name = getattr(ann, "__name__", str(ann))
            type_str = _TYPE_MAP.get(type_name, "string")

        props[name] = {"type": type_str, "description": f"{name} parameter"}

        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {
        "name": fn.__name__,
        "description": first_line or fn.__name__,
        "inputSchema": {
            "type": "object",
            "properties": props,
            "required": required,
        },
    }


# ── Cron parsing (lightweight, no deps) ───────────────────────────────────

def _cron_matches(cron_expr: str, now: "datetime" = None) -> bool:
    """Check if a 5-field cron expression matches the current minute."""
    if not cron_expr:
        return False
    from datetime import datetime as _dt
    now = now or _dt.now()
    try:
        fields = cron_expr.strip().split()
        if len(fields) != 5:
            return False
        minute, hour, dom, month, dow = fields

        def match(field, value, lo, hi):
            if field == "*":
                return True
            if field.startswith("*/"):
                step = int(field[2:])
                return value % step == 0
            if "," in field:
                return value in [int(x) for x in field.split(",")]
            if "-" in field:
                a, b = field.split("-")
                return int(a) <= value <= int(b)
            return int(field) == value

        return (
            match(minute, now.minute, 0, 59) and
            match(hour, now.hour, 0, 23) and
            match(dom, now.day, 1, 31) and
            match(month, now.month, 1, 12) and
            match(dow, now.weekday(), 0, 6)
        )
    except Exception:
        return False


# ── Loader ────────────────────────────────────────────────────────────────

class SkillLoader:
    """
    Discovers, loads, hot-reloads, and executes skills from a directory.

    Each .py file in skills_dir is a skill module.
    Typed functions become callable tools.
    Functions decorated with @skill get cron + description metadata.
    """

    def __init__(self, skills_dir: str = None):
        self.skills_dir = Path(skills_dir).expanduser() if skills_dir else Path.home() / ".openpod" / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._skills: Dict[str, dict] = {}   # name -> {fn, schema, meta, mtime, module}
        self._mtimes: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._watch_thread: Optional[threading.Thread] = None

    # ── Load ──────────────────────────────────────────────────

    def load_all(self) -> int:
        """Load all .py files in skills_dir. Returns count loaded."""
        count = 0
        for path in sorted(self.skills_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            if self._load_file(path):
                count += 1
        return count

    def _load_file(self, path: Path) -> bool:
        """Load a single skill file. Returns True on success."""
        try:
            mtime = path.stat().st_mtime
            spec = importlib.util.spec_from_file_location(f"_skill_{path.stem}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            with self._lock:
                # Remove old skills from this file
                self._skills = {k: v for k, v in self._skills.items() if v.get("source") != str(path)}

                # Register all public typed functions
                for fn_name, fn in inspect.getmembers(mod, inspect.isfunction):
                    if fn_name.startswith("_"):
                        continue
                    schema = _fn_to_schema(fn)
                    meta = getattr(fn, "_skill_meta", {})
                    self._skills[fn_name] = {
                        "fn": fn,
                        "schema": schema,
                        "cron": meta.get("cron", ""),
                        "description": meta.get("description", schema["description"]),
                        "tags": meta.get("tags", []),
                        "source": str(path),
                        "module": path.stem,
                    }
                self._mtimes[str(path)] = mtime
            return True
        except Exception as e:
            print(f"[skills] Failed to load {path.name}: {e}", file=sys.stderr)
            return False

    def reload_changed(self) -> int:
        """Reload any files that have changed on disk. Returns count reloaded."""
        count = 0
        for path in self.skills_dir.glob("*.py"):
            if path.name.startswith("_"):
                continue
            try:
                mtime = path.stat().st_mtime
                if mtime != self._mtimes.get(str(path)):
                    if self._load_file(path):
                        count += 1
            except Exception:
                pass
        return count

    # ── Execute ───────────────────────────────────────────────

    def call(self, name: str, args: dict = None) -> Any:
        """Call a skill by name with a dict of arguments."""
        with self._lock:
            skill_entry = self._skills.get(name)
        if not skill_entry:
            raise KeyError(f"Skill '{name}' not found. Available: {self.names()}")
        return skill_entry["fn"](**(args or {}))

    def names(self) -> List[str]:
        """List all loaded skill names."""
        with self._lock:
            return sorted(self._skills.keys())

    def tool_schemas(self) -> List[dict]:
        """Return OpenAI-compatible tool schemas for all loaded skills."""
        with self._lock:
            return [v["schema"] for v in self._skills.values()]

    def get_schema(self, name: str) -> Optional[dict]:
        with self._lock:
            entry = self._skills.get(name)
        return entry["schema"] if entry else None

    # ── Cron ──────────────────────────────────────────────────

    def run_due_crons(self) -> List[str]:
        """Run any skills whose cron expression matches now. Returns names executed."""
        executed = []
        with self._lock:
            entries = [(k, v) for k, v in self._skills.items() if v.get("cron")]
        for name, entry in entries:
            if _cron_matches(entry["cron"]):
                try:
                    entry["fn"]()
                    executed.append(name)
                except Exception as e:
                    print(f"[skills] Cron error in {name}: {e}", file=sys.stderr)
        return executed

    def watch(self, poll_interval: float = 5.0) -> None:
        """
        Start background thread: hot-reload changed files + run cron every minute.
        Non-blocking. Call stop_watch() to halt.
        """
        self._watching = True

        def _loop():
            last_cron = 0.0
            while self._watching:
                self.reload_changed()
                if time.time() - last_cron >= 60:
                    self.run_due_crons()
                    last_cron = time.time()
                time.sleep(poll_interval)

        self._watch_thread = threading.Thread(target=_loop, daemon=True)
        self._watch_thread.start()

    def stop_watch(self) -> None:
        self._watching = False

    # ── MCP bridge ────────────────────────────────────────────

    def serve_mcp(self) -> None:
        """
        Serve loaded skills as an MCP server over stdio (JSON-RPC 2.0).
        Blocks forever. Each skill becomes an MCP tool.

        Add to .mcp.json:
            {"command": "python", "args": ["-m", "openpod.skills", "serve", "/path/to/skills"]}
        """
        import sys

        def _handle(request: dict) -> Optional[dict]:
            method = request.get("method", "")
            req_id = request.get("id")

            if method == "initialize":
                return {"jsonrpc": "2.0", "id": req_id, "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "openpod-skills", "version": "0.2.0"},
                }}

            elif method == "notifications/initialized":
                return None

            elif method == "tools/list":
                tools = []
                with self._lock:
                    for entry in self._skills.values():
                        tools.append({
                            "name": entry["schema"]["name"],
                            "description": entry["description"],
                            "inputSchema": entry["schema"]["inputSchema"],
                        })
                return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}

            elif method == "tools/call":
                params = request.get("params", {})
                name = params.get("name", "")
                args = params.get("arguments", {})
                try:
                    result = self.call(name, args)
                    text = json.dumps(result, default=str) if not isinstance(result, str) else result
                    return {"jsonrpc": "2.0", "id": req_id, "result": {
                        "content": [{"type": "text", "text": text}]
                    }}
                except Exception as e:
                    return {"jsonrpc": "2.0", "id": req_id, "result": {
                        "content": [{"type": "text", "text": str(e)}],
                        "isError": True,
                    }}

            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}}

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                resp = _handle(req)
                if resp is not None:
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()
            except json.JSONDecodeError:
                sys.stdout.write(json.dumps({
                    "jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "Parse error"}
                }) + "\n")
                sys.stdout.flush()
