from anthropic import AsyncAnthropic, APIError, APIStatusError
from typing import List, Dict, Tuple, Optional
from app.providers.base import BaseProvider, RetryableProviderError, NonRetryableProviderError

class ClaudeProvider(BaseProvider):
    """
    Provider wrapper for Anthropic Claude API using the anthropic SDK.
    """

    def __init__(self, api_key: str):
        """
        Initialize Claude client.

        Args:
            api_key: Anthropic API key string.
        """
        if not api_key:
            raise NonRetryableProviderError("Anthropic API key is required but missing.", provider="claude")
        self.client = AsyncAnthropic(api_key=api_key)

    async def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
    ) -> Tuple[str, int, int]:
        """
        Execute chat completion on Anthropic Claude model.

        Args:
            model: Model name string.
            messages: List of chat messages in standard role/content format.
            max_tokens: Maximum tokens to generate.

        Returns:
            Tuple of (assistant_content, input_tokens, output_tokens)
        """
        from app.config import settings
        import asyncio
        if settings.MOCK_PROVIDERS:
            # Simulate a small provider network latency
            await asyncio.sleep(0.010)
            return "This is a mocked Claude response.", 15, 10
        # Anthropic does not accept 'system' role in messages.
        # It must be passed separately as the 'system' keyword argument.
        system_parts = [msg["content"] for msg in messages if msg.get("role") == "system"]
        system_instruction = "\n".join(system_parts) if system_parts else None

        filtered_messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in messages
            if msg.get("role") != "system"
        ]

        # max_tokens is required for Claude
        final_max_tokens = max_tokens or 1024

        kwargs = {}
        if system_instruction:
            kwargs["system"] = system_instruction

        try:
            response = await self.client.messages.create(
                model=model,
                messages=filtered_messages,
                max_tokens=final_max_tokens,
                **kwargs
            )
        except APIStatusError as e:
            if e.status_code >= 500 or e.status_code == 429:
                raise RetryableProviderError(f"Claude API status error: {e.message}", provider="claude", status_code=e.status_code)
            else:
                raise NonRetryableProviderError(f"Claude API status error: {e.message}", provider="claude", status_code=e.status_code)
        except APIError as e:
            raise RetryableProviderError(f"Claude API error: {str(e)}", provider="claude")
        except Exception as e:
            raise NonRetryableProviderError(f"Unexpected Claude error: {str(e)}", provider="claude")

        # Claude returns a content list with blocks
        content_text = ""
        if response.content:
            content_text = response.content[0].text if hasattr(response.content[0], "text") else ""

        input_tokens = response.usage.input_tokens if response.usage else 0
        output_tokens = response.usage.output_tokens if response.usage else 0

        return content_text, input_tokens, output_tokens
