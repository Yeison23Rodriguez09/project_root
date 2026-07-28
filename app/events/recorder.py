"""Grabacion y reproduccion de la secuencia de eventos.

Es lo que convierte el bus en un instrumento de auditoria. Con la grabacion
completa de una corrida se puede responder, meses despues, en que orden ocurrio
todo y con que carga, sin volver a ejecutar nada.

La reproduccion sirve para dos cosas distintas y ambas valiosas: comprobar que
un analizador nuevo produce el mismo resultado sobre una corrida antigua, y
diagnosticar un fallo de produccion en un entorno de desarrollo sin broker, sin
datos de mercado y sin esperar a que el mercado vuelva a ponerse igual.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.core.exceptions import PlatformError
from app.events.bus import ALL_EVENTS, EventBus
from app.events.event import Event


class ReplayError(PlatformError):
    code = "REPLAY_ERROR"


@dataclass(slots=True)
class Recorder:
    """Suscriptor que guarda todo lo que pasa por el bus, en orden.

    Se suscribe con prioridad muy baja -es decir, se ejecuta primero- para que el
    evento quede registrado ANTES de que ningun otro suscriptor pueda fallar. Si
    grabara al final, un fallo intermedio dejaria fuera del registro justo el
    evento que causo el problema, que es el unico que interesa.
    """

    events: list[Event] = field(default_factory=list)
    max_events: int | None = None

    #: Prioridad de grabacion. Muy por debajo del defecto (100) para ir primero.
    #:
    #: `ClassVar` y no campo de dataclass. Sin la anotacion seria un campo con
    #: valor por defecto, es decir un parametro del constructor: cualquiera
    #: podria construir `Recorder(max_events=None, PRIORITY=500)` y la grabacion
    #: pasaria a ocurrir DESPUES de los suscriptores que pueden fallar. La
    #: garantia de que el evento queda registrado antes de que nadie lo rompa
    #: dejaria de ser una propiedad del tipo y pasaria a depender de quien lo
    #: construya.
    PRIORITY: ClassVar[int] = 0

    def __call__(self, event: Event) -> None:
        if self.max_events is not None and len(self.events) >= self.max_events:
            # Se descarta el mas antiguo. En vivo el registro no puede crecer sin
            # limite, y perder el principio es preferible a perder el presente:
            # lo que se diagnostica casi siempre es lo ultimo que ocurrio.
            self.events.pop(0)
        self.events.append(event)

    def attach(self, bus: EventBus, *, name: str = "recorder") -> None:
        """Se engancha a todos los eventos del bus."""
        bus.subscribe(ALL_EVENTS, self, name=name, priority=self.PRIORITY)

    # -- consulta -----------------------------------------------------------

    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self) -> Iterator[Event]:
        return iter(self.events)

    def by_name(self, name: str) -> list[Event]:
        return [e for e in self.events if e.name == name]

    def by_correlation(self, correlation_id: str) -> list[Event]:
        """Todos los eventos derivados de un mismo estimulo.

        Es la consulta que reconstruye "que paso a partir de esta vela" sin
        cruzar timestamps a mano.
        """
        return [
            e
            for e in self.events
            if e.meta is not None and e.meta.correlation_id == correlation_id
        ]

    def counts(self) -> dict[str, int]:
        """Cuantos eventos de cada tipo. Primera vista de una corrida."""
        tally: dict[str, int] = {}
        for event in self.events:
            tally[event.name] = tally.get(event.name, 0) + 1
        return dict(sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])))

    def is_contiguous(self) -> bool:
        """Comprueba que no falte ningun evento en la secuencia grabada.

        Un hueco significa que algo se publico y no llego al grabador, lo que
        invalida la grabacion como registro de auditoria. Se comprueba antes de
        persistirla, no despues.
        """
        if not self.events:
            return True
        sequences = [e.sequence for e in self.events]
        return sequences == list(range(sequences[0], sequences[0] + len(sequences)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_events": len(self.events),
            "contiguous": self.is_contiguous(),
            "counts": self.counts(),
            "events": [e.to_dict() for e in self.events],
        }


def replay(
    events: Sequence[Event],
    bus: EventBus,
    *,
    source: str = "replay",
) -> list[Any]:
    """Reproduce una secuencia grabada sobre un bus nuevo.

    Los eventos se republican en el orden grabado, conservando su
    `correlation_id` y su `causation_id` originales. Lo que NO se conserva es la
    secuencia: el bus nuevo asigna la suya. Es deliberado -reproducir es un hecho
    distinto de la corrida original y debe distinguirse-, y el vinculo con el
    original se mantiene por `correlation_id`.

    Raises:
        ReplayError: si algun evento no fue publicado, es decir no tiene
            metadatos. Reproducir un evento sin sellar produciria un orden
            inventado.
    """
    unsealed = [e.name for e in events if e.meta is None]
    if unsealed:
        raise ReplayError(
            "No se puede reproducir un evento que nunca se publico",
            events=sorted(set(unsealed)),
        )

    ordered = sorted(events, key=lambda e: e.sequence)
    return [
        bus.publish(
            Event(name=e.name, payload=dict(e.payload)),
            source=source,
            correlation_id=e.meta.correlation_id if e.meta else "",
            causation_id=e.meta.causation_id if e.meta else "",
            run_id=e.meta.run_id if e.meta else "",
        )
        for e in ordered
    ]


__all__ = ["Recorder", "ReplayError", "replay"]
