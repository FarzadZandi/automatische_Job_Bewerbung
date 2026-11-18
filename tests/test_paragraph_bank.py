import json
from pathlib import Path

import pytest

from jobfilter.__main__ import main
from jobfilter.applications import MISSING_EVIDENCE
from jobfilter.paragraph_bank import FAMILIES, check_paragraph, check_paragraph_bank, word_count

ROOT = Path(__file__).parents[1]
GOOD_ONE = (
    "Bei einem Pharmaunternehmen habe ich die Absatz- und Preisprognose neu aufgebaut. Dazu habe ich "
    "Vertriebs-, Preis- und Wettbewerbsdaten zusammengeführt und konkurrierende Modelle gegeneinander "
    "validiert, darunter ARIMA, Random Forest, RNN und LSTM. Die Quartalsprognosegenauigkeit stieg um 20 %, "
    "und die Prognosen wurden zur verbindlichen Grundlage der Vertriebs- und Bestandsplanung, die seitdem "
    "auf diesen Zahlen aufbaut und regelmäßig überprüft wird. Die Ergebnisse habe ich der Leitung vorgestellt."
)
GOOD_TWO = (
    "An einem Lehrstuhl habe ich eine Python-basierte Web-Scraping-Pipeline mit Selenium und BeautifulSoup "
    "aufgebaut, die über 2.500 Profile extrahiert und bereinigt. Das Ergebnis ist ein proprietärer Datensatz, "
    "auf dem die Forschung des Lehrstuhls aufbaut. Für Analyse und Visualisierung arbeite ich mit pandas, "
    "NumPy, matplotlib und plotly sowie in R mit tidyverse und ggplot2, jeweils mit versioniertem Code "
    "und nachvollziehbarer Dokumentation für das gesamte Team."
)


def _write_bank(path, overrides=None):
    overrides = overrides or {}
    lines = []
    for family in FAMILIES:
        values = overrides.get(family, [MISSING_EVIDENCE, MISSING_EVIDENCE])
        lines.append(f"{family}:")
        lines.extend(f"  - {json.dumps(value, ensure_ascii=False)}" for value in values)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_good_paragraphs_pass_and_fehlt_is_reported_as_missing(tmp_path):
    assert 60 <= word_count(GOOD_ONE) <= 90 and 60 <= word_count(GOOD_TWO) <= 90
    bank = tmp_path / "bank.yaml"
    _write_bank(bank, {"B05": [GOOD_ONE, GOOD_TWO]})
    report = check_paragraph_bank(bank)
    assert report.errors == []
    assert report.complete == ["B05"]
    assert len(report.missing) == 9


@pytest.mark.parametrize(("text", "problem"), [
    ("Zu kurz.", "words"),
    (GOOD_ONE + " Ich bin hoch motiviert.", "banned phrase"),
    (GOOD_ONE.replace("ARIMA", "dbt"), "Grundkenntnisse"),
    (GOOD_ONE.replace("ARIMA", "Gurobi"), "Grundkenntnisse"),
    (GOOD_ONE.replace("validiert,", "validiert — "), "em dash"),
    (GOOD_ONE.replace("validiert,", "validiert –"), "en dash"),
    (GOOD_ONE.replace("validiert,", "validiert -"), "spaced hyphen"),
    (GOOD_ONE.replace("Die Quartals", "Meine Deutschkenntnisse sind fließend. Die Quartals"), "German level"),
])
def test_rule_violations_are_reported(text, problem):
    assert any(problem in message for message in check_paragraph("B05", 1, text))


def test_en_dash_inside_a_range_is_allowed():
    text = GOOD_ONE.replace("um 20 %", "im Zeitraum 01/2022 – 03/2024 um 20 %")
    assert not any("dash" in message for message in check_paragraph("B05", 1, text))


def test_structure_errors_and_duplicates(tmp_path):
    bank = tmp_path / "bank.yaml"
    _write_bank(bank, {"B05": [GOOD_ONE, GOOD_ONE], "B06": [GOOD_ONE, GOOD_TWO]})
    with bank.open("a", encoding="utf-8") as handle:
        handle.write("B11:\n  - x\n  - y\n")
    report = check_paragraph_bank(bank)
    assert any("Unknown keys: B11" in error for error in report.errors)
    assert any("B05: both paragraphs are identical" in error for error in report.errors)
    assert any("repeats" in warning for warning in report.warnings)


def test_cli_exit_codes(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text('contact_email: "test@example.com"\n', encoding="utf-8")
    bank = tmp_path / "bank.yaml"
    _write_bank(bank, {"B05": [GOOD_ONE, GOOD_TWO]})
    assert main(["--config", str(config), "check-paragraph-bank", "--file", str(bank)]) == 0
    assert main(["--config", str(config), "check-paragraph-bank", "--file", str(bank), "--require-complete"]) == 1
    _write_bank(bank, {"B05": [GOOD_ONE, "Zu kurz."]})
    assert main(["--config", str(config), "check-paragraph-bank", "--file", str(bank)]) == 1
    assert "ERROR" in capsys.readouterr().out


DRAFT = ROOT / "Job App Docs" / "anschreiben_bausteine_ENTWURF.yaml"


@pytest.mark.skipif(not DRAFT.exists(), reason="personal paragraph draft is not available")
def test_personal_draft_passes_mechanical_checks():
    report = check_paragraph_bank(DRAFT)
    assert report.errors == []
