"""Bloques de entrada: los unicos que generan direccion.

La causalidad de estos bloques descansa entera en una regla: la decision de la
barra `i` solo puede mirar hasta el CIERRE de `i`. Un cruce se detecta
comparando `i` con `i-1`, nunca `i+1` con `i`, y la orden que resulte la enviara
el motor de ejecucion en `i+1` (ADR-0003).

Todos usan `SignalOutput.from_conditions`, que resuelve el caso ambiguo de forma
explicita: si las dos mascaras coinciden en una barra, queda plana con
`CONFLICTING_BLOCKS` en vez de aplicar una precedencia inventada.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from app.core.registry.params import ParamSpec
from app.domain.entities.feature_frame import FeatureFrame, feature_key
from app.domain.value_objects.signal import ReasonCode, SignalOutput
from app.research.features.frame import FeatureRequest
from app.research.signals.registry import ENTRY, register_block

FAST_PERIODS: tuple[int, ...] = (5, 8, 10, 12, 20)
SLOW_PERIODS: tuple[int, ...] = (20, 30, 50, 100, 200)
RSI_PERIODS: tuple[int, ...] = (7, 14, 21)


def _crossed(fast: Any, slow: Any) -> tuple[Any, Any]:
    """Mascaras de cruce al alza y a la baja, comparando `i` contra `i-1`.

    Un cruce es un CAMBIO de estado, no un estado. Devolver "rapida por encima
    de lenta" produciria una senal en cada barra de la tendencia y la estrategia
    intentaria entrar continuamente; lo que interesa es la barra en que ocurre.

    La barra 0 nunca es un cruce -no tiene anterior- y las posiciones con NaN
    tampoco, porque comparar contra un NaN da falso y eso es justo lo que se
    quiere: sin dato no hay cruce.
    """
    above = fast > slow
    previous = np.empty_like(above)
    previous[0] = False
    previous[1:] = above[:-1]

    valid = ~np.isnan(fast) & ~np.isnan(slow)
    valid_previous = np.empty_like(valid)
    valid_previous[0] = False
    valid_previous[1:] = valid[:-1]
    usable = valid & valid_previous

    return (above & ~previous & usable, ~above & previous & usable)


def _declare_cross(params: Mapping[str, Any]) -> tuple[FeatureRequest, ...]:
    return (
        ("ema", {"period": int(params["fast"])}),
        ("ema", {"period": int(params["slow"])}),
    )


@register_block(
    "ema_cross",
    role=ENTRY,
    declares=_declare_cross,
    params=(
        ParamSpec(name="fast", default=12, choices=FAST_PERIODS, low=2, high=500),
        ParamSpec(name="slow", default=50, choices=SLOW_PERIODS, low=2, high=500),
    ),
    tags=("trend",),
)
def ema_cross(frame: FeatureFrame, *, fast: int = 12, slow: int = 50) -> SignalOutput:
    """Cruce de medias exponenciales: largo al alza, corto a la baja."""
    if fast >= slow:
        # No es un cruce de tendencia sino la misma media contra si misma
        # desplazada: produce ruido con aspecto de senal. Se declara plano en vez
        # de lanzar porque discovery explora combinaciones y este caso debe
        # descartarse solo, con motivo visible en el embudo.
        return SignalOutput.flat("ema_cross", len(frame), ReasonCode.FLAT_BY_DESIGN)

    quick = frame[feature_key("ema", {"period": fast})]
    slowly = frame[feature_key("ema", {"period": slow})]
    up, down = _crossed(quick, slowly)

    return SignalOutput.from_conditions(
        "ema_cross",
        long_mask=up,
        short_mask=down,
        warmup=frame.warmups[feature_key("ema", {"period": slow})],
        idle_reason=ReasonCode.NO_SETUP,
    )


def _declare_rsi(params: Mapping[str, Any]) -> tuple[FeatureRequest, ...]:
    return (("rsi", {"period": int(params["period"])}),)


@register_block(
    "rsi_reversion",
    role=ENTRY,
    declares=_declare_rsi,
    params=(
        ParamSpec(name="period", default=14, choices=RSI_PERIODS, low=2, high=100),
        ParamSpec(
            name="oversold", default=30.0, choices=(20.0, 25.0, 30.0, 35.0), low=1.0, high=49.0
        ),
        ParamSpec(
            name="overbought", default=70.0, choices=(65.0, 70.0, 75.0, 80.0), low=51.0, high=99.0
        ),
    ),
    tags=("momentum",),
)
def rsi_reversion(
    frame: FeatureFrame,
    *,
    period: int = 14,
    oversold: float = 30.0,
    overbought: float = 70.0,
) -> SignalOutput:
    """Reversion a la media: largo al SALIR de sobreventa, corto al salir de sobrecompra.

    Se entra al salir de la zona y no al entrar en ella. Comprar porque el RSI
    cayo de 30 es comprar mientras sigue cayendo; esperar a que vuelva a cruzar
    30 hacia arriba exige que el movimiento se haya agotado. Es la diferencia
    entre atrapar el rebote y atrapar el cuchillo.
    """
    if oversold >= overbought:
        return SignalOutput.flat("rsi_reversion", len(frame), ReasonCode.FLAT_BY_DESIGN)

    values = frame[feature_key("rsi", {"period": period})]
    previous = np.empty_like(values)
    previous[0] = np.nan
    previous[1:] = values[:-1]

    up = (previous <= oversold) & (values > oversold)
    down = (previous >= overbought) & (values < overbought)

    return SignalOutput.from_conditions(
        "rsi_reversion",
        long_mask=up,
        short_mask=down,
        warmup=frame.warmups[feature_key("rsi", {"period": period})],
        idle_reason=ReasonCode.THRESHOLD_NOT_REACHED,
    )


__all__ = ["FAST_PERIODS", "RSI_PERIODS", "SLOW_PERIODS", "ema_cross", "rsi_reversion"]
