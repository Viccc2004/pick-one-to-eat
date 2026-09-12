"""End-to-end through the web layer, no network."""

from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_empty_form_renders_without_a_result():
    response = client.get("/")

    assert response.status_code == 200
    assert "pick-one-to-eat" in response.text
    assert "show me another" not in response.text


def test_query_returns_exactly_one_restaurant():
    response = client.get("/", params={"q": "cheap chinese food in Allston"})

    assert response.status_code == 200
    # Corner Dumpling Counter wins: 4.7 beats Sichuan Garden House's 4.6.
    assert "Corner Dumpling Counter" in response.text
    assert "Sichuan Garden House" not in response.text


def test_rank_parameter_shows_the_next_candidate():
    response = client.get("/", params={"q": "chinese", "rank": 1})

    assert "Corner Dumpling Counter" not in response.text
    assert "Sichuan Garden House" in response.text


def test_out_of_range_rank_does_not_crash():
    response = client.get("/", params={"q": "chinese", "rank": 99})

    assert response.status_code == 200
