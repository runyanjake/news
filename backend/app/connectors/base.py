"""Connector interface and registry; @register("<type>") makes a type usable in config/sources.yaml."""

from abc import ABC, abstractmethod

import httpx

from ..models import Article, SourceConfig

_REGISTRY: dict[str, type["Connector"]] = {}


class Connector(ABC):
    def __init__(self, source: SourceConfig):
        self.source = source

    @abstractmethod
    async def fetch(self, client: httpx.AsyncClient) -> list[Article]:
        """Return the source's current items. Raise on transport errors."""

    def make_article(self, **fields) -> Article:
        return Article(
            source_id=self.source.id,
            source_name=self.source.name,
            reputation=self.source.reputation,
            tags=self.source.tags,
            **fields,
        )


def register(type_name: str):
    def decorator(cls: type[Connector]) -> type[Connector]:
        _REGISTRY[type_name] = cls
        return cls

    return decorator


def build_connector(source: SourceConfig) -> Connector:
    try:
        cls = _REGISTRY[source.type]
    except KeyError:
        raise ValueError(f"Unknown connector type {source.type!r} for source {source.id!r}") from None
    return cls(source)
