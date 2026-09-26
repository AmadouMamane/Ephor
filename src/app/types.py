"""Couche 0 — types purs. N'importe rien du projet. Jamais d'effet de bord."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Item:
    """Exemple de type métier. Remplace-le."""

    id: str
    label: str
