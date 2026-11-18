import pytest

from scripts.validate_company_sources import endpoint, row_count


def test_company_validation_builds_one_public_list_endpoint_per_ats():
    assert endpoint("greenhouse", "board").endswith("/board/jobs?content=true")
    assert endpoint("lever", "board").endswith("/board?mode=json")
    assert endpoint("ashby", "board").endswith("/board")
    assert endpoint("smartrecruiters", "board").endswith("/postings?limit=100&offset=0&country=de")


def test_company_validation_rejects_unexpected_payload_shapes():
    assert row_count("lever", [{"id": 1}]) == 1
    assert row_count("greenhouse", {"jobs": [{"id": 1}]}) == 1
    assert row_count("smartrecruiters", {"content": []}) == 0
    with pytest.raises(ValueError):
        row_count("lever", {"jobs": []})
