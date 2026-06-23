"""
LLM Client - Anthropic SDK 封装，指向 DeepSeek API。

提供统一的 chat 接口，支持:
- 单轮对话
- 多轮对话
- JSON 结构化输出（通过 tool_use）
- 自动重试
"""

import json
import time
import logging
from typing import Optional, Any

try:
    import anthropic
except ImportError:
    anthropic = None

from .config_loader import Config

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Anthropic SDK 的轻量封装，适配 DeepSeek API。

    Usage:
        config = load_config("config.yaml")
        client = LLMClient(config)

        # 简单对话
        response = client.chat("你好，请介绍你自己")

        # 多轮对话
        response = client.chat([
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮助你的？"},
            {"role": "user", "content": "请讲个故事"}
        ])

        # 要求 JSON 输出
        result = client.chat_json("将以下文本分类", system="你是一个分类器")
    """

    def __init__(self, config: Config, max_retries: int = 3):
        """
        Args:
            config: Config 对象（来自 config_loader）
            max_retries: API 调用失败时的最大重试次数
        """
        if anthropic is None:
            raise ImportError(
                "Anthropic SDK is required. Install with: pip install anthropic"
            )

        self.config = config
        self.max_retries = max_retries

        # 创建 Anthropic 客户端，指向 DeepSeek 兼容 API
        self._client = anthropic.Anthropic(
            base_url=config.base_url,
            api_key=config.api_key,
        )
        logger.info(
            "LLMClient initialized: model=%s, base_url=%s",
            config.model,
            config.base_url,
        )

    def chat(
        self,
        messages: Any,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """
        发送消息并返回文本响应。

        Args:
            messages: 字符串（单条用户消息）或消息列表
            system: 系统提示词
            temperature: 温度参数 (0.0-1.0)
            max_tokens: 最大输出 token

        Returns:
            LLM 的文本响应

        Raises:
            Exception: API 调用在 max_retries 次重试后仍然失败
        """
        # 标准化 messages 格式
        if isinstance(messages, str):
            api_messages = [{"role": "user", "content": messages}]
        else:
            api_messages = messages

        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = self._client.messages.create(
                    model=self.config.model,
                    messages=api_messages,
                    system=system or "",
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                # 提取文本内容
                text_parts = []
                for block in response.content:
                    if block.type == "text":
                        text_parts.append(block.text)
                return "".join(text_parts)

            except Exception as e:
                last_error = e
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s",
                    attempt + 1,
                    self.max_retries,
                    e,
                )
                if attempt < self.max_retries - 1:
                    wait_time = 2**attempt  # 指数退避: 1s, 2s, 4s
                    time.sleep(wait_time)

        raise RuntimeError(
            f"LLM call failed after {self.max_retries} retries. "
            f"Last error: {last_error}"
        )

    def chat_json(
        self,
        messages: Any,
        system: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 8192,
    ) -> dict:
        """
        发送消息并要求返回 JSON 结构化数据。

        通过 system prompt 指示 LLM 只返回合法 JSON。

        Args:
            messages: 字符串或消息列表
            system: 系统提示词（会自动追加 JSON 格式要求）
            temperature: 温度参数（JSON 输出建议 0.1-0.3）
            max_tokens: 最大输出 token

        Returns:
            解析后的 dict/list

        Raises:
            ValueError: LLM 返回的不是合法 JSON
        """
        json_system = (
            (system or "")
            + "\n\n请只返回合法的 JSON 数据，"
            "不要包含任何 markdown 代码块标记（如 ```json），"
            "只返回纯 JSON 字符串。"
        )

        response_text = self.chat(
            messages=messages,
            system=json_system,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # 清理可能的 markdown 代码块标记
        text = response_text.strip()
        if text.startswith("```"):
            # 移除开头的 ```json 或 ```
            text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON response: %s", response_text[:500])
            raise ValueError(
                f"LLM response is not valid JSON: {e}\n"
                f"Response (first 500 chars): {response_text[:500]}"
            )

    def summarize(
        self,
        text: str,
        target_length: int = 500,
        instruction: str = "请对以下内容生成精炼摘要",
        temperature: float = 0.3,
    ) -> str:
        """
        对长文本生成摘要。

        Args:
            text: 需要摘要的文本
            target_length: 目标摘要长度（字符数）
            instruction: 摘要指令
            temperature: 温度参数

        Returns:
            摘要文本
        """
        system = (
            f"你是一个专业的文本摘要助手。{instruction}。"
            f"摘要长度约 {target_length} 字，保留关键信息。"
        )
        return self.chat(
            messages=f"请摘要以下内容：\n\n{text}",
            system=system,
            temperature=temperature,
            max_tokens=max(target_length * 3, 2048),
        )

    @property
    def model(self) -> str:
        return self.config.model
