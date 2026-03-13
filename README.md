# OpenPod

[![PyPI](https://img.shields.io/pypi/v/openpod)](https://pypi.org/project/openpod/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/openpod)](https://pypi.org/project/openpod/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**K-104 semantic routing + inter-agent communication.** 104 rooms. 48x cheaper than Opus. Zero dependencies.

OpenPod maps every AI query to a K-104 coordinate (4 suits x 13 ranks x 2 polarities = 104 semantic rooms), then routes it to the cheapest model that can handle it. 80% of queries never leave the built-in corpus.

## Install

```bash
pip install openpod
```

## Quick Start — Routing

```python
from openpod import KlawRouter

router = KlawRouter()
result = router.route("How do I center a div?")

print(result["response"])    # The answer
print(result["cost"])        # $0.0001
print(result["savings"])     # $0.0029 saved vs Sonnet baseline
print(result["tier_name"])   # "template" (free)
```

## Quick Start — Agent Comms

```python
from openpod import Pod, Agent, Memory

# Pod-based messaging between agents
pod = Pod("my_cell", roles=["alpha", "beta"])
pod.send("alpha", "beta", "Build the widget", suit="+7D")
messages = pod.inbox("beta")

# Full agent with K-routed thinking
agent = Agent("my-agent")

@agent.on_message
def handle(msg):
    return agent.think(msg.text)  # Routes to cheapest capable model

agent.run()
```

## How It Works

Every query gets a K-104 semantic address and routes to the minimum capable model:

| Tier | Models | Cost/query |
|------|--------|------------|
| 0 — Template | Built-in corpus (1,000+ patterns) | **$0.00** |
| 1 — Local | Ollama, OpenRouter free tier | **$0.00** |
| 2 — Cheap | Haiku, GPT-4o-mini, Gemini Flash | ~$0.001 |
| 3 — Mid | Sonnet, GPT-4o, Gemini Pro | ~$0.01 |
| 4 — Premium | Opus, GPT-4 | ~$0.05 |

~80% of everyday queries hit tier 0-1 (free). Only genuinely complex reasoning reaches premium tiers.

### K-104 Coordinate System

| Suit | Domain | Example |
|------|--------|---------|
| Hearts (H) | Emotion, relationship, connection | "I'm feeling stuck" -> +3H |
| Spades (S) | Mind, analysis, logic | "Debug this function" -> +7S |
| Diamonds (D) | Material, building, code | "Build a REST API" -> +5D |
| Clubs (C) | Action, energy, execution | "Deploy to prod" -> +9C |

**Empirical result:** Transformer activations cluster by K-104 suit with silhouette score 0.312 — the geometry is real, not imposed.

## CLI

```bash
openpod route "What is Python?"
openpod classify "Explain quantum physics"
openpod stats
openpod demo
openpod setup
openpod mcp    # Start MCP server for Claude Code
```

## Claude Code Integration

Add to `.mcp.json`:

```json
{
  "mcpServers": {
    "openpod": {
      "command": "openpod",
      "args": ["mcp"]
    }
  }
}
```

## API Keys

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
export GEMINI_API_KEY=AI...
```

Or pass directly: `KlawRouter(api_keys={...})`. No keys required for tier 0 template routing.

## Modules

| Module | Purpose |
|--------|---------|
| `openpod.router` | K-104 classifier + model routing engine |
| `openpod.core` | Pod messaging, K-Coda protocol, priorities |
| `openpod.agent` | Full agent with message handling + K-routed thinking |
| `openpod.memory` | Persistent agent memory |
| `openpod.skills` | Hot-reload skill system |
| `openpod.channels` | Multi-channel comms (ntfy, Telegram, Discord) |
| `openpod.models` | Local model management (Ollama, torch) |
| `openpod.auth` | License verification for paid tiers |
| `openpod.mcp_server` | MCP server for Claude Code |

## Testing

```bash
pip install pytest
python -m pytest tests/ -v
```

All 10 tests pass without API keys (template tier is free).

## Architecture

```
Query -> K-104 Classify (0.03ms) -> Route to cheapest tier
  |
  +-- 80% -> Built-in corpus (free, instant)
  +-- 16% -> Local model (free, ~1s)
  +--  4% -> Cloud API (paid, ~2s)
```

143 primitives = 104 content rooms (K-104) + 39 relational (3 axes x 13 ranks).
The minimum complete vocabulary for embodied, relational intelligence.

## License

MIT
