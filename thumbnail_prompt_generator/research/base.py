"""
Abstract interface for a "research layer" that could look up successful
thumbnail patterns for a given topic/category (e.g. via web search or a
curated dataset of high-performing thumbnails).

The MVP does not call out to the live web. Instead it ships a
NullResearchProvider that returns a small set of well-known, generic
composition patterns per category, written from general design
knowledge rather than any specific creator's work. This keeps the
prompt engine's interface stable so a real web/search-backed provider
can be dropped in later (see provider.py) with zero changes to
services/thumbnail_service.py.
"""

from abc import ABC, abstractmethod


class ResearchProvider(ABC):
    @abstractmethod
    def get_patterns(self, category: str, topic: str, title: str) -> list[str]:
        """Return a short list of plain-language composition/strategy
        patterns relevant to this category/topic, to hand to the AI as
        extra context. Must NOT return content tied to a specific,
        identifiable competitor thumbnail — only generalized techniques.
        """
        raise NotImplementedError
