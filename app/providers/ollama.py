import httpx
from typing import List, Dict, Tuple, Optional
from app.providers.base import BaseProvider, RetryableProviderError, NonRetryableProviderError

class OllamaProvider(BaseProvider):
    """
    Provider wrapper for local Ollama API via direct HTTP REST calls.
    """

    def __init__(self, base_url: str):
        """
        Initialize Ollama provider.

        Args:
            base_url: Ollama base url endpoint (e.g. http://localhost:11434).
        """
        self.base_url = base_url.rstrip("/")

    async def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
    ) -> Tuple[str, int, int]:
        """
        Execute chat completion on local Ollama model.

        Args:
            model: Model name string.
            messages: List of chat messages in standard role/content format.
            max_tokens: Maximum tokens to generate.

        Returns:
            Tuple of (assistant_content, input_tokens, output_tokens)
        """
        url = f"{self.base_url}/api/chat"
        
        # Prepare messages in ollama format
        formatted_messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in messages
        ]

        payload = {
            "model": model,
            "messages": formatted_messages,
            "stream": False,
        }

        if max_tokens is not None:
            payload["options"] = {"num_predict": max_tokens}

        try:
            # We instantiate a temporary client for socket cleanup in Phase 1
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code >= 500:
                raise RetryableProviderError(f"Ollama server error: {str(e)}", provider="ollama", status_code=status_code)
            else:
                raise NonRetryableProviderError(f"Ollama client error: {str(e)}", provider="ollama", status_code=status_code)
        except httpx.RequestError as e:
            raise RetryableProviderError(f"Ollama network/connection error: {str(e)}", provider="ollama")
        except Exception as e:
            raise NonRetryableProviderError(f"Unexpected Ollama error: {str(e)}", provider="ollama")

        try:
            data = response.json()
        except ValueError as e:
            raise NonRetryableProviderError(f"Ollama returned invalid JSON: {str(e)}", provider="ollama")

        # Parse message content
        message_data = data.get("message", {})
        content_text = message_data.get("content", "")

        # Ollama usage metrics
        input_tokens = data.get("prompt_eval_count", 0)
        output_tokens = data.get("eval_count", 0)

        return content_text, input_tokens, output_tokens
