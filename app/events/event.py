"""Evento: dato inmutable con metadatos de trazabilidad.

Un evento es un hecho ocurrido, no una orden. `OrderCreated` describe algo que ya
pasó; nadie puede "no aceptarlo". Esa distincion es la que permite que un
suscriptor falle sin comprometer al emisor: el hecho ocurrio igual.

Inmutabilidad: `Event` es `frozen` y su carga se guarda tras una copia envuelta
en `MappingProxyType`. Python no permite congelar en profundidad valores
anidados arbitrarios; lo que si se garantiza es que nadie sustituya ni anada
claves despues de publicar. La convencion -cargas de primitivas- cubre el resto,
y `validate_payload` la comprueba cuando se activa.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Self

from app.core.exceptions import InvariantViolation


@dataclass(frozen=True, slots=True)
class EventMeta:
    """Contexto de trazabilidad que acompana a todo evento.

    Attributes:
        sequence: Numero de orden dentro del bus, empezando en 1. Es la clave del
            determinismo: dos eventos emitidos en el mismo nanosegundo siguen
            teniendo un orden total. Ordenar por `timestamp_ns` produciria
            empates que se resolverian de forma distinta en cada ejecucion.
        timestamp_ns: Instante de publicacion, inyectado. El bus no lee el reloj
            del sistema; se lo dan.
        correlation_id: Une todos los eventos derivados de un mismo estimulo. Sin
            el, reconstruir "que paso a partir de esta vela" exige cruzar
            timestamps a mano.
        causation_id: Identificador del evento que provoco este. `correlation_id`
            agrupa; `causation_id` encadena. Con los dos se reconstruye el arbol
            completo, no solo el conjunto.
        source: Componente emisor.
        run_id: Corrida a la que pertenece.
    """

    sequence: int
    timestamp_ns: int
    source: str
    correlation_id: str = ""
    causation_id: str = ""
    run_id: str = ""

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise InvariantViolation("La secuencia de un evento empieza en 1")
        if not self.source.strip():
            raise InvariantViolation("Un evento debe declarar su emisor")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp_ns": self.timestamp_ns,
            "source": self.source,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "run_id": self.run_id,
        }


@dataclass(frozen=True, slots=True)
class Event:
    """Hecho ocurrido, con nombre estable y carga inmutable.

    `name` es la clave de suscripcion y se persiste en las trazas de auditoria.
    Cambiarlo invalida cualquier grabacion anterior que lo referencie, igual que
    cambiar el nombre de un bloque invalida los `StrategySpec` que lo usan.
    """

    name: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    meta: EventMeta | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise InvariantViolation("Un evento necesita nombre")
        # Copia envuelta: nadie puede anadir, sustituir ni borrar claves despues
        # de construir el evento, ni siquiera quien conserve el dict original.
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    def with_meta(self, meta: EventMeta) -> Self:
        """Devuelve una copia sellada con sus metadatos.

        El emisor construye el evento sin metadatos; el bus los añade al
        publicar. Asi el emisor no necesita conocer ni el reloj ni el contador de
        secuencia, que son responsabilidad del bus.
        """
        return type(self)(name=self.name, payload=dict(self.payload), meta=meta)

    @property
    def sequence(self) -> int:
        if self.meta is None:
            raise InvariantViolation(
                "Evento sin publicar: no tiene secuencia", event=self.name
            )
        return self.meta.sequence

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "payload": dict(self.payload),
            "meta": self.meta.to_dict() if self.meta else None,
        }

    def __repr__(self) -> str:
        seq = self.meta.sequence if self.meta else "-"
        return f"Event(#{seq} {self.name})"


def validate_payload(event: Event) -> None:
    """Comprueba que la carga sea serializable y por tanto grabable.

    Un evento con un objeto vivo dentro no se puede grabar ni reproducir, y su
    presencia solo se descubriria al intentar auditar una corrida antigua, que es
    el peor momento posible.

    Raises:
        InvariantViolation: si algun valor no es una primitiva serializable.
    """
    allowed = (bool, int, float, str, type(None))
    for key, value in event.payload.items():
        if isinstance(value, allowed):
            continue
        if isinstance(value, (list, tuple)) and all(
            isinstance(item, allowed) for item in value
        ):
            continue
        raise InvariantViolation(
            "Carga de evento no serializable",
            event=event.name,
            key=key,
            type=type(value).__name__,
        )


__all__ = ["Event", "EventMeta", "validate_payload"]
