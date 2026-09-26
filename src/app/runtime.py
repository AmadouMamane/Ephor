"""Couche 4 — assemblage et cycle de vie. Le seul endroit qui câble les dépendances."""

from app.config import Settings
from app.providers import Providers
from app.repo import ItemRepo
from app.service import ItemService


def build() -> ItemService:
    settings = Settings.from_env()
    providers = Providers.default(settings.log_level)
    return ItemService(ItemRepo(), providers)
