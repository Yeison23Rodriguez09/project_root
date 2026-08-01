"""Combinacion de bloques de entrada en una sola direccion por barra.

Los cuatro modos son los que `CombineMode` ya declaraba en el dominio; aqui se
implementan segun su definicion escrita, no segun una interpretacion nueva:

    ALL       conjuncion: todos deben coincidir en direccion
    ANY       disyuncion, con resolucion explicita de conflictos
    MAJORITY  voto por mayoria simple
    WEIGHTED  suma ponderada de `strength` contra un umbral

Todos comparten una regla: cuando el resultado es plano, el motivo NO es
generico. Si dos bloques se contradicen se dice `CONFLICTING_BLOCKS`; si
simplemente no habia setup se propaga el motivo del primer bloque que no dijo
`OK`. Un cero sin causa rompe el embudo de descarte, que es lo unico que permite
responder por que una estrategia no opera.

"El primero" se toma en el orden declarado en el `StrategySpec`, que es estable
y forma parte de la identidad de la estrategia: el motivo propagado es por tanto
reproducible y no depende de como se recorriera el array.
"""

from __future__ import annotations

import numpy as np

from app.core.exceptions import InvariantViolation
from app.core.types import DirectionArray, FloatArray, ReasonArray
from app.domain.value_objects.signal import ReasonCode, SignalOutput
from app.domain.value_objects.strategy_spec import CombineMode


def _stack(outputs: tuple[SignalOutput, ...]) -> tuple[DirectionArray, ReasonArray]:
    directions = np.vstack([output.direction for output in outputs]).astype(np.int8)
    reasons = np.vstack([output.reason for output in outputs]).astype(np.int16)
    return directions, reasons


def _first_reason(reasons: ReasonArray) -> ReasonArray:
    """Motivo del primer bloque que no dijo `OK`, por barra.

    Si todos dijeron `OK` -caso que solo ocurre cuando el modo descarto por otra
    causa- se devuelve `NO_SETUP`, que es el neutro honesto.
    """
    blocking = reasons != int(ReasonCode.OK)
    index = np.argmax(blocking, axis=0)
    picked = np.take_along_axis(reasons, index[None, :], axis=0)[0]
    return np.where(blocking.any(axis=0), picked, int(ReasonCode.NO_SETUP)).astype(np.int16)


def _finish(
    direction: DirectionArray, reasons: ReasonArray, conflict: np.ndarray
) -> tuple[DirectionArray, ReasonArray]:
    """Construye el motivo final a partir del veredicto direccional."""
    reason = _first_reason(reasons)
    reason[conflict] = int(ReasonCode.CONFLICTING_BLOCKS)
    reason[direction != 0] = int(ReasonCode.OK)
    return direction.astype(np.int8), reason.astype(np.int16)


def combine(
    outputs: tuple[SignalOutput, ...],
    *,
    mode: str,
    threshold: float,
    weights: tuple[float, ...],
) -> tuple[DirectionArray, ReasonArray]:
    """Reduce varias salidas de entrada a una direccion y un motivo por barra.

    Raises:
        InvariantViolation: no hay salidas, el modo es desconocido o los pesos no
            acompanan a las salidas.
    """
    if not outputs:
        raise InvariantViolation("No hay bloques de entrada que combinar")
    if len(weights) != len(outputs):
        raise InvariantViolation(
            "Cada bloque necesita su peso", outputs=len(outputs), weights=len(weights)
        )

    directions, reasons = _stack(outputs)
    longs = directions == 1
    shorts = directions == -1
    any_long = longs.any(axis=0)
    any_short = shorts.any(axis=0)

    if mode == CombineMode.ALL:
        return _all(directions, reasons, longs, shorts, any_long, any_short)
    if mode == CombineMode.ANY:
        return _any(directions, reasons, any_long, any_short)
    if mode == CombineMode.MAJORITY:
        return _majority(directions, reasons)
    if mode == CombineMode.WEIGHTED:
        return _weighted(outputs, directions, reasons, threshold, weights)

    raise InvariantViolation(
        "Modo de combinacion desconocido", mode=mode, allowed=list(CombineMode.ALL_MODES)
    )


def _all(
    directions: DirectionArray,
    reasons: ReasonArray,
    longs: np.ndarray,
    shorts: np.ndarray,
    any_long: np.ndarray,
    any_short: np.ndarray,
) -> tuple[DirectionArray, ReasonArray]:
    """Conjuncion: hace falta que TODOS coincidan en la misma direccion."""
    direction = np.zeros(directions.shape[1], dtype=np.int8)
    direction[longs.all(axis=0)] = 1
    direction[shorts.all(axis=0)] = -1
    return _finish(direction, reasons, any_long & any_short)


def _any(
    directions: DirectionArray,
    reasons: ReasonArray,
    any_long: np.ndarray,
    any_short: np.ndarray,
) -> tuple[DirectionArray, ReasonArray]:
    """Disyuncion: basta uno, salvo que dos apunten a lados distintos.

    El conflicto no se resuelve por precedencia: una estrategia que se contradice
    a si misma es un defecto de diseno y debe verse en el embudo, no taparse
    eligiendo el bloque que aparece antes.
    """
    direction = np.zeros(directions.shape[1], dtype=np.int8)
    conflict = any_long & any_short
    direction[any_long & ~conflict] = 1
    direction[any_short & ~conflict] = -1
    return _finish(direction, reasons, conflict)


def _majority(
    directions: DirectionArray, reasons: ReasonArray
) -> tuple[DirectionArray, ReasonArray]:
    """Voto por mayoria simple. El empate NO desempata: queda plano.

    Un empate con votos emitidos es una contradiccion real de la estrategia, y
    romperlo con cualquier criterio -el primero, el de mas peso- inventaria una
    decision que ningun bloque tomo.
    """
    votes = directions.sum(axis=0)
    direction = np.sign(votes).astype(np.int8)
    tie = (votes == 0) & (directions != 0).any(axis=0)
    return _finish(direction, reasons, tie)


def _weighted(
    outputs: tuple[SignalOutput, ...],
    directions: DirectionArray,
    reasons: ReasonArray,
    threshold: float,
    weights: tuple[float, ...],
) -> tuple[DirectionArray, ReasonArray]:
    """Suma ponderada de `strength` con signo, contra un umbral.

    Es el unico modo que usa la intensidad y no solo el signo: permite que un
    bloque muy convencido pese mas que dos tibios. La puntuacion se normaliza por
    el peso total para que el umbral signifique lo mismo con dos bloques que con
    diez.
    """
    total = float(sum(weights))
    if total <= 0.0:
        raise InvariantViolation("La suma de pesos debe ser positiva", weights=list(weights))

    strengths: FloatArray = np.vstack([output.strength for output in outputs])
    column = np.asarray(weights, dtype=np.float64)[:, None]
    score = (strengths * directions * column).sum(axis=0) / total

    direction = np.zeros(score.size, dtype=np.int8)
    direction[score >= threshold] = 1
    direction[score <= -threshold] = -1

    # Sin conflicto declarado: en este modo las discrepancias ya se cancelan
    # dentro de la suma, y marcarlas ademas contaria dos veces lo mismo.
    reason = _first_reason(reasons)
    fired = (directions != 0).any(axis=0)
    reason[fired & (direction == 0)] = int(ReasonCode.THRESHOLD_NOT_REACHED)
    reason[direction != 0] = int(ReasonCode.OK)
    return direction, reason.astype(np.int16)


__all__ = ["combine"]
