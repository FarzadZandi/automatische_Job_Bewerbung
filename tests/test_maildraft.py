from __future__ import annotations

from email import policy
from email.parser import BytesParser

import pytest

from jobfilter import maildraft
from jobfilter.maildraft import build_draft, clean_address

PROFILE = {"first_name": "Max", "last_name": "Muster", "email": "m@example.org", "phone": "+49 151 000"}
MANIFEST = {
    "title": "Data Analyst (m/w/d)", "url": "https://www.arbeitsagentur.de/jobsuche/jobdetail/12345-ABC-S",
    "attachments": ["Gross.pdf", "Zeugnis.pdf", "Fehlt.pdf"],
}


def _package(tmp_path):
    folder = tmp_path / "package"
    folder.mkdir()
    for name in ("Anschreiben_Max_Muster.pdf", "Lebenslauf_Max_Muster.pdf"):
        (folder / name).write_bytes(b"%PDF " + name.encode())
    shared = tmp_path / "Attachements"
    shared.mkdir()
    (shared / "Zeugnis.pdf").write_bytes(b"%PDF zeugnis")
    (shared / "Gross.pdf").write_bytes(b"x" * 64)
    (shared / "Anhang_Max_Muster.pdf").write_bytes(b"%PDF anhang")
    (shared / "Passport, Visum, Aufenthaltstitel.pdf").write_bytes(b"%PDF restricted")
    return folder, shared


def test_addresses_are_cleaned_and_agency_addresses_refused():
    assert clean_address("mailto:Jobs@Firma.de?subject=Bewerbung") == "Jobs@Firma.de"
    assert clean_address(" <hr@firma.example>. ") == "hr@firma.example"
    assert clean_address("service@arbeitsagentur.de") == "" and clean_address("keine Adresse") == ""


def test_draft_is_an_unsent_message_with_the_pdfs_in_formal_german(tmp_path, monkeypatch):
    folder, shared = _package(tmp_path)
    monkeypatch.setattr(maildraft, "MAX_BYTES", 100)  # the big certificate does not fit any more
    path, left_out = build_draft(folder, MANIFEST, PROFILE, "jobs@firma.de", attachments_dir=shared, order=["Zeugnis.pdf"])
    message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    assert message["To"] == "jobs@firma.de" and message["X-Unsent"] == "1" and "From" not in message
    assert message["Subject"] == "Bewerbung als Data Analyst (m/w/d)"
    body = message.get_body(("plain",)).get_content()
    assert body.startswith("Sehr geehrte Damen und Herren,") and "Referenznummer 12345-ABC-S" in body
    assert "meine Zeugnisse" in body and body.rstrip().endswith("m@example.org")
    assert "\u2014" not in body and "\u2013" not in body
    names = [part.get_filename() for part in message.iter_attachments()]
    assert names == ["Anschreiben_Max_Muster.pdf", "Lebenslauf_Max_Muster.pdf", "Zeugnis.pdf"] and left_out == ["Gross.pdf"]


def test_draft_needs_the_pdfs_and_a_real_address(tmp_path):
    folder, shared = _package(tmp_path)
    with pytest.raises(ValueError, match="address"):
        build_draft(folder, MANIFEST, PROFILE, "service@arbeitsagentur.de", attachments_dir=shared)
    (folder / "Lebenslauf_Max_Muster.pdf").unlink()
    with pytest.raises(ValueError, match="PDF missing"):
        build_draft(folder, MANIFEST, PROFILE, "jobs@firma.de", attachments_dir=shared)


def test_draft_uses_current_general_bundle_and_never_the_restricted_residence_file(tmp_path):
    folder, shared = _package(tmp_path)
    path, left_out = build_draft(
        folder, MANIFEST, PROFILE, "jobs@firma.de", attachments_dir=shared,
        attachment_names=["Anhang_Max_Muster.pdf"],
    )
    message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    names = [part.get_filename() for part in message.iter_attachments()]
    assert names == ["Anschreiben_Max_Muster.pdf", "Lebenslauf_Max_Muster.pdf", "Anhang_Max_Muster.pdf"]
    assert "Passport, Visum, Aufenthaltstitel.pdf" not in names and left_out == []
