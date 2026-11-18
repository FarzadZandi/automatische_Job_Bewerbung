from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


def pytest_configure(config) -> None:
    """Keep all pytest scratch data outside the OneDrive project tree."""
    if config.option.basetemp is None:
        config.option.basetemp = Path(tempfile.gettempdir()) / "jobfilter-tests"


@pytest.fixture(autouse=True)
def stop_jobfilter_app_workers(monkeypatch):
    """No app-owned writer thread may escape the test that created it."""
    from jobfilter.app import App

    instances = []
    original = App.__init__

    def tracked(self, *args, **kwargs):
        original(self, *args, **kwargs)
        instances.append(self)

    monkeypatch.setattr(App, "__init__", tracked)
    yield
    for app in reversed(instances):
        app.stop()


EXAMPLE_TIER2 = ["dbt", "star schema", "analytics engineering", "pyomo", "gurobi", "simpy",
                 "regression discontinuity", "rdd", "synthetic control", "dowhy", "econml"]


@pytest.fixture(autouse=True)
def _example_applicant():
    """Tests run as the fictional example applicant: German B2, English C1, example Tier 2 list."""
    from jobfilter import applicant
    applicant.configure(
        {"german_level": "B2", "english_level": "C1",
         "language_statement_de": "Deutsch spreche ich aktiv auf B2-Niveau und baue es kontinuierlich aus; Englisch beherrsche ich auf C1-Niveau."},
        {"tier2_skills": EXAMPLE_TIER2},
    )
    yield
    applicant.configure({}, {})


@pytest.fixture(autouse=True)
def _example_keywords(monkeypatch):
    """Code paths that fall back to the package keywords.json use the anonymised example dictionary in tests."""
    from pathlib import Path
    import jobfilter.export
    monkeypatch.setattr(jobfilter.export, "DEFAULT_KEYWORDS_PATH",
                        Path(__file__).parent / "fixtures" / "keywords_example_analytics.json")
