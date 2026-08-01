"""Features de volatilidad: cuanto se mueve, sin mirar hacia donde.

Es la familia que alimenta el dimensionamiento. `FixedFractionalRiskPolicy`
divide el presupuesto entre la distancia al stop, y esa distancia sale casi
siempre de un multiplo de ATR: un error aqui no produce una senal mala, produce
un TAMANO malo, que es bastante peor.
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from app.core.registry.decorators import register, warmup_from
from app.core.types import FloatArray
from app.domain.entities.bars import Bars
from app.research.features.registry import FEATURES
from app.research.features.trend import _period_spec


def true_range(bars: Bars) -> FloatArray:
    """Rango verdadero por barra. `NaN` en la primera, que no tiene anterior.

    Las tres componentes existen por un motivo concreto: el rango de la barra
    ignora los huecos, y un hueco de apertura es movimiento real que el stop
    sufre igual. Tomarlas por separado subestimaria la volatilidad justo en las
    sesiones en que mas importa.
    """
    high = np.asarray(bars.high, dtype=np.float64)
    low = np.asarray(bars.low, dtype=np.float64)
    close = np.asarray(bars.close, dtype=np.float64)

    out = np.full(high.size, np.nan, dtype=np.float64)
    if high.size < 2:
        return out

    previous_close = close[:-1]
    out[1:] = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - previous_close), np.abs(low[1:] - previous_close)),
    )
    return out


@register(
    FEATURES,
    "atr",
    params=(_period_spec(default=14),),
    # El rango verdadero no existe en la barra 0, asi que promediar `period` de
    # ellos da el primer valor en el indice `period`.
    warmup=warmup_from("period"),
    tags=("volatility",),
)
def atr(bars: Bars, *, period: int = 14) -> FloatArray:
    """Rango verdadero medio con suavizado de Wilder."""
    ranges = true_range(bars)
    out = np.full(ranges.size, np.nan, dtype=np.float64)
    if ranges.size <= period:
        return out

    value = float(ranges[1 : period + 1].mean())
    out[period] = value
    for index in range(period + 1, ranges.size):
        value = (value * (period - 1) + float(ranges[index])) / period
        out[index] = value
    return out


@register(
    FEATURES,
    "stdev",
    params=(_period_spec(),),
    warmup=warmup_from("period", extra=-1),
    tags=("volatility",),
)
def stdev(bars: Bars, *, period: int = 20) -> FloatArray:
    """Desviacion tipica muestral del cierre en la ventana.

    Muestral -`ddof=1`- y no poblacional: la ventana es una muestra de un
    proceso, no la poblacion entera. Con periodos cortos la diferencia entre
    dividir por n o por n-1 no es despreciable, y es justo donde se usan.
    """
    close = np.asarray(bars.close, dtype=np.float64)
    out = np.full(close.size, np.nan, dtype=np.float64)
    if close.size >= period:
        out[period - 1 :] = sliding_window_view(close, period).std(axis=1, ddof=1)
    return out


__all__ = ["atr", "stdev", "true_range"]
