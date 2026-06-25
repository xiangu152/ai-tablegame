# -*- coding: utf-8 -*-
"""ToolCallCompactor — compress redundant tool calls from context.

Removes tool calls whose results are already available via ChatContextMiddleware
(Chat listen, Card get_summary, Read) and truncates WaitForMessages results.
"""
import json
import logging
from typing import Any

from agentscope.middleware import MiddlewareBase
from agentscope.message import Msg
from agentscope.message._block import ToolCallBlock, ToolResultBlock

logger = logging.getLogger(__name__)

# Tool calls to discard (result already in ChatContext or system prompt)
DISCARD_TOOLS = {
    ("Chat", "listen"),       # messages in ChatContext
    ("Chat", "list_rooms"),   # room list rarely changes
}


def _get_action(block) -> str | None:
    """Extract 'action' from ToolCallBlock.input (JSON string)."""
    try:
        if isinstance(block.input, str):
            return json.loads(block.input).get("action")
        elif isinstance(block.input, dict):
            return block.input.get("action")
    except Exception:
        pass
    return None


class ToolCallCompactor(MiddlewareBase):
    """Compact old tool calls from state.context before model call.

    - Discard: Chat(listen), Chat(list_rooms)
    - Truncate: WaitForMessages results to 500 chars
    - Keep: Chat(send), Dice, Card(create/get_summary), Write, CreatePlayer
    """

    async def on_model_call(self, agent, input_kwargs, next_handler):
        messages = input_kwargs["messages"]

        for msg in messages:
            if not isinstance(msg, Msg) or msg.role != "assistant":
                continue

            # Collect IDs of dropped ToolCallBlocks
            dropped_ids = set()
            new_content = []

            for block in msg.content:
                if isinstance(block, ToolCallBlock):
                    action = _get_action(block)
                    key = (block.name, action)

                    if key in DISCARD_TOOLS:
                        dropped_ids.add(block.id)
                        continue  # discard this call

                    new_content.append(block)

                elif isinstance(block, ToolResultBlock):
                    # Drop orphaned results (matching call was discarded)
                    if block.id in dropped_ids:
                        continue
                    # Truncate WaitForMessages result
                    if block.name == "WaitForMessages" and len(block.output) > 500:
                        block = block.model_copy(update={"output": block.output[:500] + "\n...[truncated]"})

                    new_content.append(block)

                else:
                    new_content.append(block)

            msg.content = new_content

        return await next_handler(**input_kwargs)
