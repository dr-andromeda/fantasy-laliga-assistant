from __future__ import annotations

import pytest

from fantasy_assistant.model import Player
from fantasy_assistant.providers.laliga_fantasy import LaLigaFantasyProvider


@pytest.fixture
def provider() -> LaLigaFantasyProvider:
    return LaLigaFantasyProvider(source="csv")


@pytest.fixture
def universe(provider: LaLigaFantasyProvider) -> list[Player]:
    return provider.load_players()
