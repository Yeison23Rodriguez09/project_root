"""Senales y codigos de razon.

Requisito §14 del contrato: el sistema debe poder explicar por que NO opero.
Un array de -1/0/+1 no lo permite: un cero puede significar "no habia setup",
"habia setup pero era fuera de sesion" o "aun estoy en calentamiento". Esas
tres situaciones exigen decisiones distintas del investigador.

Por eso cada barra lleva, ademas de direccion, un `ReasonCode`. El coste es un
array `int16` adicional por bloque; el beneficio es que el embudo completo de
descarte es reconstruible a posteriori sin reejecutar nada.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Self

import numpy as np

from app.core.exceptions import InvariantViolation
from app.core.types import BlockName, BoolArray, DirectionArray, FloatArray, ReasonArray


class ReasonCode(IntEnum):
    """Motivo de la decision de una barra.

    Los rangos son estables y significativos; permiten agregar por familia sin
    enumerar cada codigo:

    * 0-9    estado neutro o exito
    * 10-19  disponibilidad de datos
    * 20-39  ausencia de condicion de entrada
    * 40-59  filtros contextuales (el setup existia y se descarto)
    * 60-79  confirmacion e invalidacion
    * 80-99  riesgo
    * 100+   ejecucion
    """

    # 0-9 - neutro
    OK = 0
    FLAT_BY_DESIGN = 1

    # 10-19 - datos
    WARMUP = 10
    MISSING_DATA = 11
    STALE_DATA = 12
    GAP_IN_SERIES = 13

    # 20-39 - sin condicion
    NO_SETUP = 20
    THRESHOLD_NOT_REACHED = 21
    ALREADY_IN_POSITION = 22
    COOLDOWN_ACTIVE = 23

    # 40-59 - filtros contextuales
    FILTERED_SESSION = 40
    FILTERED_VOLATILITY_LOW = 41
    FILTERED_VOLATILITY_HIGH = 42
    FILTERED_SPREAD = 43
    FILTERED_LIQUIDITY = 44
    FILTERED_TREND = 45
    FILTERED_RANGE = 46
    FILTERED_REGIME = 47
    FILTERED_DAY_OF_WEEK = 48
    FILTERED_NEWS_WINDOW = 49

    # 60-79 - confirmacion
    CONFIRMATION_MISSING = 60
    CONFIRMATION_EXPIRED = 61
    INVALIDATED = 62
    CONFLICTING_BLOCKS = 63
    SILENCED = 64

    # 80-99 - riesgo
    RISK_MAX_POSITIONS = 80
    RISK_MAX_DAILY_LOSS = 81
    RISK_MAX_DRAWDOWN = 82
    RISK_EXPOSURE_LIMIT = 83
    RISK_SIZE_BELOW_MINIMUM = 84
    RISK_MARGIN_INSUFFICIENT = 85

    # 100+ - ejecucion
    EXEC_MARKET_CLOSED = 100
    EXEC_BROKER_REJECTED = 101
    EXEC_ORDER_TIMEOUT = 102

    @property
    def family(self) -> str:
        """Familia agregada del codigo, para dashboards y embudos."""
        value = int(self)
        if value < 10:
            return "neutral"
        if value < 20:
            return "data"
        if value < 40:
            return "no_setup"
        if value < 60:
            return "context_filter"
        if value < 80:
            return "confirmation"
        if value < 100:
            return "risk"
        return "execution"


@dataclass(frozen=True, slots=True)
class SignalOutput:
    """Salida vectorizada de un bloque de senal, alineada barra a barra.

    Invariante de causalidad: `direction[i]` solo puede depender de informacion
    disponible hasta el cierre de la barra `i` inclusive. La traduccion a orden
    la hace el motor de ejecucion en la barra `i+1`. Ver ADR-0003.

    Attributes:
        block: Nombre estable del bloque que produjo la salida.
        direction: -1, 0 o +1 por barra.
        strength: Intensidad en [0, 1]. Los bloques binarios emiten 0 o 1. Los
            scoreadores emiten valores intermedios que el combinador pondera.
        reason: `ReasonCode` por barra. En barras con direccion distinta de
            cero debe ser `OK`.
    """

    block: BlockName
    direction: DirectionArray
    strength: FloatArray
    reason: ReasonArray

    def __post_init__(self) -> None:
        n = self.direction.size
        if not (self.strength.size == n and self.reason.size == n):
            raise InvariantViolation(
                "SignalOutput con arrays de longitud dispar",
                block=str(self.block),
                direction=self.direction.size,
                strength=self.strength.size,
                reason=self.reason.size,
            )
        if self.direction.dtype != np.int8:
            raise InvariantViolation("direction debe ser int8", dtype=str(self.direction.dtype))
        if self.strength.dtype != np.float64:
            raise InvariantViolation("strength debe ser float64", dtype=str(self.strength.dtype))
        if self.reason.dtype != np.int16:
            raise InvariantViolation("reason debe ser int16", dtype=str(self.reason.dtype))
        if n and not np.all(np.isin(self.direction, (-1, 0, 1))):
            raise InvariantViolation("direction fuera de {-1,0,1}", block=str(self.block))
        if n and np.any((self.strength < 0.0) | (self.strength > 1.0)):
            raise InvariantViolation("strength fuera de [0,1]", block=str(self.block))
        for array in (self.direction, self.strength, self.reason):
            if array.flags.owndata:
                array.setflags(write=False)

    # -- constructores ------------------------------------------------------

    @classmethod
    def flat(cls, block: str, n: int, reason: ReasonCode = ReasonCode.NO_SETUP) -> Self:
        """Salida totalmente plana. Base sobre la que escriben los bloques."""
        return cls(
            block=BlockName(block),
            direction=np.zeros(n, dtype=np.int8),
            strength=np.zeros(n, dtype=np.float64),
            reason=np.full(n, int(reason), dtype=np.int16),
        )

    @classmethod
    def from_conditions(
        cls,
        block: str,
        *,
        long_mask: BoolArray,
        short_mask: BoolArray,
        warmup: int = 0,
        strength: FloatArray | None = None,
        idle_reason: ReasonCode = ReasonCode.NO_SETUP,
    ) -> Self:
        """Construye una salida a partir de dos mascaras booleanas.

        Resuelve de forma explicita el caso ambiguo: si ambas mascaras son
        ciertas en la misma barra, la barra queda plana con
        `CONFLICTING_BLOCKS`. No se aplica ninguna precedencia arbitraria,
        porque una regla que se contradice a si misma es un defecto del diseno
        de la estrategia y debe ser visible, no resuelto en silencio.

        Args:
            warmup: Numero de barras iniciales marcadas como `WARMUP`. Quedan
                planas aunque las mascaras digan lo contrario.
        """
        n = int(long_mask.size)
        if short_mask.size != n:
            raise InvariantViolation("Mascaras de longitud dispar", long_=n, short=short_mask.size)

        long_m = np.asarray(long_mask, dtype=np.bool_).copy()
        short_m = np.asarray(short_mask, dtype=np.bool_).copy()
        conflict = long_m & short_m
        long_m &= ~conflict
        short_m &= ~conflict

        direction = np.zeros(n, dtype=np.int8)
        direction[long_m] = 1
        direction[short_m] = -1

        reason = np.full(n, int(idle_reason), dtype=np.int16)
        reason[conflict] = int(ReasonCode.CONFLICTING_BLOCKS)
        reason[long_m | short_m] = int(ReasonCode.OK)

        if strength is None:
            values = np.zeros(n, dtype=np.float64)
            values[long_m | short_m] = 1.0
        else:
            values = np.clip(np.asarray(strength, dtype=np.float64), 0.0, 1.0).copy()
            values[direction == 0] = 0.0

        effective_warmup = min(max(warmup, 0), n)
        if effective_warmup:
            direction[:effective_warmup] = 0
            values[:effective_warmup] = 0.0
            reason[:effective_warmup] = int(ReasonCode.WARMUP)

        return cls(
            block=BlockName(block),
            direction=direction,
            strength=values,
            reason=reason,
        )

    # -- transformacion -----------------------------------------------------

    def veto(self, allowed: BoolArray, reason: ReasonCode) -> SignalOutput:
        """Aplica un filtro contextual conservando el motivo del descarte.

        Es la operacion central de los bloques contextuales: donde `allowed` es
        falso la senal se anula y la barra queda etiquetada con el motivo. La
        senal original no se pierde como informacion agregada, porque el
        histograma resultante permite contar exactamente cuantos setups validos
        elimino cada filtro.
        """
        if allowed.size != len(self):
            raise InvariantViolation(
                "Mascara de veto con longitud distinta a la senal",
                block=str(self.block),
                signal=len(self),
                mask=int(allowed.size),
            )
        blocked = (~np.asarray(allowed, dtype=np.bool_)) & (self.direction != 0)
        if not np.any(blocked):
            return self
        direction = self.direction.copy()
        strength = self.strength.copy()
        reasons = self.reason.copy()
        direction[blocked] = 0
        strength[blocked] = 0.0
        reasons[blocked] = int(reason)
        return SignalOutput(
            block=self.block, direction=direction, strength=strength, reason=reasons
        )

    # -- consulta -----------------------------------------------------------

    def __len__(self) -> int:
        return int(self.direction.size)

    @property
    def active_mask(self) -> BoolArray:
        active: BoolArray = self.direction != 0
        return active

    @property
    def n_long(self) -> int:
        return int(np.count_nonzero(self.direction == 1))

    @property
    def n_short(self) -> int:
        return int(np.count_nonzero(self.direction == -1))

    def reason_histogram(self) -> dict[str, int]:
        """Embudo de descarte: cuantas barras murieron en cada motivo.

        Es la vista mas util para diagnosticar una estrategia que no opera. Si
        el 98% de las barras cae en `FILTERED_SESSION`, el problema no es la
        senal sino el horario configurado.
        """
        if not len(self):
            return {}
        codes, counts = np.unique(self.reason, return_counts=True)
        histogram: dict[str, int] = {}
        for code, count in zip(codes.tolist(), counts.tolist(), strict=True):
            try:
                label = ReasonCode(code).name
            except ValueError:  # codigo emitido por un bloque externo
                label = f"UNKNOWN_{code}"
            histogram[label] = int(count)
        return dict(sorted(histogram.items(), key=lambda kv: (-kv[1], kv[0])))

    def to_dict(self) -> dict[str, Any]:
        return {
            "block": str(self.block),
            "bars": len(self),
            "n_long": self.n_long,
            "n_short": self.n_short,
            "reasons": self.reason_histogram(),
        }

    def __repr__(self) -> str:
        return (
            f"SignalOutput({self.block} n={len(self)} "
            f"long={self.n_long} short={self.n_short})"
        )


__all__ = ["ReasonCode", "SignalOutput"]
