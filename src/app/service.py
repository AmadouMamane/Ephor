"""Couche 3 — logique métier. Pure vis-à-vis du monde : tout effet passe par Providers."""

from app.providers import Providers
from app.repo import ItemRepo
from app.types import Item


class ItemService:
    def __init__(self, repo: ItemRepo, providers: Providers) -> None:
        self._repo = repo
        self._p = providers

    def register(self, item_id: str, label: str) -> Item:
        item = Item(id=item_id, label=label.strip())
        self._repo.put(item)
        self._p.logger.info("item.registered id=%s", item.id)
        return item
