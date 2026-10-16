"""Extension point for future AI audio tools (stem separation, analysis).

The MVP ships a documented ``UnavailableProvider``: BeatLab never pretends to
run AI it does not have. A real provider can be dropped in later without
touching the editor — see ``docs/AI_EXTENSIONS.md`` for the integration guide
(resource requirements, queueing, licensing).

Beat replacement (vocals over a new instrumental) is already possible in the
MVP using ordinary multitrack mixing: users place an existing acapella clip
on one track and the new beat on another.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


class StemSeparationUnavailable(Exception):
    """Raised by providers that cannot perform separation."""


@dataclass(frozen=True)
class StemResult:
    """Real output files produced by a stem-separation run."""

    vocals: Path | None = None
    drums: Path | None = None
    bass: Path | None = None
    other: Path | None = None
    extra: dict[str, Path] = field(default_factory=dict)


class StemSeparationProvider(ABC):
    """Interface every stem-separation backend must implement.

    Implementations must write real audio files and never fabricate results.
    Heavy models belong behind a Celery queue (see docs), not in a request.
    """

    #: Short machine-readable identifier, e.g. "demucs".
    name: str = "abstract"

    @abstractmethod
    def is_available(self) -> bool:
        """Whether the provider can run in this deployment."""

    @abstractmethod
    def separate(self, source_path: Path, output_dir: Path) -> StemResult:
        """Separate ``source_path`` into stems written under ``output_dir``."""


class UnavailableProvider(StemSeparationProvider):
    """Default provider: honestly reports that AI separation is not included."""

    name = "unavailable"

    def is_available(self) -> bool:
        return False

    def separate(self, source_path: Path, output_dir: Path) -> StemResult:
        raise StemSeparationUnavailable(
            "AI stem separation is not configured in this deployment."
        )


def get_provider() -> StemSeparationProvider:
    """Return the configured provider (default: unavailable).

    Future deployments can register a real provider by setting the
    ``BEATLAB_STEM_PROVIDER`` import path (documented in docs/AI_EXTENSIONS.md).
    """
    import os

    from django.conf import settings
    from django.utils.module_loading import import_string

    path = os.environ.get("BEATLAB_STEM_PROVIDER", "").strip() or getattr(
        settings, "BEATLAB_STEM_PROVIDER", ""
    )
    if path:
        provider_cls = import_string(path)
        return provider_cls()
    return UnavailableProvider()
