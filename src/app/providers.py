"""Point d'entrée UNIQUE des préoccupations transverses.

Pourquoi : si l'horloge, l'aléa, le log et la télémétrie passent par ici, alors
les tests sont déterministes et un agent ne peut pas introduire un `datetime.now()`
au fond d'un service sans que `make arch` le voie.
"""

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class Providers:
    logger: logging.Logger
    now: Callable[[], datetime]
    rand: random.Random

    @staticmethod
    def default(log_level: str = "INFO") -> "Providers":
        logging.basicConfig(level=log_level, format="%(levelname)s %(name)s %(message)s")
        return Providers(
            logger=logging.getLogger("app"),
            now=lambda: datetime.now(UTC),
            # Aléa NON cryptographique, assumé : jitter, échantillonnage, ids de test.
            # Pour tout usage de sécurité (token, sel, mot de passe), utiliser `secrets`.
            rand=random.Random(),  # noqa: S311
        )
