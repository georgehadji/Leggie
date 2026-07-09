"""EventBus Port — abstract interface for event publishing/subscription.

Enables decoupled communication between stages (Observer pattern).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from leggie.domain.models import Event, EventType

EventHandler = Callable[[Event], Any]


class EventBusPort(ABC):
    """Port for event-driven communication."""

    @abstractmethod
    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers."""
        ...

    @abstractmethod
    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Subscribe a handler to an event type."""
        ...

    @abstractmethod
    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Unsubscribe a handler from an event type."""
        ...
