"""Tests d'acceptation de EXEMPLE-001.

Écrits et validés par un humain AVANT le travail de l'agent, commités sur la branche
principale avec le contrat. Ils vivent hors de `tests/` : la suite normale reste verte
tant que la fonctionnalité n'existe pas, et l'agent ne peut pas les modifier.
"""

import pytest

from app.providers import Providers
from app.repo import ItemRepo
from app.service import ItemService


@pytest.mark.parametrize("label", ["", "   ", "\t\n"])
def test_rejects_blank_label(label: str) -> None:
    service = ItemService(ItemRepo(), Providers.default())
    with pytest.raises(ValueError):
        service.register("a1", label)


def test_nothing_stored_on_refusal() -> None:
    repo = ItemRepo()
    service = ItemService(repo, Providers.default())
    with pytest.raises(ValueError):
        service.register("a1", "  ")
    assert repo.count() == 0
