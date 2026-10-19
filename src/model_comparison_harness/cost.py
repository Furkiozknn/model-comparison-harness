"""Per-backend cost estimates from a price table in the comparison config.

"Which backend should this route to" is rarely only about speed: a model
twice as fast at ten times the price is a different answer. promptfoo shows
a cost per provider for LLM token pricing; the backends compared here are
often priced per image, per second of GPU time or per token, so the price
table is explicit and per backend:

.. code-block:: yaml

    backends:
      - name: flux-pro
        type: http
        url: https://api.example.test/generate
        cost:
          per_request: 0.04          # a flat price per call (one image)
      - name: gpu-endpoint
        type: gateway
        url: http://127.0.0.1:8000
        capability: generate-image
        cost:
          per_second: 0.00055        # billed by wall time of the call
      - name: llm
        type: http
        url: https://api.example.test/v1/chat
        cost:
          per_unit: 0.000002         # price per unit counted in the result
          unit_path: usage.total_tokens
          currency: USD

The estimate is ``per_request + per_second * latency + per_unit * units``,
for successful calls only. Whether a provider bills a failed call varies,
so an error row carries no cost rather than a guess. If ``unit_path`` does
not lead to a number in the result, the cost is unknown (``None``), never
silently computed without that part.

These are your numbers, not the provider's invoice: the latency term uses
the harness's measured wall time, which includes this process's overhead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

#: Keys a ``cost:`` mapping may have.
COST_FIELDS = ("per_request", "per_second", "per_unit", "unit_path", "currency")


class CostConfigError(ValueError):
    """A ``cost:`` mapping that cannot be used. ``config`` turns it into a
    ConfigError naming the backend."""


@dataclass(frozen=True)
class CostModel:
    per_request: float = 0.0
    per_second: float = 0.0
    per_unit: float = 0.0
    unit_path: Optional[str] = None
    currency: str = "USD"

    def estimate(self, result: Any, latency_seconds: float) -> Optional[float]:
        """The estimated cost of one successful call, or None if the unit
        count the price needs is not in ``result``."""
        total = self.per_request + self.per_second * latency_seconds
        if self.unit_path is not None:
            units = units_at(result, self.unit_path)
            if units is None:
                return None
            total += self.per_unit * units
        return total


def units_at(result: Any, path: str) -> Optional[float]:
    """Follow a dotted path (``usage.total_tokens``, ``images.0.megapixels``)
    into a JSON result. A numeric segment indexes a list. Returns the number
    found there, or None if the path is missing or does not end in a finite
    number (a bool does not count)."""
    node = result
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    if isinstance(node, bool) or not isinstance(node, (int, float)) or not math.isfinite(node):
        return None
    return float(node)


def parse_cost(spec: Any) -> CostModel:
    """Validate a ``cost:`` mapping from YAML."""
    if not isinstance(spec, dict):
        raise CostConfigError(f"'cost' must be a mapping, got {type(spec).__name__}")
    unknown = sorted(set(spec) - set(COST_FIELDS))
    if unknown:
        raise CostConfigError(
            f"'cost' has unknown field {unknown[0]!r} (allowed: {', '.join(COST_FIELDS)})"
        )
    prices: dict[str, float] = {}
    for key in ("per_request", "per_second", "per_unit"):
        value = spec.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise CostConfigError(f"'cost.{key}' must be a number >= 0, got {value!r}")
        prices[key] = float(value)
    unit_path = spec.get("unit_path")
    if unit_path is not None and (not isinstance(unit_path, str) or not unit_path.strip(".")):
        raise CostConfigError(f"'cost.unit_path' must be a dotted path such as usage.total_tokens, got {unit_path!r}")
    if prices["per_unit"] and unit_path is None:
        raise CostConfigError("'cost.per_unit' needs 'cost.unit_path' (where to count the units in the result)")
    if unit_path is not None and not prices["per_unit"]:
        raise CostConfigError("'cost.unit_path' needs 'cost.per_unit' (the price of one unit)")
    currency = spec.get("currency", "USD")
    if not isinstance(currency, str) or not currency.strip():
        raise CostConfigError(f"'cost.currency' must be a non-empty string, got {currency!r}")
    if not any(prices.values()):
        raise CostConfigError("'cost' sets no price: give per_request, per_second or per_unit")
    return CostModel(unit_path=unit_path, currency=currency.strip(), **prices)
