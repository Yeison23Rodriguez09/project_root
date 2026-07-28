"""Tipos primitivos compartidos por todo el sistema.

Nivel 0 del grafo de dependencias. Este modulo no importa nada del proyecto y
nada del proyecto puede hacer que lo haga. Es la garantia mecanica de la regla
`dependency_rule = "outward_only"` declarada en `pyproject.toml`.

Convenciones no negociables que se fijan aqui:

* El tiempo se representa SIEMPRE como nanosegundos desde epoch UTC (`int64`).
  No circulan objetos `datetime` con zona horaria implicita por el dominio.
* Los precios y magnitudes numericas circulan como `float64` en arrays de numpy,
  nunca como listas de Python ni como `Decimal`.
* La direccion de una posicion o senal es un entero con signo: -1, 0, +1.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Final, NewType, TypeAlias

import numpy as np
import numpy.typing as npt

# ---------------------------------------------------------------------------
# Alias de arrays. El sufijo indica el dtype exacto exigido en las fronteras.
# ---------------------------------------------------------------------------

FloatArray: TypeAlias = npt.NDArray[np.float64]
IntArray: TypeAlias = npt.NDArray[np.int64]
BoolArray: TypeAlias = npt.NDArray[np.bool_]
DirectionArray: TypeAlias = npt.NDArray[np.int8]
ReasonArray: TypeAlias = npt.NDArray[np.int16]

# ---------------------------------------------------------------------------
# Identificadores nominales. Evitan confundir un simbolo con un id de estrategia
# cuando ambos son `str` en tiempo de ejecucion.
# ---------------------------------------------------------------------------

Symbol = NewType("Symbol", str)
StrategyId = NewType("StrategyId", str)
RunId = NewType("RunId", str)
FeatureName = NewType("FeatureName", str)
BlockName = NewType("BlockName", str)
ContentHash = NewType("ContentHash", str)

#: Nanosegundos desde 1970-01-01T00:00:00Z. Unidad canonica de tiempo.
TimestampNs = NewType("TimestampNs", int)

NS_PER_SECOND: Final[int] = 1_000_000_000
NS_PER_MINUTE: Final[int] = 60 * NS_PER_SECOND
NS_PER_HOUR: Final[int] = 60 * NS_PER_MINUTE
NS_PER_DAY: Final[int] = 24 * NS_PER_HOUR


class Timeframe(StrEnum):
    """Marcos temporales soportados.

    El valor textual es el identificador estable usado en rutas de artefactos,
    nombres de fichero y claves de configuracion. No debe cambiarse sin migrar
    los artefactos existentes.
    """

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"

    @property
    def nanoseconds(self) -> int:
        """Duracion nominal de una barra en nanosegundos."""
        return _TIMEFRAME_NS[self]

    @property
    def minutes(self) -> int:
        """Duracion nominal de una barra en minutos enteros."""
        return _TIMEFRAME_NS[self] // NS_PER_MINUTE

    @property
    def bars_per_year(self) -> float:
        """Barras negociables al ano, usado para anualizar metricas.

        Se asume mercado de divisas: 24 horas, 5 dias, 52 semanas. Para
        instrumentos con horario distinto el valor debe venir de la
        configuracion del simbolo, no de aqui.
        """
        return (5 * 52 * NS_PER_DAY) / _TIMEFRAME_NS[self]


_TIMEFRAME_NS: Final[dict[Timeframe, int]] = {
    Timeframe.M1: NS_PER_MINUTE,
    Timeframe.M5: 5 * NS_PER_MINUTE,
    Timeframe.M15: 15 * NS_PER_MINUTE,
    Timeframe.M30: 30 * NS_PER_MINUTE,
    Timeframe.H1: NS_PER_HOUR,
    Timeframe.H4: 4 * NS_PER_HOUR,
    Timeframe.D1: NS_PER_DAY,
}


class Direction(IntEnum):
    """Direccion de una senal o posicion.

    Se modela como `IntEnum` para poder volcarse directamente a arrays `int8`
    sin conversiones intermedias, manteniendo legibilidad en el codigo.
    """

    SHORT = -1
    FLAT = 0
    LONG = 1


class Side(StrEnum):
    """Lado de una orden desde la perspectiva del broker."""

    BUY = "BUY"
    SELL = "SELL"


class Environment(StrEnum):
    """Entorno de ejecucion.

    Determina que adaptadores de infraestructura se instancian y que
    salvaguardas se activan. `LIVE` es el unico que puede enviar ordenes reales.
    """

    RESEARCH = "research"
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class Severity(IntEnum):
    """Gravedad de un hallazgo de validacion.

    El orden numerico es significativo: se usa para decidir si una corrida
    continua (`WARNING`) o se aborta (`ERROR` o superior).
    """

    INFO = 10
    WARNING = 20
    ERROR = 30
    FATAL = 40


class LifecycleState(StrEnum):
    """Estado de una estrategia dentro del ciclo de vida institucional.

    Una estrategia solo alcanza `PROMOTED` tras superar walk-forward y
    validacion estadistica. `LIVE` exige ademas aprobacion operativa explicita.
    """

    CANDIDATE = "candidate"
    VALIDATED = "validated"
    PROMOTED = "promoted"
    LIVE = "live"
    RETIRED = "retired"
    REJECTED = "rejected"


__all__ = [
    "NS_PER_DAY",
    "NS_PER_HOUR",
    "NS_PER_MINUTE",
    "NS_PER_SECOND",
    "BlockName",
    "BoolArray",
    "ContentHash",
    "Direction",
    "DirectionArray",
    "Environment",
    "FeatureName",
    "FloatArray",
    "IntArray",
    "LifecycleState",
    "ReasonArray",
    "RunId",
    "Severity",
    "Side",
    "StrategyId",
    "Symbol",
    "Timeframe",
    "TimestampNs",
]
