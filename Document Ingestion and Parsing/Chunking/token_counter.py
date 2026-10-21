"""Token-counting abstractions. Approximate counter is dependency-free."""
from abc import ABC, abstractmethod
import re


class TokenCounter(ABC):
    @abstractmethod
    def count(self, text: str) -> int:
        raise NotImplementedError


class ApproximateTokenCounter(TokenCounter):
    """Simply Estimate"""
    _pattern = re.compile(r"\w+|[^\w\s]", re.UNICODE)

    def count(self, text: str) -> int:
        return len(self._pattern.findall(text))


