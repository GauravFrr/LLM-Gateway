from abc import ABC, abstractmethod
from typing import List, Dict, Tuple, Optional

# Standard exception hierarchy
class GatewayProviderError(Exception):
    """Base exception for all provider failures."""
    def __init__(self, message: str, provider: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.status_code = status_code
        # Whether this failure should increment the circuit breaker failure counter.
        # Set to False for quota/rate-limit errors: the provider is UP, just throttling us.
        self.trips_circuit: bool = True


class RetryableProviderError(GatewayProviderError):
    """Exceptions that are safe to retry (e.g., rate limits, network timeouts, transient 5xx)."""
    pass


class ProviderRateLimitError(RetryableProviderError):
    """Provider returned 429 (quota/rate-limit). Retryable but does NOT trip the circuit breaker."""
    def __init__(self, message: str, provider: str, status_code: int = 429):
        super().__init__(message, provider, status_code)
        self.trips_circuit = False  # Provider is UP — only this request is throttled


class NonRetryableProviderError(GatewayProviderError):
    """Exceptions that should not be retried (e.g., bad request, invalid API keys, invalid models)."""
    pass


class BaseProvider(ABC):
    """
    Abstract interface for all model providers.
    All providers must implement this interface to ensure type safety and normalization.
    """

    @abstractmethod
    async def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
    ) -> Tuple[str, int, int]:
        """
        Send a chat completion request to the provider.

        Args:
            model: The exact provider model string.
            messages: List of message dictionaries, each with 'role' and 'content'.
            max_tokens: Optional token limit for generation.

        Returns:
            A tuple of (generated_content_string, input_tokens_count, output_tokens_count).

        Raises:
            RetryableProviderError: If the error is transient and can be retried.
            NonRetryableProviderError: If the error is permanent and should fail immediately.
        """
        pass
