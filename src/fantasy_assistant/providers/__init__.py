"""Fantasy platform providers. Each one adapts a platform to the shared model."""

from fantasy_assistant.providers.base import FantasyProvider
from fantasy_assistant.providers.biwenger import BiwengerProvider
from fantasy_assistant.providers.laliga_fantasy import LaLigaFantasyProvider

_REGISTRY: dict[str, type[FantasyProvider]] = {
    LaLigaFantasyProvider.key: LaLigaFantasyProvider,
    BiwengerProvider.key: BiwengerProvider,
}

#: platform keys the CLI can offer today
AVAILABLE = tuple(_REGISTRY)


def get_provider(key: str, **kwargs: object) -> FantasyProvider:
    """Instantiate a provider by key (e.g. ``"laliga"``)."""
    try:
        cls = _REGISTRY[key]
    except KeyError:
        raise ValueError(
            f"unknown provider {key!r}; available: {', '.join(_REGISTRY)}"
        ) from None
    return cls(**kwargs)


__all__ = [
    "AVAILABLE",
    "BiwengerProvider",
    "FantasyProvider",
    "LaLigaFantasyProvider",
    "get_provider",
]
