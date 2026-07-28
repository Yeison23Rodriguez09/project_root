"""Objeto de valor de metricas de rendimiento.

Se define en el dominio aunque el calculo viva en `analytics` porque es un
contrato entre capas: backtest lo produce, walk-forward lo agrega, discovery lo
ordena y la politica de promocion lo evalua. Fijar la forma en el dominio evita
que cada motor invente su propio diccionario y que las comparaciones entre
fases dejen de ser validas.

Regla de honestidad: todas las metricas se calculan sobre PnL **neto**. No
existe una version bruta en este objeto para que sea imposible presentar por
descuido un resultado sin costes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.exceptions import InvariantViolation

#: Numero minimo de operaciones para que una metrica admita lectura
#: estadistica. Por debajo, el resultado se marca como degenerado y no compite
#: en los rankings de discovery.
MIN_TRADES_FOR_INFERENCE: int = 30


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """Resumen cuantitativo de una serie de operaciones.

    Attributes:
        n_trades: Numero de operaciones cerradas. Metrica de contexto
            obligatoria: un Sharpe de 3.0 con 7 operaciones no es informacion.
        net_profit: Suma de resultados netos.
        gross_profit / gross_loss: Suma de ganancias y de perdidas. `gross_loss`
            se expresa con signo negativo o cero.
        profit_factor: `gross_profit / |gross_loss|`. `None` si no hay perdidas,
            para no propagar infinitos a los artefactos JSON ni a los rankings.
        win_rate: Proporcion de operaciones ganadoras en [0, 1].
        expectancy: Resultado neto medio por operacion.
        max_drawdown: Caida maxima de la curva de equity, en moneda de cuenta,
            expresada como numero positivo.
        max_drawdown_pct: La misma caida en fraccion del pico previo.
        sharpe: Ratio de Sharpe anualizado sobre retornos por barra.
        sortino: Variante que penaliza solo la desviacion a la baja.
        calmar: Retorno anualizado dividido por el drawdown maximo.
        exposure: Fraccion de barras con posicion abierta.
        avg_bars_held: Duracion media en barras.
        turnover: Operaciones por ano; detecta estrategias inviables por coste.
        cost_ratio: `costes_totales / |beneficio_bruto|`. Cuanto mayor, mas
            fragil es el resultado frente a un empeoramiento de la ejecucion.
    """

    n_trades: int
    net_profit: float
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    win_rate: float
    expectancy: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe: float
    sortino: float
    calmar: float
    exposure: float
    avg_bars_held: float
    turnover: float
    cost_ratio: float

    def __post_init__(self) -> None:
        if self.n_trades < 0:
            raise InvariantViolation("n_trades no puede ser negativo")
        if not (0.0 <= self.win_rate <= 1.0):
            raise InvariantViolation("win_rate fuera de [0,1]", win_rate=self.win_rate)
        if self.max_drawdown < 0:
            raise InvariantViolation("max_drawdown debe expresarse como positivo")
        if not (0.0 <= self.exposure <= 1.0):
            raise InvariantViolation("exposure fuera de [0,1]", exposure=self.exposure)

    @classmethod
    def empty(cls) -> PerformanceMetrics:
        """Metricas de una estrategia que no opero.

        Existe para que "cero operaciones" sea un resultado representable y
        comparable, en lugar de un `None` que obligue a ramas especiales en
        discovery y en los rankings.
        """
        return cls(
            n_trades=0,
            net_profit=0.0,
            gross_profit=0.0,
            gross_loss=0.0,
            profit_factor=None,
            win_rate=0.0,
            expectancy=0.0,
            max_drawdown=0.0,
            max_drawdown_pct=0.0,
            sharpe=0.0,
            sortino=0.0,
            calmar=0.0,
            exposure=0.0,
            avg_bars_held=0.0,
            turnover=0.0,
            cost_ratio=0.0,
        )

    @property
    def is_degenerate(self) -> bool:
        """Marca resultados que no admiten interpretacion estadistica.

        Una estrategia degenerada no se rechaza por mala: se rechaza por no
        tener evidencia suficiente. La distincion importa en los informes,
        porque la primera se descarta y la segunda puede merecer mas datos.
        """
        return self.n_trades < MIN_TRADES_FOR_INFERENCE or self.exposure <= 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_trades": self.n_trades,
            "net_profit": self.net_profit,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "profit_factor": self.profit_factor,
            "win_rate": self.win_rate,
            "expectancy": self.expectancy,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_pct": self.max_drawdown_pct,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "calmar": self.calmar,
            "exposure": self.exposure,
            "avg_bars_held": self.avg_bars_held,
            "turnover": self.turnover,
            "cost_ratio": self.cost_ratio,
            "is_degenerate": self.is_degenerate,
        }


__all__ = ["MIN_TRADES_FOR_INFERENCE", "PerformanceMetrics"]
