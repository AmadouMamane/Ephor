"""Fumée : le câblage tient debout. Remplace par tes vrais tests de comportement."""

from app.providers import Providers
from app.repo import ItemRepo
from app.runtime import build
from app.service import ItemService


def test_build_returns_a_service() -> None:
    assert isinstance(build(), ItemService)


def test_register_persists_and_trims() -> None:
    repo = ItemRepo()
    svc = ItemService(repo, Providers.default())
    item = svc.register("a1", "  Libellé  ")
    assert item.label == "Libellé"
    assert repo.get("a1") == item
    assert repo.count() == 1
