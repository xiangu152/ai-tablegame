from __future__ import annotations

import json
import asyncio
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from werewolf.config import GameConfig

logger = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    """Parsed response from an AI agent."""
    action: str
    target: str | None = None
    reasoning: str = ""
    dialogue: str = ""
    raw_response: str = ""


class AgentError(Exception):
    """Raised when an agent call fails irrecoverably."""
    pass


class BaseAgent:
    """Base AI agent wrapping OpenAI-compatible async API client.

    Provides retry logic (max 2 retries), 60s timeout, structured JSON parsing,
    and fallback to default action on parse failure.
    """

    def __init__(self, config: GameConfig, agent_name: str = "base"):
        self.config = config
        self.agent_name = agent_name
        self.client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=60.0,
            max_retries=0,  # we handle retries ourselves via tenacity
        )
        self._semaphore = asyncio.Semaphore(config.concurrency_limit)

    @retry(
        stop=stop_after_attempt(3),  # initial + 2 retries
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((Exception,)),
    )
    async def _call_api(self, system_prompt: str, user_message: str) -> str:
        """Make a single API call with retry. Returns raw response text."""
        response = await self.client.chat.completions.create(
            model=self.config.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=self.config.temperature,
        )
        content = response.choices[0].message.content
        return content if content is not None else ""

    async def call(
        self,
        system_prompt: str,
        user_message: str,
        default_action: str = "abstain",
    ) -> AgentResponse:
        """Send prompt to LLM and return parsed AgentResponse.

        On JSON parse failure, returns AgentResponse with action=default_action
        and raw_response preserved for debugging.
        """
        async with self._semaphore:
            try:
                raw = await self._call_api(system_prompt, user_message)
            except Exception as e:
                logger.error(
                    "Agent '%s' API call failed after retries: %s",
                    self.agent_name,
                    e,
                )
                return AgentResponse(
                    action=default_action,
                    raw_response=str(e),
                )

        return self._parse_response(raw, default_action)

    def _parse_response(self, raw: str, default_action: str) -> AgentResponse:
        """Parse JSON from LLM response. Extract action, target, reasoning, dialogue."""
        json_str = self._extract_json(raw)
        if json_str is None:
            logger.error(
                "Agent '%s' could not extract JSON from response: %s",
                self.agent_name,
                raw[:200],
            )
            return AgentResponse(action=default_action, raw_response=raw)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(
                "Agent '%s' JSON parse error: %s. Raw: %s",
                self.agent_name,
                e,
                raw[:200],
            )
            return AgentResponse(action=default_action, raw_response=raw)

        action = data.get("action", default_action)
        if not isinstance(action, str):
            action = default_action

        target = data.get("target")
        if target is not None and not isinstance(target, str):
            target = str(target)

        reasoning = data.get("reasoning", "")
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)

        dialogue = data.get("dialogue", "")
        if not isinstance(dialogue, str):
            dialogue = str(dialogue)

        return AgentResponse(
            action=action,
            target=target,
            reasoning=reasoning,
            dialogue=dialogue,
            raw_response=raw,
        )

    def _extract_json(self, text: str) -> str | None:
        """Extract JSON object from text that may have markdown code fences or extra text."""
        if not text:
            return None

        # Try ```json ... ``` code fence
        fence_start = text.find("```json")
        if fence_start != -1:
            inner_start = fence_start + len("```json")
            fence_end = text.find("```", inner_start)
            if fence_end != -1:
                return text[inner_start:fence_end].strip()

        # Try ``` ... ``` code fence (no language tag)
        fence_start = text.find("```")
        if fence_start != -1:
            inner_start = fence_start + 3
            fence_end = text.find("```", inner_start)
            if fence_end != -1:
                candidate = text[inner_start:fence_end].strip()
                if candidate.startswith("{"):
                    return candidate

        # Find first { and matching }
        brace_start = text.find("{")
        if brace_start == -1:
            return None

        depth = 0
        in_string = False
        escape_next = False
        for i in range(brace_start, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[brace_start : i + 1]

        return None
