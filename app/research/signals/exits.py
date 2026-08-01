"""Bloques de salida: cuando cerrar lo que este abierto.

`StrategySpec` los evalua en OR -la primera condicion que se cumple cierra- y esa
asimetria respecto a las entradas es deliberada. Para entrar se exige acuerdo;
para salir basta con que UNA razon aparezca. Un sistema que exigiera consenso
para cerrar seguiria dentro mientras los motivos para salir se acumulan.

Estas salidas son por SENAL. El stop de proteccion no vive aqui: lo dimensiona
`portfolio` y lo coloca el motor de ejecucion sobre el precio de llenado. Son
dos mecanismos distintos y confundirlos dejaria la posicion sin proteccion
cuando el bloque de salida no dispara.

Convencion de este modulo: `direction` es la direccion que se CIERRA. Un `+1`
significa "cierra los largos", no "abre un largo".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from app.core.registry.params import ParamSpec
from app.domain.entities.feature_frame import FeatureFrame, feature_key
from app.domain.value_objects.signal import ReasonCode, SignalOutput
from app.research.features.frame import FeatureRequest
from app.research.signals.registry import EXIT, register_block


def _declare_ema(params: Mapping[str, Any]) -> tuple[FeatureRequest, ...]:
    return (("ema", {"period": int(params["period"])}),)


@register_block(
    "ema_exit",
    role=EXIT,
    declares=_declare_ema,
    params=(ParamSpec(name="period", default=20, choices=(10, 20, 30, 50), low=2, high=500),),
    tags=("trend",),
)
def ema_exit(frame: FeatureFrame, *, period: int = 20) -> SignalOutput:
    """Cierra cuando el cierre cruza la media en contra de la posicion.

    El cruce se detecta contra la barra anterior, igual que en las entradas: el
    estado "precio por debajo de la media" mandaria cerrar en cada barra de la
    caida, y lo que interesa es el instante en que la condicion aparece.
    """
    average = frame[feature_key("ema", {"period": period})]
    close = np.asarray(frame.bars.close, dtype=np.float64)

    above = close > average
    previous = np.empty_like(above)
    previous[0] = False
    previous[1:] = above[:-1]

    valid = ~np.isnan(average)
    valid_previous = np.empty_like(valid)
    valid_previous[0] = False
    valid_previous[1:] = valid[:-1]
    usable = valid & valid_previous

    # Cruce a la baja cierra largos (+1); cruce al alza cierra cortos (-1).
    close_longs = ~above & previous & usable
    close_shorts = above & ~previous & usable

    return SignalOutput.from_conditions(
        "ema_exit",
        long_mask=close_longs,
        short_mask=close_shorts,
        warmup=frame.warmups[feature_key("ema", {"period": period})],
        idle_reason=ReasonCode.NO_SETUP,
    )


def _declare_rsi(params: Mapping[str, Any]) -> tuple[FeatureRequest, ...]:
    return (("rsi", {"period": int(params["period"])}),)


@register_block(
    "rsi_exit",
    role=EXIT,
    declares=_declare_rsi,
    params=(
        ParamSpec(name="period", default=14, choices=(7, 14, 21), low=2, high=100),
        ParamSpec(name="exit_long", default=70.0, choices=(60.0, 70.0, 80.0), low=50.0, high=100.0),
        ParamSpec(name="exit_short", default=30.0, choices=(20.0, 30.0, 40.0), low=0.0, high=50.0),
    ),
    tags=("momentum",),
)
def rsi_exit(
    frame: FeatureFrame,
    *,
    period: int = 14,
    exit_long: float = 70.0,
    exit_short: float = 30.0,
) -> SignalOutput:
    """Cierra al alcanzar la zona opuesta del oscilador: toma de beneficio."""
    if exit_short >= exit_long:
        return SignalOutput.flat("rsi_exit", len(frame), ReasonCode.FLAT_BY_DESIGN)

    values = frame[feature_key("rsi", {"period": period})]
    usable = ~np.isnan(values)

    return SignalOutput.from_conditions(
        "rsi_exit",
        long_mask=np.asarray(usable & (values >= exit_long), dtype=np.bool_),
        short_mask=np.asarray(usable & (values <= exit_short), dtype=np.bool_),
        warmup=frame.warmups[feature_key("rsi", {"period": period})],
        idle_reason=ReasonCode.THRESHOLD_NOT_REACHED,
    )


__all__ = ["ema_exit", "rsi_exit"]
