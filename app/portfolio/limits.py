"""Limites de riesgo, con sus invariantes verificadas en construccion.

Un limite mal formado -una fraccion de riesgo negativa, un tope de lotes cero-
no debe descubrirse cuando llega la primera orden. Se rechaza al construir la
politica, que es cuando todavia no hay dinero en juego.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.exceptions import InvariantViolation
from app.domain.value_objects.instrument import Instrument


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Politica de riesgo declarada. Ver `configs/risk.toml` para los motivos.

    Attributes:
        risk_fraction: Fraccion del capital ACTUAL arriesgada hasta el stop.
        min_stop_cost_multiple: Distancia minima al stop, en multiplos del coste
            de ida y vuelta del instrumento.
        absolute_min_stop_points: Suelo en puntos, para instrumentos que
            declaran coste cero. Sin el, un modelo de coste sin rellenar
            anularia el limite anterior en silencio.
        max_lots_per_order: Tope absoluto por orden. Cortafuegos frente a un
            error de dato que haya superado los demas controles.
        max_lots_per_symbol: Lotes abiertos mas los nuevos, por instrumento.
        max_open_positions: Posiciones simultaneas.
        max_margin_utilization: Fraccion del capital comprometida como margen.
    """

    risk_fraction: float = 0.01
    min_stop_cost_multiple: float = 3.0
    absolute_min_stop_points: float = 10.0
    max_lots_per_order: float = 10.0
    max_lots_per_symbol: float = 20.0
    max_open_positions: int = 5
    max_margin_utilization: float = 0.25

    def __post_init__(self) -> None:
        if not (0.0 < self.risk_fraction <= 1.0):
            raise InvariantViolation(
                "risk_fraction fuera de (0,1]", risk_fraction=self.risk_fraction
            )
        if self.min_stop_cost_multiple < 0.0:
            raise InvariantViolation("min_stop_cost_multiple no puede ser negativo")
        if self.absolute_min_stop_points <= 0.0:
            # Un suelo de cero deja el limite del stop a merced de que el modelo
            # de coste este relleno, que es como un limite deja de existir sin
            # que nadie borre una linea.
            raise InvariantViolation("absolute_min_stop_points debe ser positivo")
        if self.max_lots_per_order <= 0.0:
            raise InvariantViolation("max_lots_per_order debe ser positivo")
        if self.max_lots_per_symbol < self.max_lots_per_order:
            # Al reves, el tope por simbolo nunca podria activarse: el de orden
            # ya habria recortado antes, y el limite seria decorativo.
            raise InvariantViolation(
                "max_lots_per_symbol no puede ser menor que max_lots_per_order",
                per_symbol=self.max_lots_per_symbol,
                per_order=self.max_lots_per_order,
            )
        if self.max_open_positions < 1:
            raise InvariantViolation("max_open_positions debe ser al menos 1")
        if not (0.0 < self.max_margin_utilization <= 1.0):
            raise InvariantViolation("max_margin_utilization fuera de (0,1]")

    def min_stop_points_for(self, instrument: Instrument) -> float:
        """Distancia minima al stop admitida para este instrumento, en puntos.

        El coste de ida y vuelta es el spread -pagado una vez- mas el
        deslizamiento de entrada y de salida. Se toma el mayor entre el multiplo
        del coste y el suelo absoluto, de modo que ninguno de los dos pueda
        anular al otro.
        """
        costs = instrument.costs
        round_trip = costs.spread_points + 2.0 * costs.slippage_points
        return max(self.min_stop_cost_multiple * round_trip, self.absolute_min_stop_points)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_fraction": self.risk_fraction,
            "min_stop_cost_multiple": self.min_stop_cost_multiple,
            "absolute_min_stop_points": self.absolute_min_stop_points,
            "max_lots_per_order": self.max_lots_per_order,
            "max_lots_per_symbol": self.max_lots_per_symbol,
            "max_open_positions": self.max_open_positions,
            "max_margin_utilization": self.max_margin_utilization,
        }


__all__ = ["RiskLimits"]
