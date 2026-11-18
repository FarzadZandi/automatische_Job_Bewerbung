"""E-mail applications as drafts for the user's own mail program.

Some jobs can only be applied for by e-mail; on the Arbeitsagentur the address often appears only
after its CAPTCHA. jobfilter then writes a ready e-mail file with the PDFs attached and opens it.
The header ``X-Unsent: 1`` makes Outlook open the file as a new, unsent message: the user checks it
and presses Send. jobfilter never sends anything itself.
"""
from __future__ import annotations

import re
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlsplit

MAX_BYTES = 10_000_000  # many mail servers refuse bigger messages
EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")


def clean_address(value: object) -> str:
    address = str(value or "").strip().removeprefix("mailto:").split("?")[0].strip().strip("<>.,;:")
    return address if EMAIL.match(address) and not address.lower().endswith("arbeitsagentur.de") else ""


def _reference(manifest: Mapping[str, object]) -> str:
    url = str(manifest.get("url") or "")
    if not urlsplit(url).netloc.endswith("arbeitsagentur.de"):
        return ""
    return url.rstrip("/").rsplit("/", 1)[-1]


def draft_text(manifest: Mapping[str, object], profile: Mapping[str, str], with_certificates: bool) -> tuple[str, str]:
    """Subject and body in formal German, from the package and the applicant profile only."""
    title = " ".join(str(manifest.get("title") or "").split()) or "die ausgeschriebene Position"
    reference = _reference(manifest)
    where = f" (Referenznummer {reference} in der Jobbörse der Bundesagentur für Arbeit)" if reference else ""
    documents = "mein Anschreiben, meinen Lebenslauf und meine Zeugnisse" if with_certificates else "mein Anschreiben und meinen Lebenslauf"
    name = " ".join(part for part in (profile.get("first_name", ""), profile.get("last_name", "")) if part)
    signature = "\n".join(line for line in (name, profile.get("phone", ""), profile.get("email", "")) if line)
    body = (
        "Sehr geehrte Damen und Herren,\n\n"
        f"anbei sende ich Ihnen meine Bewerbung für die Position {title}{where}. "
        f"Im Anhang finden Sie {documents}.\n\n"
        "Für Rückfragen stehe ich Ihnen gern zur Verfügung.\n\n"
        f"Mit freundlichen Grüßen\n{signature}\n"
    )
    return f"Bewerbung als {title}", body


def build_draft(
    folder: str | Path, manifest: Mapping[str, object], profile: Mapping[str, str], to: str, *,
    attachments_dir: str | Path, order: Sequence[str] = (), attachment_names: Sequence[str] | None = None,
) -> tuple[Path, list[str]]:
    """Write the .eml draft next to the package. Returns its path and the certificates left out for size."""
    package = Path(folder)
    address = clean_address(to)
    if not address:
        raise ValueError(f"Not a usable e-mail address: {to}")
    person = f"{profile['first_name']}_{profile['last_name']}".replace(" ", "_")
    documents = [package / f"Anschreiben_{person}.pdf", package / f"Lebenslauf_{person}.pdf"]
    missing = [path.name for path in documents if not path.is_file()]
    if missing:
        raise ValueError(f"PDF missing: {', '.join(missing)}. Click 'Update PDFs and form data' first.")
    names = [str(name) for name in (attachment_names if attachment_names is not None else manifest.get("attachments", []))]
    names.sort(key=lambda name: list(order).index(name) if name in order else len(order))
    total = sum(path.stat().st_size for path in documents)
    certificates, left_out = [], []
    for name in names:
        source = Path(attachments_dir) / name
        if not source.is_file():
            source = package / "Attachements" / name
        if not source.is_file():
            continue
        if total + source.stat().st_size > MAX_BYTES:
            left_out.append(name)
            continue
        certificates.append(source)
        total += source.stat().st_size
    subject, body = draft_text(manifest, profile, bool(certificates))
    message = EmailMessage(policy=SMTP)
    message["To"] = address
    message["Subject"] = subject
    message["X-Unsent"] = "1"  # Outlook: open as a new message, ready to send
    message.set_content(body)
    for path in [*documents, *certificates]:
        message.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=path.name)
    target = package / f"Bewerbung_{person}.eml"
    target.write_bytes(message.as_bytes())
    return target, left_out
