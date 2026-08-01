"""Features de momento: velocidad del precio, no su nivel."""

from __future__ import annotations

import numpy as np

from app.core.registry.decorators import register, warmup_from
from app.core.types import FloatArray
from app.domain.entities.bars import Bars
from app.research.features.registry import FEATURES
from app.research.features.trend import _period_spec

#: Valor del RSI cuando no hubo movimiento alguno en la ventana.
#
# Ni sobrecompra ni sobreventa: la definicion no esta definida cuando ganancias
# y perdidas medias son ambas cero, y 50 es el unico valor que no inventa una
# senal donde no hay informacion.
FLAT_RSI = 50.0


@register(
    FEATURES,
    "rsi",
    params=(_period_spec(default=14),),
    # Las diferencias empiezan en la barra 1, asi que promediar `period` de
    # ellas da el primer valor en el indice `period`, no en `period - 1`.
    warmup=warmup_from("period"),
    tags=("momentum",),
)
def rsi(bars: Bars, *, period: int = 14) -> FloatArray:
    """Indice de fuerza relativa con suavizado de Wilder.

    Se usa el suavizado de Wilder y no una media simple porque es el que define
    el indicador: una media simple da otro numero y las lecturas dejarian de
    ser comparables con cualquier referencia externa.
    """
    close = np.asarray(bars.close, dtype=np.float64)
    out = np.full(close.size, np.nan, dtype=np.float64)
    if close.size <= period:
        return out

    delta = np.diff(close)
    gains = np.maximum(delta, 0.0)
    losses = np.maximum(-delta, 0.0)

    average_gain = float(gains[:period].mean())
    average_loss = float(losses[:period].mean())
    out[period] = _rsi_value(average_gain, average_loss)

    for index in range(period + 1, close.size):
        # `delta[i - 1]` es el movimiento que CIERRA la barra `i`: usarlo aqui
        # mantiene la causalidad.
        average_gain = (average_gain * (period - 1) + float(gains[index - 1])) / period
        average_loss = (average_loss * (period - 1) + float(losses[index - 1])) / period
        out[index] = _rsi_value(average_gain, average_loss)
    return out


def _rsi_value(average_gain: float, average_loss: float) -> float:
    """RSI a partir de las medias, resolviendo los casos degenerados.

    Sin perdidas medias el cociente es infinito y la formula no puede evaluarse.
    Se devuelve 100 -sobrecompra total- si hubo ganancias, y `FLAT_RSI` si no
    hubo movimiento en ninguna direccion.
    """
    if average_loss == 0.0:
        return 100.0 if average_gain > 0.0 else FLAT_RSI
    strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + strength)


@register(
    FEATURES,
    "roc",
    params=(_period_spec(default=10),),
    warmup=warmup_from("period"),
    tags=("momentum",),
)
def roc(bars: Bars, *, period: int = 10) -> FloatArray:
    """Variacion relativa del cierre respecto a `period` barras antes.

    La division SI lleva guarda, al contrario que en otros calculos de la
    plataforma: `Bars` no exige precios positivos -acepta un cierre de cero- asi
    que el denominador puede anularse de verdad. Donde ocurre se devuelve NaN,
    que es el mismo marcador de "no calculable" que usa el calentamiento, y no
    un infinito que contaminaria en silencio todo lo que venga despues.
    """
    close = np.asarray(bars.close, dtype=np.float64)
    out = np.full(close.size, np.nan, dtype=np.float64)
    if close.size <= period:
        return out

    previous = close[:-period]
    current = close[period:]
    ratio = np.full(previous.size, np.nan, dtype=np.float64)
    np.divide(current, previous, out=ratio, where=previous != 0.0)
    out[period:] = ratio - 1.0
    return out


__all__ = ["FLAT_RSI", "roc", "rsi"]
