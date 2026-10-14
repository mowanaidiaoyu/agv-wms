from __future__ import annotations

from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.algorithms.sku_heat.provider import sku_demand_provider
from app.api import sku_algorithm


app = FastAPI()
app.include_router(sku_algorithm.router, prefix="/api/v1")
client = TestClient(app)


def test_abc_api_runs_real_algorithm_and_labels_demo_source():
    response = client.get("/api/v1/algorithm/sku/abc")
    assert response.status_code == 200
    payload = response.json()
    assert payload["algorithm_version"] == "abc-v1.0.0"
    assert payload["data_source"] == "synthetic_demo"
    assert payload["as_of_date"] == sku_demand_provider.coverage_end.isoformat()
    assert len(payload["items"]) == sku_demand_provider.sku_count
    assert {item["abc_class"] for item in payload["items"]} <= {"A", "B", "C"}
    assert any(item["outbound_quantity"] > 0 for item in payload["items"])


def test_abc_api_validates_query_parameters():
    assert client.get("/api/v1/algorithm/sku/abc", params={"lookback_days": 0}).status_code == 422
    future = sku_demand_provider.coverage_end + timedelta(days=1)
    response = client.get("/api/v1/algorithm/sku/abc", params={"as_of_date": future.isoformat()})
    assert response.status_code == 422
    assert "coverage end" in response.json()["detail"]


def test_heat_api_returns_real_sma_contract_and_requested_order():
    as_of = sku_demand_provider.coverage_end - timedelta(days=3)
    response = client.post(
        "/api/v1/algorithm/sku/heat/predict",
        json={
            "sku_ids": ["SKU-TREND-UP", "SKU-SPARSE"],
            "as_of_date": as_of.isoformat(),
            "method": "moving_average",
            "lookback_days": 14,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["algorithm_version"] == "heat-baseline-v1.0.0"
    assert payload["data_source"] == "synthetic_demo"
    assert payload["method"] == "moving_average"
    assert payload["lookback_days"] == 14
    assert payload["forecast_date"] == (as_of + timedelta(days=1)).isoformat()
    assert [item["sku_id"] for item in payload["predictions"]] == ["SKU-TREND-UP", "SKU-SPARSE"]
    assert all(item["predicted_heat"] >= 0 for item in payload["predictions"])


def test_heat_api_supports_legacy_candidate_windows_and_wma():
    for lookback_days in (7, 14, 30):
        response = client.post(
            "/api/v1/algorithm/sku/heat/predict",
            json={
                "sku_ids": ["SKU-STABLE"],
                "method": "weighted_moving_average",
                "lookback_days": lookback_days,
            },
        )
        assert response.status_code == 200
        assert response.json()["lookback_days"] == lookback_days


def test_heat_api_stably_deduplicates_skus_and_is_deterministic():
    request = {"sku_ids": ["SKU-STABLE", "SKU-TREND-UP", "SKU-STABLE"]}
    first = client.post("/api/v1/algorithm/sku/heat/predict", json=request)
    second = client.post("/api/v1/algorithm/sku/heat/predict", json=request)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert [item["sku_id"] for item in first.json()["predictions"]] == ["SKU-STABLE", "SKU-TREND-UP"]


def test_heat_api_rejects_empty_unknown_future_and_invalid_method_inputs():
    path = "/api/v1/algorithm/sku/heat/predict"
    assert client.post(path, json={"sku_ids": []}).status_code == 422
    assert client.post(path, json={"sku_ids": ["SKU-UNKNOWN"]}).status_code == 422
    assert client.post(path, json={"sku_ids": ["SKU-STABLE"], "method": "lstm"}).status_code == 422
    future = sku_demand_provider.coverage_end + timedelta(days=1)
    assert client.post(path, json={"sku_ids": ["SKU-STABLE"], "as_of_date": future.isoformat()}).status_code == 422
    assert client.post(path, json={"sku_ids": ["SKU-STABLE"], "horizon_hours": 48}).status_code == 422


def test_heat_api_calls_migrated_forecast_not_a_mock_fallback(monkeypatch):
    called = False
    real_forecast = sku_algorithm.forecast_heat

    def recording_forecast(*args, **kwargs):
        nonlocal called
        called = True
        return real_forecast(*args, **kwargs)

    monkeypatch.setattr(sku_algorithm, "forecast_heat", recording_forecast)
    response = client.post("/api/v1/algorithm/sku/heat/predict", json={"sku_ids": ["SKU-STABLE"]})
    assert response.status_code == 200
    assert called is True
