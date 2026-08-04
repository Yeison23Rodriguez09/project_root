"""Respuesta del caso de uso de backtest.

Reune en un solo objeto lo que sabe cada pieza -el catalogo, el motor, analytics
y el almacen- para que la interfaz no tenga que recomponerlo y para que el
resultado sea auditable sin volver a tocar el disco.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.types import RunId
from app.domain.value_objects.metrics import PerformanceMetrics


@dataclass(frozen=True, slots=True)
class BacktestReport:
    """Que se evaluo, que produjo y donde quedo la constancia.

    Attributes:
        run_id: Identidad de la corrida, derivada del contenido.
        strategy_id: Identidad de la composicion evaluada.
        dataset_fingerprint: Identidad de la serie.
        metrics: Resultado medido por `analytics`. Nunca se recalcula aqui.
        n_trades / final_equity: Duplican informacion de `metrics` y del motor a
            proposito: son lo que se imprime, y obligar a la interfaz a extraerlo
            de dos sitios distintos invitaria a que cada una lo hiciera a su
            manera.
        ambiguous_bars: Barras en que stop y objetivo cayeron los dos dentro del
            rango. No es una curiosidad: mide cuanto del resultado descansa en
            una suposicion que el dato no sostiene, y por eso viaja hasta arriba
            en lugar de quedarse en el motor.
        rejected_by_risk: Senales que la politica de riesgo no dejo dimensionar.
            Un numero alto significa que la estrategia y el riesgo no se hablan,
            y sin este campo el sintoma seria "opera menos de lo que deberia".
        artifacts: Rutas de lo escrito, en orden de escritura.
    """

    run_id: RunId
    strategy_id: str
    dataset_fingerprint: str
    metrics: PerformanceMetrics
    n_trades: int
    final_equity: float
    ambiguous_bars: int
    rejected_by_risk: int
    artifacts: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "strategy_id": self.strategy_id,
            "dataset_fingerprint": self.dataset_fingerprint,
            "n_trades": self.n_trades,
            "final_equity": self.final_equity,
            "ambiguous_bars": self.ambiguous_bars,
            "rejected_by_risk": self.rejected_by_risk,
            "metrics": self.metrics.to_dict(),
            "artifacts": list(self.artifacts),
        }


__all__ = ["BacktestReport"]
