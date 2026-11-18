from __future__ import annotations
import csv, json, shutil, subprocess, sys
from pathlib import Path

from jobfilter.config import Config
from jobfilter.jdarchive import record_outcome
from jobfilter.osutil import app_data_dir, is_mac, is_windows
from jobfilter.scoring import family_codes, load_keywords
from jobfilter.scorefolder import score_folder

ROOT = Path(__file__).parents[1]

def test_three_family_dictionary_and_ten_family_snapshot():
    three=load_keywords(ROOT/"tests/fixtures/keywords_three_families.json")
    ten=load_keywords(ROOT/"tests/fixtures/keywords_example_analytics.json")
    assert family_codes(three)==("B01","B02","B03")
    assert family_codes(ten)==tuple(f"B{i:02d}" for i in range(1,11))

def test_jdarchive_full_text_idempotence_and_transition(tmp_path):
    job={"application_id":"abc","company":"Beispiel GmbH","title":"Analyst","url":"https://example.org/a","description":"Synthetic vacancy"}
    assert record_outcome(tmp_path,job,"skipped")
    record_outcome(tmp_path,job,"skipped")
    record_outcome(tmp_path,job,"applied")
    rows=list(csv.DictReader((tmp_path/"index.csv").open(encoding="utf-8-sig")))
    assert [r["status"] for r in rows]==["skipped","applied"]
    record_outcome(tmp_path,{**job,"application_id":"drop"},"dropped")
    assert len(list(tmp_path.rglob("*.md")))==1

def test_score_folder_and_doctor_json(tmp_path):
    keywords=load_keywords(ROOT/"tests/fixtures/keywords_three_families.json")
    out=tmp_path/"scores.csv";assert score_folder(ROOT/"templates/example_persona/vacancies",keywords,{"priority":12,"review":6},out)==12
    assert len(list(csv.DictReader(out.open(encoding="utf-8-sig"))))==12
    status=tmp_path/"status.json"
    result=subprocess.run([sys.executable,"-m","jobfilter","--config",str(tmp_path/"missing.yaml"),"doctor","--json",str(status)],cwd=ROOT)
    assert result.returncode in {0,1} and json.loads(status.read_text(encoding="utf-8"))["os"]

def test_osutil_matches_current_platform():
    assert is_windows() == sys.platform.startswith("win")
    assert is_mac() == (sys.platform == "darwin")
    assert app_data_dir().name == "jobfilter"

def test_mac_commands_have_valid_bash_syntax():
    bash = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if Path("C:/Program Files/Git/bin/bash.exe").exists() else None)
    if not bash: return
    for name in ("Start jobfilter (Mac).command","Check setup (Mac).command"):
        assert subprocess.run([bash,"-n",str(ROOT/name)]).returncode==0
