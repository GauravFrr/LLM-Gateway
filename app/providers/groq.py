from groq import APIStatusError, AsyncGroq, GroqError

from app.providers.base import BaseProvider, NonRetryableProviderError, ProviderRateLimitError, RetryableProviderError


class GroqProvider(BaseProvider):
    """
    Provider wrapper for Groq API using the groq SDK (OpenAI-compatible).
    """

    def __init__(self, api_key: str):
        """
        Initialize Groq client.

        Args:
            api_key: Groq API key string.
        """
        if not api_key:
            raise NonRetryableProviderError("Groq API key is required but missing.", provider="groq")
        self.client = AsyncGroq(api_key=api_key)

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
    ) -> tuple[str, int, int]:
        """
        Execute chat completion on Groq model.

        Args:
            model: Model name string.
            messages: List of chat messages in standard role/content format.
            max_tokens: Maximum tokens to generate.

        Returns:
            Tuple of (assistant_content, input_tokens, output_tokens)
        """
        import asyncio

        from app.config import settings

        if settings.MOCK_PROVIDERS:
            # Simulate a small provider network latency
            await asyncio.sleep(0.010)
            return "This is a mocked Groq response.", 15, 10
        # Format messages for openai-compatible API
        formatted_messages = [{"role": msg["role"], "content": msg["content"]} for msg in messages]

        kwargs = {}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        try:
            response = await self.client.chat.completions.create(model=model, messages=formatted_messages, **kwargs)
        except APIStatusError as e:
            if e.status_code == 429:
                raise ProviderRateLimitError(f"Groq API status error: {e.message}", provider="groq")
            elif e.status_code >= 500:
                raise RetryableProviderError(
                    f"Groq API status error: {e.message}", provider="groq", status_code=e.status_code
                )
            else:
                raise NonRetryableProviderError(
                    f"Groq API status error: {e.message}", provider="groq", status_code=e.status_code
                )
        except GroqError as e:
            raise RetryableProviderError(f"Groq error: {str(e)}", provider="groq")
        except Exception as e:
            raise NonRetryableProviderError(f"Unexpected Groq error: {str(e)}", provider="groq")

        content_text = ""
        if response.choices:
            content_text = response.choices[0].message.content or ""

        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0

        return content_text, input_tokens, output_tokens
