from google import genai
from google.genai import types
from google.genai.errors import APIError
from typing import List, Dict, Tuple, Optional
from app.providers.base import BaseProvider, RetryableProviderError, NonRetryableProviderError, ProviderRateLimitError

class GeminiProvider(BaseProvider):
    """
    Provider wrapper for Google Gemini API using the modern google-genai SDK.
    """

    def __init__(self, api_key: str):
        """
        Initialize Gemini client.

        Args:
            api_key: Gemini API key string.
        """
        if not api_key:
            raise NonRetryableProviderError("Gemini API key is required but missing.", provider="gemini")
        self.client = genai.Client(api_key=api_key)

    async def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
    ) -> Tuple[str, int, int]:
        """
        Execute chat completion on Google Gemini model.

        Args:
            model: Model name string.
            messages: List of chat messages in standard role/content format.
            max_tokens: Maximum tokens to generate.

        Returns:
            Tuple of (assistant_content, input_tokens, output_tokens)
        """
        contents = []
        system_instruction = None

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                system_instruction = content
            elif role == "assistant":
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=content)]
                    )
                )
            else:
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=content)]
                    )
                )

        config = types.GenerateContentConfig()
        if max_tokens is not None:
            config.max_output_tokens = max_tokens
        if system_instruction is not None:
            config.system_instruction = system_instruction

        try:
            response = await self.client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except APIError as e:
            code = e.code
            if code == 429:
                raise ProviderRateLimitError(f"Gemini API error: {e.message}", provider="gemini")
            elif code is not None and code >= 500:
                raise RetryableProviderError(f"Gemini API error: {e.message}", provider="gemini", status_code=code)
            else:
                raise NonRetryableProviderError(f"Gemini API error: {e.message}", provider="gemini", status_code=code)
        except Exception as e:
            raise NonRetryableProviderError(f"Unexpected Gemini error: {str(e)}", provider="gemini")

        # The SDK text attribute might be None if blocked or empty
        content_text = response.text or ""
        
        usage = response.usage_metadata
        input_tokens = (usage.prompt_token_count or 0) if usage else 0
        output_tokens = (usage.candidates_token_count or 0) if usage else 0

        return content_text, input_tokens, output_tokens
