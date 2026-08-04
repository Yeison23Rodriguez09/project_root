"""Features de tendencia: medias sobre el cierre.

Las tres reglas que cumple cada funcion de este modulo, y que
`tests/test_no_lookahead.py` verifica sobre TODAS las registradas:

    longitud    la salida tiene exactamente `len(bars)` elementos
    causalidad  `salida[i]` depende solo de `bars[0..i]`
    calentamiento  las primeras `warmup` posiciones son NaN, nunca ceros

La tercera es la que mas errores esconde. Un cero es un valor legitimo de un
indicador, asi que rellenar el arranque con ceros no produce ningun fallo
visible: produce senales fantasma en las primeras barras de cada fold, que es
donde menos se miran.
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from app.core.registry.decorators import register, warmup_from
from app.core.registry.params import ParamSpec
from app.core.types import FloatArray
from app.domain.entities.bars import Bars
from app.research.features.registry import FEATURES

PERIODS: tuple[int, ...] = (5, 10, 14, 20, 30, 50, 100, 200)


def _period_spec(default: int = 20) -> ParamSpec:
    return ParamSpec(
        name="period",
        default=default,
        choices=PERIODS,
        low=2,
        high=500,
        description="Numero de barras de la ventana.",
    )


@register(
    FEATURES,
    "sma",
    params=(_period_spec(),),
    # La media de 20 barras ya es calculable EN la barra 20, es decir en el
    # indice 19: por eso el calentamiento es `period - 1` y no `period`.
    warmup=warmup_from("period", extra=-1),
    tags=("trend",),
)
def sma(bars: Bars, *, period: int = 20) -> FloatArray:
    """Media movil simple del cierre."""
    close = np.asarray(bars.close, dtype=np.float64)
    out = np.full(close.size, np.nan, dtype=np.float64)
    if close.size >= period:
        out[period - 1 :] = sliding_window_view(close, period).mean(axis=1)
    return out


@register(
    FEATURES,
    "ema",
    params=(_period_spec(),),
    warmup=warmup_from("period", extra=-1),
    tags=("trend",),
)
def ema(bars: Bars, *, period: int = 20) -> FloatArray:
    """Media movil exponencial del cierre, sembrada con la simple.

    Una EMA es recursiva y en teoria necesita historia infinita. Se siembra con
    la media simple de las primeras `period` barras en vez de con el primer
    cierre: sembrar con un unico valor deja el indicador arrastrando el ruido de
    esa barra durante decenas de posiciones, y ese sesgo cae justo al principio
    de cada fold de walk-forward.
    """
    close = np.asarray(bars.close, dtype=np.float64)
    out = np.full(close.size, np.nan, dtype=np.float64)
    if close.size < period:
        return out

    alpha = 2.0 / (period + 1.0)
    value = float(close[:period].mean())
    out[period - 1] = value
    for index in range(period, close.size):
        value = alpha * float(close[index]) + (1.0 - alpha) * value
        out[index] = value
    return out


__all__ = ["PERIODS", "ema", "sma"]
