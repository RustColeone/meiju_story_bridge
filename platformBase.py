"""
Platform abstraction layer.

This is the extension point for messaging app integrations (Discord, Telegram, etc.).
`meiju_bridge.py` is the fixed destination/core.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class PlatformMessage:
    """Normalized inbound message envelope from a social platform."""
    channel_id: str
    author_id: str
    author_name: str
    content: str
    raw: Any


class PlatformAdapter(ABC):
    """Abstract adapter contract for social/messaging platforms."""

    @abstractmethod
    def is_self_message(self, raw_message: Any) -> bool:
        """Return True when message is sent by the bot itself."""
        raise NotImplementedError

    @abstractmethod
    def normalize_message(self, raw_message: Any) -> Optional[PlatformMessage]:
        """Convert a raw platform message into PlatformMessage."""
        raise NotImplementedError

    @abstractmethod
    async def send_text(self, raw_target: Any, text: str) -> None:
        """Send text back to the same target/channel/thread."""
        raise NotImplementedError

    @abstractmethod
    async def typing(self, raw_target: Any):
        """Return an async context manager for typing indicator if supported."""
        raise NotImplementedError
