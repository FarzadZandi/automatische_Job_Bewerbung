from jobfilter.geography import enrich_location

HOME = (48.7758, 9.1829)

def test_resolves_german_state_and_distance_from_configured_home():
    local = enrich_location("Stuttgart, Neckar", HOME)
    assert local.federal_state == "Baden-Württemberg"
    assert local.distance_from_home_km < 2

    munich = enrich_location("München", HOME)
    assert munich.federal_state == "Bayern"
    assert 180 < munich.distance_from_home_km < 250

    hamburg = enrich_location("Hamburg", HOME)
    assert hamburg.federal_state == "Hamburg"
    assert 450 < hamburg.distance_from_home_km < 550


def test_uses_nearest_resolved_city_for_multi_location_jobs():
    result = enrich_location("Berlin; Stuttgart", HOME)
    assert result.federal_state == "Berlin; Baden-Württemberg"
    assert result.distance_from_home_km < 2


def test_generic_location_is_left_unresolved():
    result = enrich_location("verschiedene Arbeitsorte", HOME)
    assert result.federal_state == ""
    assert result.distance_from_home_km is None


def test_distance_uses_supplied_home_instead_of_a_module_constant():
    berlin = (52.5200, 13.4050)
    assert enrich_location("Berlin", berlin).distance_from_home_km < 2
    assert enrich_location("Berlin", HOME).distance_from_home_km > 400
