from __future__ import annotations

import hashlib

from PIL import Image
from pypdf import PdfReader

from jobfilter.pdfopt import optimised_path, upload_pdf


def test_large_scanned_pdf_is_cached_beside_the_unchanged_original(tmp_path):
    source = tmp_path / "Certificates.pdf"
    image = Image.effect_noise((2400, 3400), 110).convert("RGB")
    image.save(source, "PDF", quality=100, resolution=300)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    result = upload_pdf(source)

    assert result == optimised_path(source) and result.is_file()
    assert result.stat().st_size < source.stat().st_size
    optimized_page = PdfReader(result).pages[0]
    assert len(PdfReader(result).pages) == len(PdfReader(source).pages) == 1
    assert optimized_page.images and optimized_page.images[0].image.getbbox() is not None
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    timestamp = result.stat().st_mtime_ns
    assert upload_pdf(source).stat().st_mtime_ns == timestamp
