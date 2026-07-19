"""Subscription tiers and their usage limits.

Tiers gate how much a user can transcribe and which features they get. This
module is the single source of truth for tier names, limits, and feature
flags; the worker consults :func:`can_transcribe` to decide whether to spend
compute on a job, and the API surfaces tier/usage info to the UI.

Current tiers
-------------
* ``free``     — default; 30 lifetime minutes of transcription + summary.
* ``standard`` — unlimited transcription.
* ``pro``      — unlimited transcription + (future) continuous daily summary,
                 summary templates, and 24/7 support.

The pro-only features are declared here so the UI can show what a plan unlocks,
but enforcement for those features is not yet implemented (they're roadmap).
"""
from __future__ import annotations

import enum

# Sentinel for "no limit" so callers can compare without special-casing None.
UNLIMITED = float("inf")


class Tier(str, enum.Enum):
    FREE = "free"
    STANDARD = "standard"
    PRO = "pro"

    @property
    def label(self) -> str:
        return _TIER_LABELS[self]

    def features(self) -> "TierFeatures":
        return _TIER_FEATURES[self]


class TierFeatures:
    """Feature availability for a tier. Future gated features live here."""

    def __init__(
        self,
        *,
        unlimited_transcription: bool,
        continuous_summary: bool,
        summary_templates: bool,
        support_24_7: bool,
    ) -> None:
        self.unlimited_transcription = unlimited_transcription
        self.continuous_summary = continuous_summary
        self.summary_templates = summary_templates
        self.support_24_7 = support_24_7


# Lifetime transcription-minute budget per tier. Free is capped; the paid
# tiers are unlimited. None/INF means no cap.
_TIER_LIMITS_MINUTES: dict[Tier, float] = {
    Tier.FREE: 30.0,
    Tier.STANDARD: UNLIMITED,
    Tier.PRO: UNLIMITED,
}

_TIER_LABELS: dict[Tier, str] = {
    Tier.FREE: "Free",
    Tier.STANDARD: "Standard",
    Tier.PRO: "Pro",
}

_TIER_FEATURES: dict[Tier, TierFeatures] = {
    Tier.FREE: TierFeatures(
        unlimited_transcription=False,
        continuous_summary=False,
        summary_templates=False,
        support_24_7=False,
    ),
    Tier.STANDARD: TierFeatures(
        unlimited_transcription=True,
        continuous_summary=False,
        summary_templates=False,
        support_24_7=False,
    ),
    Tier.PRO: TierFeatures(
        unlimited_transcription=True,
        continuous_summary=True,
        summary_templates=True,
        support_24_7=True,
    ),
}


def default_tier() -> str:
    """Tier assigned to newly registered users."""
    return Tier.FREE.value


def is_valid_tier(value: str) -> bool:
    return value in {t.value for t in Tier}


def transcribe_limit_minutes(tier_value: str) -> float:
    """Lifetime transcription-minute budget for a tier (INF = unlimited)."""
    try:
        tier = Tier(tier_value)
    except ValueError:
        tier = Tier.FREE
    return _TIER_LIMITS_MINUTES[tier]


def can_transcribe(tier_value: str, usage_minutes: float) -> tuple[bool, float, float]:
    """Decide whether a user may start a transcription.

    Returns ``(allowed, used_minutes, limit_minutes)`` where ``limit_minutes``
    is ``INF`` for unlimited tiers. Free users are blocked once their lifetime
    usage reaches the 30-minute cap. The check is lenient (>=) so exactly
    hitting the cap still allows the final job to complete.
    """
    limit = transcribe_limit_minutes(tier_value)
    if limit is UNLIMITED:
        return (True, usage_minutes, UNLIMITED)
    allowed = usage_minutes < limit
    return (allowed, usage_minutes, limit)
