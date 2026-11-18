from __future__ import annotations

from pathlib import Path


TARGET_DPI = 150
JPEG_QUALITY = 60
TARGET_BYTES = 3_000_000


def optimised_path(source: str | Path) -> Path:
    source = Path(source)
    return source.with_name(f"{source.stem}_optimised{source.suffix}")


def optimise_pdf(
    source: str | Path,
    *,
    target: str | Path | None = None,
    dpi: int = TARGET_DPI,
    quality: int = JPEG_QUALITY,
    force: bool = False,
) -> Path:
    """Create a cached, upload-sized copy without ever changing the source PDF."""
    from PIL import Image
    from pypdf import PdfReader, PdfWriter

    source = Path(source)
    destination = Path(target) if target else optimised_path(source)
    if (
        not force
        and destination.is_file()
        and destination.stat().st_mtime >= source.stat().st_mtime
        and destination.stat().st_size <= TARGET_BYTES
    ):
        return destination

    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(source))
    replaced: set[tuple[int, int]] = set()
    for page in writer.pages:
        page_width = float(page.mediabox.width) / 72 * dpi
        page_height = float(page.mediabox.height) / 72 * dpi
        for image_file in page.images:
            reference = getattr(image_file, "indirect_reference", None)
            key = (getattr(reference, "idnum", id(image_file)), getattr(reference, "generation", 0))
            if key in replaced or reference is None:
                continue
            image = image_file.image
            scale = min(1.0, page_width / image.width, page_height / image.height)
            if scale < 1.0:
                image = image.resize(
                    (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                    Image.Resampling.LANCZOS,
                )
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image_file.replace(image, quality=quality)
            replaced.add(key)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp.pdf")
    with temporary.open("wb") as stream:
        writer.write(stream)
    temporary.replace(destination)
    return destination


def upload_pdf(source: str | Path) -> Path:
    """Use the cached optimized copy for large general PDFs; small files stay byte-identical."""
    source = Path(source)
    if source.suffix.casefold() != ".pdf" or source.stat().st_size <= TARGET_BYTES:
        return source
    return optimise_pdf(source)
