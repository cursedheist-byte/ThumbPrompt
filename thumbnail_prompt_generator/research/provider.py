"""
Concrete research providers.

NullResearchProvider ships generalized, category-level composition
patterns from general design knowledge — no live web calls, no
per-competitor scraping. It exists so the AI has *some* grounded
strategic context even in the MVP.

To plug in real research later (e.g. a web-search-backed provider):
  1. Create a new class implementing ResearchProvider.get_patterns().
  2. Set it as the return value of get_research_provider() below, or
     swap it via an env var / config flag.
No other file needs to change — services/thumbnail_service.py only
depends on the ResearchProvider interface.
"""

from research.base import ResearchProvider

_GENERIC_PATTERNS = {
    "gaming": [
        "Exaggerated scale contrast between the player/character and the "
        "environment or enemy communicates stakes instantly.",
        "A single, brightly lit subject against a darker, desaturated "
        "background keeps the focal point unmistakable.",
        "Cropping the subject tight and off-center (rule of thirds) reads "
        "better at small sizes than a centered full-body shot.",
    ],
    "documentary": [
        "A striking real-world image with strong natural lighting builds "
        "credibility and curiosity.",
        "Close, emotionally legible facial expressions outperform wide "
        "establishing shots for click-through.",
        "Muted, cinematic color grading signals seriousness and quality.",
    ],
    "vlog": [
        "An authentic, expressive face reacting to something creates an "
        "instant emotional hook.",
        "A clear before/after or two-moment contrast communicates the "
        "story arc at a glance.",
        "Warm, natural lighting reads as relatable rather than staged.",
    ],
    "challenge": [
        "Visualizing the extreme/absurd premise directly (scale, quantity, "
        "danger) is more effective than showing a neutral moment.",
        "A visible countdown, timer, or objective cue adds urgency.",
        "High color saturation and a shocked/determined expression boost "
        "curiosity.",
    ],
    "entertainment": [
        "Bold, high-contrast color blocking makes the thumbnail pop in a "
        "crowded feed.",
        "An exaggerated, theatrical expression reads clearly even tiny.",
        "A simple graphic focal object (prop, symbol) reinforces the topic "
        "without needing text.",
    ],
    "tech": [
        "The product or device shown at a dramatic angle with clean "
        "studio-style lighting signals quality.",
        "A minimal, uncluttered background (or soft gradient) keeps focus "
        "on the subject.",
        "Subtle glow/highlight accents on key parts of the product draw "
        "the eye without needing text.",
    ],
    "educational": [
        "A clear before/after or problem/solution visual communicates the "
        "value instantly.",
        "Simple, iconographic visual metaphors work better than complex "
        "scenes at small sizes.",
        "A confident, approachable expression builds trust quickly.",
    ],
}

_DEFAULT_PATTERNS = [
    "One unmistakable focal point with strong separation from the "
    "background reads fastest at small sizes.",
    "Emotion or visible stakes in the subject's expression/pose drives "
    "curiosity more than a neutral pose.",
    "High contrast between subject and background outperforms low-contrast, "
    "busy compositions.",
]


class NullResearchProvider(ResearchProvider):
    """Offline provider: generalized patterns keyed by category."""

    def get_patterns(self, category: str, topic: str, title: str) -> list[str]:
        return _GENERIC_PATTERNS.get(category, _DEFAULT_PATTERNS)


def get_research_provider() -> ResearchProvider:
    """Factory so callers never instantiate a concrete provider directly.
    Swap the return value here (or branch on a config flag/env var) to
    plug in a live web-search-backed provider without touching callers.
    """
    return NullResearchProvider()
