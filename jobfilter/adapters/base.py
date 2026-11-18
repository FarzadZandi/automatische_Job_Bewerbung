from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from jobfilter.fetch import Fetcher


@dataclass(frozen=True)
class JobStub:
    url: str
    title: str
    employer: str = ""
    location: str = ""
    posted_date: str = ""


@dataclass(frozen=True)
class JobAd(JobStub):
    deadline: str = ""
    full_text: str = ""
    employment_type: str = ""
    raw_meta: dict[str, Any] = field(default_factory=dict)


class Adapter(ABC):
    name: str
    host_patterns: list[str]
    requires_position_delay: bool = True

    @abstractmethod
    def iter_listing(self, url: str, fetch: "Fetcher", max_pages: int | None) -> Iterator[JobStub]:
        raise NotImplementedError

    @abstractmethod
    def parse_detail(self, html_or_json: str, url: str) -> JobAd:
        raise NotImplementedError

    def load_detail(self, stub: JobStub, fetch: "Fetcher") -> JobAd:
        """Load one job detail.

        Network-backed boards use the public detail URL by default. Sources
        that already return complete advertisements (ATS APIs and imports)
        override this method and return their cached normalized ``JobAd``.
        """
        detail = fetch.get(stub.url, cache_detail=True)
        return self.parse_detail(detail.text, stub.url)
