"""
klaw_streamer.py -- SSE streaming for KLAW UFO.

Provides functions for creating streaming responses in the OpenAI format.
Handles both fake streaming (for corpus hits) and real streaming (for Hermes/Claude).
"""

import asyncio
import json
from typing import Dict, AsyncGenerator
from fastapi.responses import StreamingResponse

# ---------------------------------------------------------------------------
# Streaming response creation
# ---------------------------------------------------------------------------

def create_streaming_response(
    result: Dict,
    tier_used: str,
    mana_cost: int,
    mana_remaining: int,
    k_address: str,
    confidence: float,
    template_id: str,
    latency_ms: int,
) -> StreamingResponse:
    """Create a streaming response based on the result."""
    if tier_used == "corpus":
        return StreamingResponse(
            fake_stream(
                result["response"],
                tier_used,
                mana_cost,
                mana_remaining,
                k_address,
                confidence,
                template_id,
                latency_ms,
            ),
            media_type="text/event-stream"
        )
    else:
        # Implement real streaming passthrough for Hermes/Claude here
        async def passthrough_stream(): # Example, needs real impl
            yield f"data: {json.dumps({'content': result['response']})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            passthrough_stream(),
            media_type="text/event-stream"
        )


# ---------------------------------------------------------------------------
# Fake streaming for corpus hits
# ---------------------------------------------------------------------------

async def fake_stream(
    response: str,
    tier_used: str,
    mana_cost: int,
    mana_remaining: int,
    k_address: str,
    confidence: float,
    template_id: str,
    latency_ms: int,
) -> AsyncGenerator[str, None]:
    """Chunk the assembled response and yield SSE chunks."""
    chunk_size = 32  # Adjust as needed
    for i in range(0, len(response), chunk_size):
        chunk = response[i:i + chunk_size]
        data = {
            "choices": [
                {
                    "delta": {
                        "content": chunk
                    },
                    "finish_reason": None,
                    "index": 0
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            },
            "tier_used": tier_used,
            "mana_cost": mana_cost,
            "mana_remaining": mana_remaining,
            "k_address": k_address,
            "confidence": confidence,
            "template_id": template_id,
            "latency_ms": latency_ms,
        }
        yield f"data: {json.dumps(data)}\n\n"
        await asyncio.sleep(0.008)

    yield "data: [DONE]\n\n"