"""Objeto de valor de metricas de rendimiento.

Se define en el dominio aunque el calculo viva en `analytics` porque es un
contrato entre capas: backtest lo produce, walk-forward lo agrega, discovery lo
ordena y la politica de promocion lo evalua. Fijar la forma en el dominio evita
que cada motor invente su propio diccionario y que las comparaciones entre
fases dejen de ser validas.

Responde a UNA pregunta: **como gano**. Cuanta confianza merece el resultado es
otra pregunta y vive en `validation_metrics.py`, separada a proposito (ADR-0010).

Regla de honestidad: todas las metricas se calculan sobre PnL **neto**. No
existe una version bruta en este objeto para que sea imposible presentar por
descuido un resultado sin costes. Desde ADR-0010 ese neto incluye la
financiacion, sin la cual la regla se derrotaba a si misma para cualquier
estrategia mantenida de un dia para otro.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.exceptions import InvariantViolation
from app.core.types import Timeframe

#: Numero minimo de operaciones para que una metrica admita lectura
#: estadistica. Por debajo, el resultado se marca como degenerado y no compite
#: en los rankings de discovery.
#:
#: 30 es el umbral convencional a partir del cual la distribucion de la media
#: muestral se aproxima razonablemente a la normal, de modo que la media por
#: operacion admite lectura inferencial en lugar de descriptiva. No es una cota
#: de suficiencia -una estrategia con 31 operaciones sigue siendo evidencia
#: pobre-: es el suelo por debajo del cual el numero directamente no significa
#: nada. Se mantiene como constante del dominio y NO se externaliza a
#: configuracion: es una heuristica metodologica, y ponerla en un fichero de
#: perfil invita a bajarla bajo presion de entrega, que es exactamente lo que
#: P9 existe para impedir.
MIN_TRADES_FOR_INFERENCE: int = 30


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """Resumen cuantitativo de una serie de operaciones.

    Attributes:
        timeframe: Marco temporal de las barras evaluadas. Va en el objeto
            porque sin el, `sharpe`, `sortino`, `cagr` y `turnover` no son
            auditables ni comparables: anualizar retornos por barra exige el
            factor `sqrt(barras por ano)`, que depende del timeframe. Dos
            resultados de M15 y H1 no son comparables, y antes de declararlo
            nada en el objeto lo delataba mientras discovery los ordenaba en la
            misma lista.
        bars: Numero de barras del periodo evaluado. Con `timeframe`, fija la
            duracion real y hace reconstruible cualquier anualizacion.
        n_trades: Numero de operaciones cerradas. Metrica de contexto
            obligatoria: un Sharpe de 3.0 con 7 operaciones no es informacion.
        net_profit: Suma de resultados netos.
        gross_profit / gross_loss: Suma de ganancias y de perdidas. `gross_loss`
            se expresa con signo negativo o cero, y ahora se verifica.
        profit_factor: `gross_profit / |gross_loss|`. `None` si no hay perdidas,
            para no propagar infinitos a los artefactos JSON ni a los rankings.
        win_rate: Proporcion de operaciones ganadoras en [0, 1].
        expectancy: Resultado neto medio por operacion.
        max_drawdown: Caida maxima de la curva de equity, en moneda de cuenta,
            expresada como numero positivo.
        max_drawdown_pct: La misma caida en fraccion del pico previo, en [0, 1].
        cagr: Tasa de crecimiento anual compuesta, en fraccion. Es la base del
            ranking del zoo y el numerador de `calmar`.
        sharpe: Ratio de Sharpe anualizado sobre retornos por barra, con tasa
            libre de riesgo cero. La eleccion se declara aqui en lugar de
            quedar implicita: con `Rf = 0` es en rigor un ratio de informacion,
            y no decirlo hace incomparable el numero con cualquier otra fuente.
        sortino: Variante que penaliza solo la desviacion a la baja.
        calmar: `cagr / max_drawdown_pct`. Sobre la FRACCION, nunca sobre el
            drawdown en moneda: un Calmar en unidades de cuenta no es comparable
            entre instrumentos ni entre tamanos de cuenta (ADR-0010).
        exposure: Fraccion de barras con posicion abierta.
        avg_bars_held: Duracion media en barras.
        turnover: Operaciones por ano; detecta estrategias inviables por coste.
        cost_ratio: `costes_totales / |beneficio_bruto|`. `None` cuando no hay
            beneficio bruto, por el mismo motivo que `profit_factor`: el mismo
            problema no puede recibir dos tratamientos dentro del mismo objeto.
            Cuanto mayor, mas fragil es el resultado frente a un empeoramiento
            de la ejecucion.
    """

    timeframe: Timeframe
    bars: int
    n_trades: int
    net_profit: float
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    win_rate: float
    expectancy: float
    max_drawdown: float
    max_drawdown_pct: float
    cagr: float
    sharpe: float
    sortino: float
    calmar: float
    exposure: float
    avg_bars_held: float
    turnover: float
    cost_ratio: float | None

    def __post_init__(self) -> None:
        if self.bars < 0:
            raise InvariantViolation("bars no puede ser negativo", bars=self.bars)
        if self.n_trades < 0:
            raise InvariantViolation("n_trades no puede ser negativo")
        if not (0.0 <= self.win_rate <= 1.0):
            raise InvariantViolation("win_rate fuera de [0,1]", win_rate=self.win_rate)
        if self.max_drawdown < 0:
            raise InvariantViolation("max_drawdown debe expresarse como positivo")
        if not (0.0 <= self.exposure <= 1.0):
            raise InvariantViolation("exposure fuera de [0,1]", exposure=self.exposure)
        # Las cuatro siguientes las prometia el docstring desde el principio y no
        # se comprobaban. Un objeto de dominio inmutable convierte "si existe, es
        # correcto" en propiedad del tipo; con la mitad de las promesas sin
        # verificar, esa garantia era media verdad (ADR-0010).
        if self.gross_loss > 0:
            raise InvariantViolation(
                "gross_loss se expresa con signo negativo o cero",
                gross_loss=self.gross_loss,
            )
        if not (0.0 <= self.max_drawdown_pct <= 1.0):
            raise InvariantViolation(
                "max_drawdown_pct es una fraccion en [0,1]",
                max_drawdown_pct=self.max_drawdown_pct,
            )
        if self.profit_factor is not None and self.profit_factor < 0:
            raise InvariantViolation(
                "profit_factor no puede ser negativo", profit_factor=self.profit_factor
            )
        if self.cost_ratio is not None and self.cost_ratio < 0:
            raise InvariantViolation(
                "cost_ratio no puede ser negativo", cost_ratio=self.cost_ratio
            )

    @classmethod
    def empty(cls, timeframe: Timeframe, *, bars: int = 0) -> PerformanceMetrics:
        """Metricas de una estrategia que no opero.

        Existe para que "cero operaciones" sea un resultado representable y
        comparable, en lugar de un `None` que obligue a ramas especiales en
        discovery y en los rankings.

        Exige `timeframe` porque el vacio tambien pertenece a un marco temporal:
        un resultado nulo de M15 y uno de H1 no son el mismo resultado, y
        permitir construirlo sin declararlo reabriria por la puerta de atras la
        incomparabilidad que este objeto evita.
        """
        return cls(
            timeframe=timeframe,
            bars=bars,
            n_trades=0,
            net_profit=0.0,
            gross_profit=0.0,
            gross_loss=0.0,
            profit_factor=None,
            win_rate=0.0,
            expectancy=0.0,
            max_drawdown=0.0,
            max_drawdown_pct=0.0,
            cagr=0.0,
            sharpe=0.0,
            sortino=0.0,
            calmar=0.0,
            exposure=0.0,
            avg_bars_held=0.0,
            turnover=0.0,
            cost_ratio=None,
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
            "timeframe": str(self.timeframe),
            "bars": self.bars,
            "n_trades": self.n_trades,
            "net_profit": self.net_profit,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "profit_factor": self.profit_factor,
            "win_rate": self.win_rate,
            "expectancy": self.expectancy,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_pct": self.max_drawdown_pct,
            "cagr": self.cagr,
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
