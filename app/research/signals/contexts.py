"""Bloques contextuales: no generan direccion, vetan.

`StrategySpec` los declara aparte de las entradas porque hacen algo distinto, y
la diferencia no es de matiz. Una entrada dice "aqui hay una oportunidad"; un
contexto dice "esta no cuenta". Mezclarlos haria imposible responder a la
pregunta que mas informa un embudo de descarte: de todos los setups que
aparecieron, cuantos elimino cada filtro y por que.

De ahi la convencion de salida de este modulo: direccion CERO en todas las
barras -nunca proponen operar- y el veredicto en `reason`. `OK` donde permiten,
un `FILTERED_*` donde vetan. La composicion lo traduce despues con
`SignalOutput.veto`, que conserva el motivo.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from app.core.registry.params import ParamSpec
from app.core.types import BlockName, BoolArray, ReasonArray
from app.domain.entities.feature_frame import FeatureFrame, feature_key
from app.domain.value_objects.signal import ReasonCode, SignalOutput
from app.research.features.frame import FeatureRequest
from app.research.signals.registry import CONTEXT, register_block


def gate(block: str, *, allowed: BoolArray, warmup: int, vetoed: ReasonCode) -> SignalOutput:
    """Construye la salida de un bloque contextual.

    Direccion cero en todo el array: un contexto nunca propone. El calentamiento
    se marca como `WARMUP` y no como veto, porque "no lo se todavia" y "lo he
    mirado y no vale" son cosas distintas y el embudo debe poder separarlas.
    """
    size = int(allowed.size)
    reason: ReasonArray = np.where(
        np.asarray(allowed, dtype=np.bool_), int(ReasonCode.OK), int(vetoed)
    ).astype(np.int16)

    effective = min(max(warmup, 0), size)
    if effective:
        reason[:effective] = int(ReasonCode.WARMUP)

    return SignalOutput(
        block=BlockName(block),
        direction=np.zeros(size, dtype=np.int8),
        strength=np.zeros(size, dtype=np.float64),
        reason=reason,
    )


def allows(output: SignalOutput) -> BoolArray:
    """Mascara de barras que un bloque contextual permite.

    Vive aqui y no en `domain` porque es la lectura de una CONVENCION de este
    modulo -direccion cero, veredicto en `reason`- y no una propiedad del tipo.
    El calentamiento cuenta como no permitido: sin dato no se autoriza nada.
    """
    return np.asarray(output.reason == int(ReasonCode.OK), dtype=np.bool_)


def _declare_rsi(params: Mapping[str, Any]) -> tuple[FeatureRequest, ...]:
    return (("rsi", {"period": int(params["period"])}),)


@register_block(
    "rsi_filter",
    role=CONTEXT,
    declares=_declare_rsi,
    params=(
        ParamSpec(name="period", default=14, choices=(7, 14, 21), low=2, high=100),
        ParamSpec(name="lower", default=25.0, choices=(15.0, 20.0, 25.0, 30.0), low=0.0, high=50.0),
        ParamSpec(
            name="upper", default=75.0, choices=(70.0, 75.0, 80.0, 85.0), low=50.0, high=100.0
        ),
    ),
    tags=("momentum",),
)
def rsi_filter(
    frame: FeatureFrame, *, period: int = 14, lower: float = 25.0, upper: float = 75.0
) -> SignalOutput:
    """Veta las barras en zona extrema de RSI.

    Entrar con el RSI ya saturado es entrar cuando el movimiento que justificaba
    la senal ya ocurrio. El filtro no dice hacia donde ir: solo retira de la mesa
    las barras donde cualquier direccion llega tarde.
    """
    values = frame[feature_key("rsi", {"period": period})]
    inside = (values >= lower) & (values <= upper)

    return gate(
        "rsi_filter",
        allowed=np.asarray(inside & ~np.isnan(values), dtype=np.bool_),
        warmup=frame.warmups[feature_key("rsi", {"period": period})],
        vetoed=ReasonCode.FILTERED_REGIME,
    )


def _declare_atr(params: Mapping[str, Any]) -> tuple[FeatureRequest, ...]:
    return (
        ("atr", {"period": int(params["period"])}),
        ("sma", {"period": int(params["baseline"])}),
    )


@register_block(
    "atr_filter",
    role=CONTEXT,
    declares=_declare_atr,
    params=(
        ParamSpec(name="period", default=14, choices=(7, 14, 21), low=2, high=100),
        ParamSpec(name="baseline", default=100, choices=(50, 100, 200), low=10, high=500),
        # Banda MEDIDA, no elegida. Sobre las 10.365 barras reales de EURUSD M15
        # el ATR(14) va de 0.015% a 0.150% del precio, con mediana 0.041% y
        # percentiles 1 y 99 en 0.021% y 0.104%. Los defaults recortan esos dos
        # extremos y dejan pasar el 98% central.
        #
        # Estos numeros son de ESE simbolo y ESE marco temporal. En H4 el ATR
        # relativo es varias veces mayor, asi que discovery debe reajustarlos por
        # serie; las cotas `low`/`high` se dejan anchas para permitirlo.
        ParamSpec(
            name="min_ratio", default=0.02, choices=(0.0, 0.02, 0.03, 0.04), low=0.0, high=50.0
        ),
        ParamSpec(
            name="max_ratio", default=0.10, choices=(0.06, 0.08, 0.10, 0.15), low=0.001, high=100.0
        ),
    ),
    tags=("volatility",),
)
def atr_filter(
    frame: FeatureFrame,
    *,
    period: int = 14,
    baseline: int = 100,
    min_ratio: float = 0.02,
    max_ratio: float = 0.10,
) -> SignalOutput:
    """Veta las barras con volatilidad fuera de banda, medida en relativo.

    La banda se expresa como cociente entre el ATR y el precio medio, no en
    puntos absolutos: un ATR de 50 puntos es agitacion en EURUSD y calma en un
    indice, y un umbral absoluto significaria cosas distintas en cada simbolo.

    Los dos extremos vetan por motivos opuestos. Con volatilidad demasiado baja
    el coste de operar se come el recorrido; con demasiado alta el stop calculado
    sobre el ATR se vuelve tan ancho que el tamano cae a cero o el ruido lo barre
    antes de que la idea se desarrolle. Por eso llevan `ReasonCode` distintos: en
    el embudo son diagnosticos distintos.
    """
    if min_ratio >= max_ratio:
        return SignalOutput.flat("atr_filter", len(frame), ReasonCode.FLAT_BY_DESIGN)

    volatility = frame[feature_key("atr", {"period": period})]
    reference = frame[feature_key("sma", {"period": baseline})]

    ratio = np.full(volatility.size, np.nan, dtype=np.float64)
    np.divide(volatility, reference, out=ratio, where=reference > 0.0)
    scaled = ratio * 100.0  # Porcentaje del precio: numeros legibles en informes.

    usable = ~np.isnan(scaled)
    too_low = usable & (scaled < min_ratio)
    allowed = usable & (scaled >= min_ratio) & (scaled <= max_ratio)

    warmup = max(
        frame.warmups[feature_key("atr", {"period": period})],
        frame.warmups[feature_key("sma", {"period": baseline})],
    )
    output = gate(
        "atr_filter",
        allowed=np.asarray(allowed, dtype=np.bool_),
        warmup=warmup,
        vetoed=ReasonCode.FILTERED_VOLATILITY_HIGH,
    )

    # Se reetiqueta el extremo bajo: los dos vetos existen por motivos opuestos y
    # contarlos juntos ocultaria cual de los dos esta descartando la estrategia.
    reason = np.array(output.reason, copy=True)
    reason[too_low & (reason == int(ReasonCode.FILTERED_VOLATILITY_HIGH))] = int(
        ReasonCode.FILTERED_VOLATILITY_LOW
    )
    return SignalOutput(
        block=output.block,
        direction=output.direction,
        strength=output.strength,
        reason=reason,
    )


__all__ = ["allows", "atr_filter", "gate", "rsi_filter"]
