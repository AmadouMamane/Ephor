"""Couche 2 — persistance. Ne connaît pas la logique métier."""

from app.types import Item


class ItemRepo:
    def __init__(self) -> None:
        self._items: dict[str, Item] = {}

    def put(self, item: Item) -> None:
        self._items[item.id] = item

    def get(self, item_id: str) -> Item | None:
        return self._items.get(item_id)

    def count(self) -> int:
        return len(self._items)
