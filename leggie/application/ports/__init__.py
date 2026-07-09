"""Ports — abstract effect boundaries (Hexagonal/Ports & Adapters).

Every external dependency goes through a port. Each port has contract tests + fakes.
"""

from leggie.application.ports.blackboard import BlackboardEntry, BlackboardPort
from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.event_bus import EventBusPort
from leggie.application.ports.llm import LLMPort
from leggie.application.ports.retrieval import RetrievalPort, RetrievalResult
from leggie.application.ports.router import RouteResult, RouterPort
from leggie.application.ports.state import StatePort

__all__ = [
    "LLMPort",
    "RouterPort",
    "RouteResult",
    "RetrievalPort",
    "RetrievalResult",
    "StatePort",
    "EventBusPort",
    "BlackboardPort",
    "BlackboardEntry",
    "CitationParserPort",
]
