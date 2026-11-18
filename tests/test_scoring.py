import json
from pathlib import Path

import pytest

from jobfilter.scoring import load_keywords, normalize, score_text, title_gate


ROOT = Path(__file__).parents[1]
KEYWORDS = load_keywords(ROOT / "tests/fixtures/keywords_example_analytics.json")
CASES = json.loads((ROOT / "tests/fixtures/student_scoring_cases.json").read_text(encoding="utf-8"))
REVIEWED_TITLES = json.loads(
    (ROOT / "tests/fixtures/reviewed_priority_titles.json").read_text(encoding="utf-8")
)


def test_normalize_strips_html_decodes_and_transliterates_once():
    assert normalize("<p>Grüße&nbsp; &amp; Ökonomie</p><script>wrong</script>") == "gruesse & oekonomie"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_student_scoring_fixtures(case):
    result = score_text(case["title"], case["body"], KEYWORDS, employment_type=case.get("employment_type", ""))
    assert result.band == case["band"]
    if "drop_stage" in case:
        assert result.drop_stage == case["drop_stage"]
    if "reason" in case:
        assert result.reason == case["reason"]
    if "primary" in case:
        assert result.primary_cv_family.startswith(case["primary"])
    for flag in case.get("flags", []):
        assert flag in result.flags


def test_all_ten_families_are_defined_and_exercised():
    assert len(KEYWORDS["cv_families"]) == 10
    exercised = {case["primary"] for case in CASES if "primary" in case}
    assert exercised == {f"B{index:02d}" for index in range(1, 11)}


@pytest.mark.parametrize("case", REVIEWED_TITLES, ids=lambda case: case["title"])
def test_claude_reviewed_priority_titles_are_regression_cases(case):
    gate = title_gate(case["title"], KEYWORDS)
    if case["decision"] == "drop":
        assert gate is not None
        assert gate[0] == "DROP"
        assert gate[1] == case.get("reason", "excluded_senior_or_leadership_role")
    else:
        # Remaining proposals stay inactive until Max approves a new gate.
        assert gate is None


def test_structured_metadata_passes_without_title_format():
    result = score_text("Data Analytics", "KPI reporting and data analysis using Power BI.", KEYWORDS, employment_type="Praktikum")
    assert result.band in {"PRIORITY", "REVIEW"}
    assert result.employment_format == "PRAKTIKUM / INTERNSHIP"


def test_structured_full_time_is_scored_and_classified():
    result = score_text(
        "Data Analyst",
        "Business intelligence and KPI reporting using Power BI.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert result.band in {"PRIORITY", "REVIEW"}
    assert result.employment_format == "FULL-TIME / VOLLZEIT"


def test_explicit_doctoral_candidate_titles_are_separated():
    for title in ("Doktorand Data Analytics", "PhD Candidate Market Research"):
        result = score_text(
            title,
            "Data analytics, market research and KPI reporting.",
            KEYWORDS,
            employment_type="Vollzeit",
        )
        assert result.band == "ACADEMISCHE"
        assert result.drop_stage == ""
        assert result.reason == "academic_research_or_professorship_role"


@pytest.mark.parametrize(
    ("title", "body", "employer", "expected_band", "expected_reason", "expected_flags"),
    [
        (
            "Doktorand (m/w/d) Digitale Transformation im Mittelstand",
            "Kooperation mit TUM. Data analytics und digitale Transformation.",
            "Robert Bosch GmbH", "TOP", None, {"UNI PARTNER NAMED"},
        ),
        (
            "Industrial PhD - AI adoption in HR",
            "English working language. AI adoption in human resources.",
            "SAP SE", "TOP", None, set(),
        ),
        (
            "Doktorand Batteriezellchemie",
            "Forschung an Batteriezellen und Chemie.",
            "BMW AG", "DROP", "industry_phd_wrong_field", set(),
        ),
        (
            "Masterarbeit Innovationsmanagement",
            "Analyse von Innovation.",
            "Siemens AG", "DROP", "thesis_trainee_title", set(),
        ),
        (
            "PhD Candidate Supply Chain Analytics",
            "Supply chain analytics.",
            "Airbus Defence and Space", "DROP", "industry_phd_security_employer", set(),
        ),
        (
            "Wissenschaftliche/r Mitarbeiter/in (Promotion) Entrepreneurship",
            "Industry partners and entrepreneurship research.",
            "Universitaet Mannheim", "ACADEMISCHE", "academic_research_or_professorship_role", set(),
        ),
        (
            "Doktorand Change Management",
            "Betreuer an einer Hochschule selbst suchen. Change Management.",
            "mid-sized GmbH", "CHECK", None, {"SUPERVISOR SELF-FOUND"},
        ),
        (
            "Graduate Program Finance (Trainee)",
            "Finance analytics.",
            "Allianz", "DROP", "thesis_trainee_title", set(),
        ),
    ],
)
def test_industry_phd_specification_fixtures(
    title, body, employer, expected_band, expected_reason, expected_flags,
):
    result = score_text(title, body, KEYWORDS, employment_type="Vollzeit", employer=employer)
    assert result.band == expected_band
    assert result.reason == expected_reason
    assert expected_flags.issubset(result.flags)
    if expected_band in {"TOP", "CHECK"}:
        assert result.industry_trigger in {"strong_term", "company_signals", "employer_whitelist"}
    else:
        assert result.band in {"DROP", "ACADEMISCHE"}


def test_academic_doktorand_stays_in_academische_even_with_industry_partner():
    result = score_text(
        "Doktorand Digitale Transformation",
        "Kooperation with Bosch and other industry partners. Data analytics.",
        KEYWORDS,
        employment_type="Vollzeit",
        employer="Fraunhofer-Institut fuer System- und Innovationsforschung",
    )
    assert result.band == "ACADEMISCHE"
    assert result.industry_trigger == ""


def test_industry_phd_acronyms_require_token_boundaries():
    result = score_text(
        "Doktorand Change Management",
        "Change management research.",
        KEYWORDS,
        employment_type="Vollzeit",
        employer="Agentur Sehrgut",
    )
    assert result.band == "ACADEMISCHE"


def test_standalone_phd_title_routes_by_employer():
    company = score_text(
        "PhD - AI adoption in HR",
        "AI adoption and data analytics in human resources and corporate research.",
        KEYWORDS,
        employment_type="Vollzeit",
        employer="Robert Bosch GmbH",
    )
    university = score_text(
        "PhD - Digital Transformation",
        "Digital transformation research.",
        KEYWORDS,
        employment_type="Vollzeit",
        employer="University of Mannheim",
    )
    assert company.band == "TOP"
    assert company.industry_trigger in {"company_signals", "employer_whitelist"}
    assert university.band == "ACADEMISCHE"


def test_public_employer_is_not_industry_phd():
    result = score_text(
        "Wissenschaftlicher Mitarbeiter / Doktorand",
        "Promotion in Kooperation mit TUM. Change Management.",
        KEYWORDS,
        employment_type="Vollzeit",
        employer="Zweckverband Landeswasserversorgung",
    )
    assert result.band == "ACADEMISCHE"
    assert result.industry_trigger == ""


def test_industry_phd_flags_and_self_supervisor_penalty_are_exportable():
    result = score_text(
        "Industrial PhD Change Management",
        "Find your own supervisor. Enrolment required. Company tariff. "
        "German C1 and cooperation with RWTH.",
        KEYWORDS,
        employment_type="Vollzeit",
        employer="Example GmbH",
    )
    assert {"SUPERVISOR SELF-FOUND", "ENROLMENT REQUIRED", "INDUSTRY PAY", "UNI PARTNER NAMED"}.issubset(result.flags)
    assert "GERMAN C1+ / NATIVE-LEVEL" in result.language_flags
    assert "SUPERVISOR SELF-FOUND (-2)" in result.penalties
    assert result.university_partner == "rwth"


def test_relevant_non_doctoral_university_position_is_separated():
    result = score_text(
        "Wissenschaftlicher Mitarbeiter Market Research",
        "Market research, consumer insights and data analysis at a university institute.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert result.band == "ACADEMISCHE"
    assert result.reason == "academic_research_or_professorship_role"
    assert result.employment_format == "FULL-TIME / VOLLZEIT"


def test_body_only_employment_evidence_must_be_strong():
    weak = score_text("Data Analytics", "Student role with KPI reporting and Power BI.", KEYWORDS)
    strong = score_text("Data Analytics", "Join us as a working student for KPI reporting and data analysis with Power BI.", KEYWORDS)
    assert weak.reason == "employment_format_not_target"
    assert strong.band != "DROP"


def test_mixed_employment_format_keeps_and_flags_other_format():
    result = score_text("Werkstudent Data Analytics", "Vollzeit team; KPI reporting and Power BI data analysis.", KEYWORDS)
    assert result.band != "DROP"
    assert result.employment_format == "WERKSTUDENT / WORKING STUDENT"
    assert any("vollzeit" in flag.lower() for flag in result.other_flags)


def test_family_overlap_uses_only_best_family_contribution():
    result = score_text("Werkstudent Digital Analytics Data Analytics", "GA4 funnel conversion attribution plus KPI reporting and Power BI data analysis.", KEYWORDS)
    assert result.primary_cv_family.startswith("B09")
    assert result.family_scores["B06"] > 0 and result.family_scores["B09"] > 0
    assert result.score < 50


def test_keyword_dictionary_is_dynamic_not_hardcoded(tmp_path):
    modified = json.loads((ROOT / "tests/fixtures/keywords_example_analytics.json").read_text(encoding="utf-8"))
    modified["cv_families"]["B01_forschung_unternehmen_institute"]["strong_terms"].append("quuxresearch")
    path = tmp_path / "keywords.json"
    path.write_text(json.dumps(modified), encoding="utf-8")
    result = score_text("Werkstudent Spezialrolle", "quuxresearch and data analysis", load_keywords(path))
    assert result.band != "DROP"
    assert result.primary_cv_family.startswith("B01")


def test_relevant_high_german_goes_to_de_required():
    for phrase in (
        "sehr gute Deutschkenntnisse",
        "Deutsch C1",
        "Deutschkenntnisse C2",
        "verhandlungssicheres Deutsch",
        "fließende Deutsch- und Englischkenntnisse",
        "hervorragenden Deutsch- und Englischkenntnissen",
        "sehr gut auf Deutsch",
        "fluent in German",
        "Deutsch und Englisch fließend",
        "German and English fluency",
    ):
        result = score_text("Werkstudent Market Research", f"Market research and data analysis. {phrase}.", KEYWORDS)
        assert result.band == "DE REQUIRED"
        assert result.drop_stage == ""
        assert result.reason == "german_c1_c2_or_native_required"


def test_strong_family_passes_without_analysis_in_recall_first_mode():
    german_context = score_text("Werkstudent Corporate Strategy", "Corporate strategy projects.", KEYWORDS)
    english_context = score_text(
        "Werkstudent Corporate Strategy",
        "Corporate strategy projects. English is the working language.",
        KEYWORDS,
    )
    assert german_context.band != "DROP"
    assert english_context.band != "DROP"


def test_isolated_english_requirement_does_not_rescue_a_german_ad():
    result = score_text(
        "Werkstudent Spezialrolle",
        "Du arbeitest in unserem Team an Strategie und unterstuetzt die Kollegen. "
        "Wir erwarten sehr gute Englischkenntnisse und bieten dir spannende Aufgaben im Unternehmen.",
        KEYWORDS,
    )
    assert result.band == "REVIEW"
    assert result.reason == "uncertain_relevance_requires_manual_review"


def test_uncertain_academic_research_goes_to_academische():
    result = score_text(
        "Research Associate Spezialrolle",
        "Administrative Unterstützung ohne eindeutige the configured CV families-Aufgaben.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert result.band == "ACADEMISCHE"
    assert result.reason == "academic_research_or_professorship_role"


def test_substantially_english_ad_can_pass_without_analytical_task_phrase():
    result = score_text(
        "Working Student Corporate Strategy",
        "You will work with our international strategy team and support projects for our business. "
        "The role gives you experience with senior colleagues and our products. You are part of the team "
        "and will help with planning for the company. Your skills and experience are important for this role.",
        KEYWORDS,
    )
    assert result.band != "DROP"
    assert "ENGLISH-FRIENDLY" in result.flags


def test_weak_family_plus_analysis_passes_in_recall_first_mode():
    result = score_text("Werkstudent Spezialrolle", "KPI analysis and reporting.", KEYWORDS)
    assert result.band != "DROP"


def test_relevant_hr_strategy_and_people_analytics_are_maintained():
    result = score_text(
        "Werkstudent HR Strategy",
        "People strategy and workforce planning with KPI analysis.",
        KEYWORDS,
    )
    assert result.band != "DROP"
    assert result.primary_cv_family.startswith(("B02", "B06"))


@pytest.mark.parametrize(
    ("title", "body", "family"),
    [
        ("Praktikum Personalentwicklung", "HR analytics und Auswertung von Personalkennzahlen.", "B02"),
        ("Werkstudent Controlling", "Financial controlling, Reporting und Abweichungsanalyse.", "B06"),
        ("Werkstudent Risk Management", "Risikomanagement und Bewertung von Risiken.", "B06"),
        ("Werkstudent Operational Excellence", "Operational Excellence und Prozessanalyse.", "B10"),
        ("Student Research Assistant", "Survey methodology and literature research for questionnaire evaluation.", "B01"),
    ],
)
def test_semantic_analytics_equivalents_pass(title, body, family):
    result = score_text(title, body, KEYWORDS)
    assert result.band != "DROP"
    assert result.primary_cv_family.startswith(family)


def test_short_terms_use_word_boundaries():
    result = score_text(
        "Werkstudent Softwareentwickler",
        "Digital product implementation and semiconductors with Python.",
        KEYWORDS,
    )
    assert "git" not in result.matched_terms.get("tools_strong", ())
    assert "sem" not in result.matched_terms.get("B09.weak", ())


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Bachelorarbeit Innovationsmanagement", "THESIS / STUDENT PROJECT"),
        ("Student Research Assistant Survey Methods", "STUDENT ASSISTANT"),
        ("Praktikantin Data Analytics", "PRAKTIKUM / INTERNSHIP"),
    ],
)
def test_student_titles_override_structured_full_time_routing(title, expected):
    result = score_text(title, "Data analytics and data analysis.", KEYWORDS, employment_type="Vollzeit")
    assert result.employment_format == expected


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Praktikant Personalentwicklung", "HR-Auswertungen, Reports und Analyse relevanter KPIs."),
        ("Praktikant Immobilien", "Erstellung von Markt- und Standortanalysen."),
        ("Werkstudent Customer Experience", "Datenaufbereitung für Kundenbefragungen."),
        ("Praktikant Strategic Design", "Monatliche HR-Auswertungen und strategische Insights."),
        ("Praktikant Intralogistikplanung", "Analyse und Optimierung logistischer Prozesse."),
        ("Praktikant Logistic Engineering", "Analyse technischer Anforderungen und digitale Prozessunterstützung."),
        ("Praktikant Marketing", "Auswertung von Marketingmaßnahmen und datenbasiertes Marketing."),
        ("Praktikant Online-Marketing SEM", "Fundierte Recherchen und Analysen für strategische Entscheidungen."),
        ("Werkstudent Qualitätsmanagement", "Aufbau von Datenbanken und Dashboards im Verbesserungsprozess."),
        ("Working Student Controlling", "Cloud revenue and backlog controlling."),
        ("Praktikant Projektleitung Qualität", "Qualitätsanalyse und Qualitätsreporting."),
        ("Praktikant Qualität und Logistik", "Implementierung neuer Prozesse und Analyse von Reklamationen."),
        ("Pflichtpraktikum Projektmanagement", "Prozessoptimierungen und Überwachung von Projektfortschritten."),
        ("Werkstudent Product Compliance Datenmanagement", "Definition von Datenstrukturen und Überwachung von Datenqualitätskennzahlen."),
    ],
)
def test_recall_first_cases_from_latest_workbook_audit(title, body):
    result = score_text(title, body, KEYWORDS, employment_type="Praktikum")
    assert result.band != "DROP"


@pytest.mark.parametrize(
    "title",
    ["Masterarbeit Innovationsmanagement", "Graduate Program Finance (Trainee)", "PreMaster Data Engineer"],
)
def test_thesis_and_trainee_titles_are_excluded_before_scoring(title):
    result = score_text(title, "Data analytics and reporting.", KEYWORDS, employment_type="Vollzeit")
    assert result.band == "DROP"
    assert result.drop_stage == "title_exclusion"
    assert result.reason == "thesis_trainee_title"


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Working Student - Strategic Finance", "Support the finance team with recurring reports."),
        ("Working Student - S4/HANA Public Cloud Revenue Recognition", "Role details are hosted externally."),
        ("Werkstudent After Sales und Business Applica", "Role details are hosted externally."),
        ("Werkstudent AI Engineering", "Du evaluierst LLMs und automatisierst Entwicklungsaufgaben."),
        ("Praktikant Leben Geschäftsprozesse", "Du entwickelst Daten, Prozesse und Schnittstellen weiter."),
        ("Praktikant Operations Customer Products & Implementation", "Transformation mit Data-diven-Mindset."),
        ("Werkstudent Personalentwicklung", "Sie werten Feedbacks zu Entwicklungsmaßnahmen aus."),
    ],
)
def test_false_negative_cases_from_2026_09_06_audit(title, body):
    result = score_text(title, body, KEYWORDS, employment_type="Praktikum")
    assert result.band != "DROP"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("PreMaster Programm – Data Engineer", "GRADUATE / TRAINEE"),
        ("Praktikkant*in Personalentwicklung", "PRAKTIKUM / INTERNSHIP"),
        ("Freiwilligendienst im FSJ / BFD", "OTHER / EARLY CAREER"),
        ("Rechtsreferendar Wahlstation", "OTHER / EARLY CAREER"),
    ],
)
def test_early_career_titles_are_routed_out_of_vollzeit(title, expected):
    result = score_text(title, "Data analytics and reporting.", KEYWORDS, employment_type="Vollzeit")
    assert result.employment_format == expected


def test_einkauf_seller_and_sales_titles_are_hard_excluded():
    for title in (
        "Werkstudent Einkauf",
        "Praktikum Verkäufer",
        "Werkstudent Vertrieb",
        "Account Manager",
        "Sales Manger",
        "Manager Energiebeschaffung",
        "Outbound Kampagnenmanagement",
    ):
        result = score_text(title, "Market research and data analysis.", KEYWORDS, employment_type="Vollzeit")
        assert result.drop_stage == "commercial_role_gate"


def test_talking_marketing_drops_but_research_marketing_passes():
    talking = score_text(
        "Praktikum Marketing",
        "Du führst Kundengespräche und übernimmst telefonische Ansprache.",
        KEYWORDS,
    )
    research = score_text(
        "Praktikum Marketing Analytics",
        "Market research, consumer insights and campaign data analysis.",
        KEYWORDS,
    )
    assert talking.drop_stage == "marketing_talking_gate"
    assert research.band != "DROP"


def test_marketing_events_without_research_or_analytics_is_dropped():
    result = score_text(
        "Praktikant Marketing und Events",
        "Unterstützung bei Events, Gästebetreuung und organisatorischen Aufgaben.",
        KEYWORDS,
    )
    assert result.drop_stage == "marketing_talking_gate"


def test_relevant_consulting_with_strong_german_routes_to_de_required():
    english = score_text(
        "Analytics Consultant Customer Service",
        "You will work with our team on analytics consulting projects and process transformation. "
        "English is the working language and German is not required.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    german = score_text(
        "Analytics Consultant Customer Service",
        "Analytics consulting and process transformation. Sehr gute Deutschkenntnisse erforderlich.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert english.band != "DROP"
    assert english.primary_cv_family.startswith("B08")
    assert german.band == "DE REQUIRED"
    assert german.drop_stage == ""
    assert german.reason == "german_c1_c2_or_native_required"


@pytest.mark.parametrize(
    ("title", "expected_band", "expected_stage"),
    [
        ("Professor für Digital Business", "ACADEMISCHE", ""),
        ("Wissenschaftlicher Mitarbeiter Data Analytics", "ACADEMISCHE", ""),
        ("Wissenschaftliche/r Mitarbeiter/in Market Research", "ACADEMISCHE", ""),
        ("Akademische*r Mitarbeiter*in Data Analytics", "ACADEMISCHE", ""),
        ("Wissenschaftliche*r Mitarbeiter*in Fertigungstechnik", "ACADEMISCHE", ""),
        ("Researcher in computational superconductivity", "ACADEMISCHE", ""),
        ("Senior Business Intelligence Analyst", "DROP", "seniority_gate"),
        ("Head of Market Intelligence", "DROP", "seniority_gate"),
        ("Research Group Leader Data Science", "DROP", "seniority_gate"),
        ("Teamleitung Data Analytics", "DROP", "seniority_gate"),
        ("Teamleiterin Market Intelligence", "DROP", "seniority_gate"),
        ("Team Lead Business Intelligence", "DROP", "seniority_gate"),
        ("Gruppenleitung Research", "DROP", "seniority_gate"),
        ("Bereichsleitung Strategie", "DROP", "seniority_gate"),
        ("Software Engineer Data Platform", "DROP", "pure_it_title_gate"),
        ("IT Support / Systemadministrator", "DROP", "pure_it_title_gate"),
        ("SAP ABAP Entwickler", "DROP", "pure_it_title_gate"),
        ("SAP FI/CO Consultant", "DROP", "pure_it_title_gate"),
        ("SAP GTS Consultant", "DROP", "pure_it_title_gate"),
        ("Junior ERP-Consultant", "DROP", "pure_it_title_gate"),
        ("IT-Berater:in", "DROP", "pure_it_title_gate"),
        ("Technischer Berater M365", "DROP", "pure_it_title_gate"),
        ("Business Process Automation Consultant", "DROP", "pure_it_title_gate"),
    ],
)
def test_deterministic_title_gates_cannot_be_rescued_by_body(title, expected_band, expected_stage):
    result = score_text(
        title,
        "Business intelligence, strategy, market research, KPI analysis and Power BI dashboards.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert result.band == expected_band
    assert result.drop_stage == expected_stage


def test_high_score_without_target_title_evidence_stays_in_review():
    result = score_text(
        "Werkstudent Spezialrolle",
        "Data analytics, business intelligence, market research, strategy, forecasting, KPI reporting and Power BI.",
        KEYWORDS,
    )
    assert result.score >= 12
    assert result.band == "REVIEW"
    assert result.reason == "priority_requires_target_title_evidence"


def test_target_title_with_high_german_is_de_required_after_fit_scoring():
    result = score_text(
        "Werkstudent Business Intelligence",
        "KPI analysis and Power BI. Verhandlungssicheres Deutsch ist erforderlich.",
        KEYWORDS,
    )
    assert result.band == "DE REQUIRED"
    assert result.drop_stage == ""


@pytest.mark.parametrize(
    "title",
    [
        "Kindheitspädagoge (m/w/d)",
        "Praxisanleiter Pflege (m/w/d)",
        "Callcenter Kundenbetreuer (m/w/d)",
        "IT Support / Systemadministrator (m/w/d)",
        "Operations Manager Logistik (m/w/d)",
        "Projektingenieur Verkehrsanlagen (m/w/d)",
        "Personalsachbearbeiter (m/w/d)",
    ],
)
def test_obvious_non_target_titles_drop_without_analytical_rescue(title):
    result = score_text(title, "Allgemeine operative Aufgaben und Teamarbeit.", KEYWORDS, employment_type="Vollzeit")
    expected = "pure_it_title_gate" if title.startswith("IT Support") else "wrong_profession"
    assert result.drop_stage == expected


def test_title_exclusion_is_rescued_by_strong_analytics_body():
    result = score_text(
        "Projektingenieur Qualitätsmanagement",
        "Business intelligence, quality data analytics, root cause analysis and KPI dashboards.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert result.band != "DROP"
    assert result.primary_cv_family.startswith("B06")


def test_plain_ausbildung_title_is_not_accepted_via_structured_full_time():
    result = score_text(
        "Ausbildung zum Fachinformatiker",
        "Allgemeine Ausbildungsinhalte.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert result.drop_stage == "employment_gate"


def test_structured_full_time_is_not_misrouted_by_body_internship_wording():
    result = score_text(
        "Data Analyst",
        "Data analytics and KPI reporting. We also cooperate with internship programs.",
        KEYWORDS,
        employment_type="Vollzeit",
    )
    assert result.employment_format == "FULL-TIME / VOLLZEIT"


@pytest.mark.parametrize(("title", "dropped"), [
    ("Referent (m/w/d) der IT-Leitung für KI & Digitalisierung", False),
    ("Assistenz der Kaufmännischen Leitung (m/w/d)", False),
    ("Assistentin / Assistent (m/w/d) der Abteilungsleitung Informationstechnologie", False),
    ("Kaufmännische Leitung (m/w/d)", True),
    ("Leitung Marketing (m/w/d)", True),
    ("Leiter:in Unterstützungsprozesse (w/m/d)", True),
    ("Stellvertretende Leitung Marketing (m/w/d)", True),
    ("Group Controller (m/w/d)", False),
])
def test_staff_roles_supporting_a_manager_are_not_seniority_drops(title, dropped):
    gate = title_gate(title, KEYWORDS)
    is_seniority_drop = gate is not None and gate[1] == "excluded_senior_or_leadership_role"
    assert is_seniority_drop is dropped


@pytest.mark.parametrize(("title", "dropped"), [
    ("Anwendungstechniker (m/w/d) Fußbodentechnik", True),
    ("Technischer Berater/Support (m/w/d)", True),
    ("ERP Application Specialist (m/w/d) - Microsoft Dynamics 365 Business Central & Power BI", True),
    ("Supportmitarbeiter (m/w/d) ERP-Lösungen", True),
    ("IT-Support Engineer (m/w/d) – ERP-Fokus", True),
    ("Projektmanager Anwendungstechnik (m/w/d)", False),
    ("Business Analyst ERP (m/w/d)", False),
    ("Inhouse Consultant ERP (m/w/d)", False),
    ("Sachbearbeitung (m/w/d) im Bereich Altenhilfeplanung", False),
])
def test_technical_support_gate_activated_on_2026_11_13(title, dropped):
    gate = title_gate(title, KEYWORDS)
    assert (gate is not None and gate[1] == "excluded_technical_support_role") is dropped


def test_technical_support_drop_reports_its_own_stage():
    result = score_text("Anwendungstechniker (m/w/d)", "Beratung von Kunden zu Produkten.", KEYWORDS, employment_type="Vollzeit")
    assert result.band == "DROP"
    assert result.drop_stage == "technical_support_gate"
