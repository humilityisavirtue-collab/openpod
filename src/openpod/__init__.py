"""
OpenPod -- 143-Primitive Inter-Agent Communication Protocol.

Whale-grade pod comms for AI agent cells. Zero deps. Magically fast.
UFO-class: K-routed thinking, persistent memory, hot-reload skills, multi-channel.

Quick start:
    from openpod import Pod, Agent, Memory
    from openpod.channels import ntfy, telegram

    # Simple pod comms
    pod = Pod("my_cell", roles=["alpha", "beta"])
    pod.send("alpha", "beta", "Build the widget", suit="+7D")
    messages = pod.inbox("beta")

    # Full agent
    agent = Agent("my-agent")
    agent.connect(ntfy(topic="my-topic"))

    @agent.on_message
    def handle(msg):
        return agent.think(msg.text)  # K-routed, cheapest capable model

    agent.run()

143 primitives = 104 content (K-rooms) + 39 relational (3 axes x 13 ranks).
The minimum complete vocabulary for embodied, relational intelligence.
"""

__version__ = "0.2.0"

from openpod.core import Pod, KCoda, Message, Priority
from openpod.core import send, inbox, close_session
from openpod.models import LocalModels, load_torch_model, torch_available
from openpod.memory import Memory
from openpod.skills import SkillLoader, skill
from openpod.agent import Agent


# Router (K-104 model routing)
from openpod.router import KlawRouter, KClassifier, CostEngine, PRICING, TIER_NAMES
from openpod.auth import verify_license, LicenseStatus, clear_cache

__all__ = [
    # Core
    "Pod", "KCoda", "Message", "Priority",
    "send", "inbox", "close_session",
    # Models
    "LocalModels", "load_torch_model", "torch_available",
    # Memory
    "Memory",
    # Skills
    "SkillLoader", "skill",
    # Agent
    "Agent",
    # Router
    "KlawRouter", "KClassifier", "CostEngine", "PRICING", "TIER_NAMES",
    "verify_license", "LicenseStatus", "clear_cache",
    "__version__",
]
