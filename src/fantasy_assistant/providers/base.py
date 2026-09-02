"""The provider interface every fantasy platform plugs into."""

from __future__ import annotations

import abc

from fantasy_assistant.model import Constraints, Fixture, Player, ScoringRules, Squad


class FantasyProvider(abc.ABC):
    """Read-only access to one fantasy platform's public data.

    v1 only needs the four public methods below; :meth:`import_squad` is optional
    and may raise :class:`NotImplementedError` — the user can always supply their
    squad by hand instead.
    """

    #: short lowercase identifier, e.g. "laliga"
    key: str

    @abc.abstractmethod
    def load_players(self) -> list[Player]:
        """The full player universe with current prices and points."""

    @abc.abstractmethod
    def fixtures(self, upcoming: int = 5) -> list[Fixture]:
        """The next ``upcoming`` gameweeks of fixtures."""

    @abc.abstractmethod
    def scoring_rules(self) -> ScoringRules:
        """How this platform turns match events into fantasy points."""

    @abc.abstractmethod
    def constraints(self) -> Constraints:
        """Squad-building rules for this platform."""

    def import_squad(self, *, team_id: str) -> Squad:
        """Fetch the user's current squad. Optional; not needed for v1."""
        raise NotImplementedError(
            f"{type(self).__name__} cannot import a squad yet — provide it via squad.yaml"
        )
