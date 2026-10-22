"""Common connector interface and type registry.

A connector turns one configured source into a list of normalised Articles.
To add a new kind of source (Twitter/X API, a JSON API, a scraper...):

    @register("twitter")
    class TwitterConnector(Connector):
        async def fetch(self, client): ...

then reference `type: twitter` in config/sources.yaml.
"""

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
        raise ValueError(f"Unknown connector type {source.type!r} for source {source.id!r}")
    return cls(source)
