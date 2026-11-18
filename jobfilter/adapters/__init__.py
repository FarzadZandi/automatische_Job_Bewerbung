"""Board-specific adapters and URL routing."""

from urllib.parse import urlsplit

from .arbeitsagentur import ArbeitsagenturAdapter
from .base import Adapter
from .company_ats import CompanyATSAdapter
from .imported_csv import ImportedCSVAdapter


ADAPTERS: tuple[type[Adapter], ...] = (ArbeitsagenturAdapter,)


def adapter_for_url(url: str) -> Adapter:
    parts = urlsplit(url)
    if parts.scheme == "company-ats":
        return CompanyATSAdapter(url)
    if parts.scheme == "job-import":
        return ImportedCSVAdapter(url)
    host = (parts.hostname or "").lower()
    for adapter_type in ADAPTERS:
        if any(host == pattern or host.endswith(f".{pattern}") for pattern in adapter_type.host_patterns):
            return adapter_type()
    supported = ", ".join(pattern for adapter in ADAPTERS for pattern in adapter.host_patterns)
    raise ValueError(f"Unsupported job-board host {host or '<missing>'}. Supported: {supported}")


__all__ = [
    "ADAPTERS",
    "ArbeitsagenturAdapter",
    "CompanyATSAdapter",
    "ImportedCSVAdapter",
    "adapter_for_url",
]

