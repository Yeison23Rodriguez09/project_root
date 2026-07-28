"""Rangos temporales y particiones de walk-forward.

`Fold` es el objeto que hace auditable la validacion secuencial. Que sea un
objeto de valor validado y no una tupla de indices tiene una consecuencia
concreta: es imposible construir un fold cuyo out-of-sample solape con su
in-sample, porque el constructor lo rechaza. La fuga de informacion entre IS y
OOS es el error mas caro de la investigacion cuantitativa y aqui se vuelve
estructuralmente imposible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.exceptions import InvariantViolation
from app.core.timeutils import format_ns
from app.core.types import TimestampNs


@dataclass(frozen=True, slots=True)
class TimeRange:
    """Intervalo semiabierto `[start_ns, end_ns)` en nanosegundos UTC.

    Semiabierto por convencion uniforme: asi dos rangos consecutivos cubren el
    eje temporal sin solapar en el instante frontera, que es exactamente el
    error que produce una barra duplicada entre folds.
    """

    start_ns: TimestampNs
    end_ns: TimestampNs

    def __post_init__(self) -> None:
        if int(self.end_ns) <= int(self.start_ns):
            raise InvariantViolation(
                "TimeRange vacio o invertido",
                start_ns=int(self.start_ns),
                end_ns=int(self.end_ns),
            )

    @property
    def duration_ns(self) -> int:
        return int(self.end_ns) - int(self.start_ns)

    def contains(self, ts: TimestampNs | int) -> bool:
        return int(self.start_ns) <= int(ts) < int(self.end_ns)

    def overlaps(self, other: TimeRange) -> bool:
        return int(self.start_ns) < int(other.end_ns) and int(other.start_ns) < int(self.end_ns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_ns": int(self.start_ns),
            "end_ns": int(self.end_ns),
            "start": format_ns(self.start_ns),
            "end": format_ns(self.end_ns),
        }

    def __str__(self) -> str:
        return f"[{format_ns(self.start_ns)} .. {format_ns(self.end_ns)})"


@dataclass(frozen=True, slots=True)
class Fold:
    """Particion in-sample / out-of-sample de walk-forward.

    Attributes:
        index: Numero de fold, empezando en 0. Se usa para derivar semillas.
        in_sample: Tramo de ajuste u optimizacion.
        out_of_sample: Tramo de evaluacion, siempre POSTERIOR al in-sample.
        purge_ns: Separacion temporal impuesta entre IS y OOS.

    Sobre `purge_ns`: no basta con que OOS empiece donde acaba IS. Cualquier
    feature con ventana de `w` barras calculada al principio del OOS usa datos
    del final del IS. Si ademas la etiqueta de una operacion depende de barras
    posteriores a su entrada, el solape es bidireccional. La purga elimina ese
    puente. Un `purge_ns` de cero es legitimo solo si se ha verificado que el
    calentamiento maximo de todas las features es cero, lo cual casi nunca es
    cierto; por eso el valor se declara de forma explicita en cada fold en
    lugar de asumirse.
    """

    index: int
    in_sample: TimeRange
    out_of_sample: TimeRange
    purge_ns: int = 0

    def __post_init__(self) -> None:
        if self.index < 0:
            raise InvariantViolation("El indice de fold no puede ser negativo")
        if self.purge_ns < 0:
            raise InvariantViolation("purge_ns no puede ser negativo")
        if int(self.out_of_sample.start_ns) < int(self.in_sample.end_ns):
            raise InvariantViolation(
                "El out-of-sample empieza antes de terminar el in-sample",
                fold=self.index,
                is_end=int(self.in_sample.end_ns),
                oos_start=int(self.out_of_sample.start_ns),
            )
        actual_gap = int(self.out_of_sample.start_ns) - int(self.in_sample.end_ns)
        if actual_gap < self.purge_ns:
            raise InvariantViolation(
                "La separacion real es menor que la purga exigida",
                fold=self.index,
                required=self.purge_ns,
                actual=actual_gap,
            )

    @property
    def is_ratio(self) -> float:
        """Proporcion de tiempo dedicada al in-sample.

        Un valor muy alto indica poco OOS y por tanto poca evidencia; uno muy
        bajo indica ajuste sobre una muestra insuficiente. Se reporta en cada
        corrida para que la eleccion sea visible y discutible.
        """
        total = self.in_sample.duration_ns + self.out_of_sample.duration_ns
        return self.in_sample.duration_ns / total if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "in_sample": self.in_sample.to_dict(),
            "out_of_sample": self.out_of_sample.to_dict(),
            "purge_ns": self.purge_ns,
            "is_ratio": self.is_ratio,
        }

    def __repr__(self) -> str:
        return f"Fold(#{self.index} IS={self.in_sample} OOS={self.out_of_sample})"


__all__ = ["Fold", "TimeRange"]
