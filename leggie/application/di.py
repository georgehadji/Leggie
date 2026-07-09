"""Dependency Injection container — re-exported from infrastructure.

Moved to leggie/infrastructure/container.py per Clean Architecture: the
composition root belongs in Infrastructure, not Application.
"""

from leggie.infrastructure.container import Container, BindingNotFoundError

__all__ = ["Container", "BindingNotFoundError"]
