"""Price tables: `cost:` per backend, the cost column, cheapest backend."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from model_comparison_harness.config import ConfigError, load_backends_from_dict
from model_comparison_harness.cli import main
from model_comparison_harness.cost import CostModel, parse_cost, units_at
from model_comparison_harness.runner import run_comparison

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_estimate_adds_the_three_terms():
    model = CostModel(per_request=0.01, per_second=0.5, per_unit=0.001, unit_path="usage.tokens")
    assert model.estimate({"usage": {"tokens": 100}}, 2.0) == pytest.approx(0.01 + 1.0 + 0.1)


def test_a_missing_unit_count_makes_the_cost_unknown_not_partial():
    model = CostModel(per_request=0.01, per_unit=0.001, unit_path="usage.tokens")
    assert model.estimate({"usage": {}}, 1.0) is None
    assert model.estimate({"usage": {"tokens": "100"}}, 1.0) is None
    assert model.estimate({"usage": {"tokens": True}}, 1.0) is None


def test_units_at_follows_dicts_and_list_indexes():
    result = {"images": [{"mp": 1.5}, {"mp": 2.0}], "n": 3}
    assert units_at(result, "images.1.mp") == 2.0
    assert units_at(result, "n") == 3.0
    assert units_at(result, "images.9.mp") is None
    assert units_at(result, "nope") is None
    assert units_at(None, "n") is None


@pytest.mark.parametrize(
    "spec, message",
    [
        ("0.04", "must be a mapping"),
        ({"per_requset": 0.04}, "unknown field 'per_requset'"),
        ({"per_request": -1}, "per_request' must be a number >= 0"),
        ({"per_request": True}, "per_request' must be a number >= 0"),
        ({"per_unit": 0.1}, "needs 'cost.unit_path'"),
        ({"unit_path": "usage.tokens"}, "needs 'cost.per_unit'"),
        ({}, "sets no price"),
        ({"per_request": 1, "currency": ""}, "currency"),
    ],
)
def test_parse_cost_rejects_bad_tables(spec, message):
    with pytest.raises(ValueError, match=message):
        parse_cost(spec)


def test_config_attaches_the_price_table_and_names_the_backend_on_error():
    (backend,) = load_backends_from_dict(
        {"backends": [{"name": "a", "type": "mock", "cost": {"per_request": 0.04, "currency": "EUR"}}]}
    )
    assert backend.cost == CostModel(per_request=0.04, currency="EUR")
    with pytest.raises(ConfigError, match=r"backend 'b' \(type=mock\).*per_request"):
        load_backends_from_dict({"backends": [{"name": "b", "type": "mock", "cost": {"per_request": "x"}}]})


def test_a_typo_next_to_cost_still_gets_a_did_you_mean():
    with pytest.raises(ConfigError, match="did you mean 'cost'"):
        load_backends_from_dict({"backends": [{"name": "a", "type": "mock", "cots": {}}]})


async def test_run_comparison_prices_successes_only():
    backends = load_backends_from_dict(
        {
            "backends": [
                {"name": "ok", "type": "mock", "delay": 0, "cost": {"per_request": 0.04}},
                {"name": "down", "type": "mock", "delay": 0, "should_fail": True, "cost": {"per_request": 0.04}},
                {"name": "free", "type": "mock", "delay": 0},
            ]
        }
    )
    ok, down, free = await run_comparison(backends, {"x": 1})
    assert ok.cost == pytest.approx(0.04) and ok.cost_currency == "USD"
    assert down.cost is None and down.cost_currency is None
    assert free.cost is None and free.cost_currency is None


def _cli(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["mch", *argv])
    main()


def test_cli_table_shows_cost_and_the_cheapest_backend(monkeypatch, capsys):
    _cli(monkeypatch, "run", str(EXAMPLES / "compare-with-prices.yaml"), "--input", '{"prompt": "x"}')
    out = capsys.readouterr().out
    header = out.splitlines()[0].split()
    assert header[:5] == ["backend", "status", "latency", "(s)", "cost"]
    per_image = next(l for l in out.splitlines() if l.startswith("per-image"))
    per_token = next(l for l in out.splitlines() if l.startswith("per-token"))
    assert "0.0400 USD" in per_image
    assert "0.002500 USD" in per_token  # 1250 tokens x 0.000002
    assert "cheapest successful backend: gpu-seconds" in out  # ~0.6 s x 0.0011


def test_cli_without_price_tables_keeps_the_old_table(monkeypatch, capsys):
    _cli(monkeypatch, "run", str(EXAMPLES / "compare-mocks.yaml"), "--input", '{"prompt": "x"}')
    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == ["backend", "status", "latency", "(s)", "summary"]
    assert "cheapest" not in out


def test_cli_json_and_csv_carry_cost_fields(monkeypatch, capsys):
    config = str(EXAMPLES / "compare-with-prices.yaml")
    _cli(monkeypatch, "run", config, "--input", '{"prompt": "x"}', "--json")
    rows = json.loads(capsys.readouterr().out)
    per_image = next(r for r in rows if r["backend"] == "per-image")
    assert per_image["cost"] == pytest.approx(0.04) and per_image["cost_currency"] == "USD"

    _cli(monkeypatch, "run", config, "--input", '{"prompt": "x"}', "--csv")
    csv_rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert float(next(r for r in csv_rows if r["backend"] == "per-image")["cost"]) == pytest.approx(0.04)


def test_cli_does_not_name_a_cheapest_backend_across_currencies(tmp_path, monkeypatch, capsys):
    config = tmp_path / "c.yaml"
    config.write_text(
        "backends:\n"
        "  - {name: a, type: mock, delay: 0, cost: {per_request: 1, currency: USD}}\n"
        "  - {name: b, type: mock, delay: 0, cost: {per_request: 1, currency: EUR}}\n",
        encoding="utf-8",
    )
    _cli(monkeypatch, "run", str(config), "--input", "{}")
    assert "not compared (the price tables use different currencies)" in capsys.readouterr().out


def test_cli_marks_an_unknown_unit_count(tmp_path, monkeypatch, capsys):
    config = tmp_path / "c.yaml"
    config.write_text(
        "backends:\n"
        "  - {name: a, type: mock, delay: 0, cost: {per_unit: 0.1, unit_path: usage.tokens}}\n",
        encoding="utf-8",
    )
    _cli(monkeypatch, "run", str(config), "--input", "{}")
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("a "))
    assert " ? " in line
