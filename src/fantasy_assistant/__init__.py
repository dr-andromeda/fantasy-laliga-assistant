"""fantasy-laliga-assistant — decision support for LaLiga Fantasy.

    from fantasy_assistant.providers import get_provider
    from fantasy_assistant.squad_io import load_squad
    from fantasy_assistant.valuation import value_squad

    prov = get_provider("laliga")
    universe = prov.load_players()
    squad = load_squad("squad.yaml", universe)
    report = value_squad(squad, prov.fixtures(), squad.validate_against(prov.constraints()))
"""

__version__ = "0.1.0"
