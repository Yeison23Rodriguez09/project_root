"""Agregacion del trafico del bus. Suscriptor real y componente del ciclo de vida.

Es el consumidor que ADR-0006 exige: *"cada bloque de la Fase 3 debe tener al
menos un consumidor real en la propia Fase 3 antes de considerarse cerrado. Si
un bloque no tiene consumidor, es especulacion y se recorta."* Sin esto el bus
seria un mecanismo probado con dobles y jamas ejercitado.

Que hace y que NO hace. Cuenta: eventos por nombre, por fuente y por corrida, y
la ventana temporal cubierta. No guarda los eventos: eso es trabajo de
`Recorder`, que graba la secuencia completa para auditarla. La diferencia es de
proposito y de coste -`Recorder` crece con el numero de eventos y `RuntimeMetrics`
no-, y por eso son dos objetos y no uno con un flag.

Ciclo de vida. Implementa `LifecyclePort` completo. `initialize` prepara los
contadores, `load` se engancha al bus, `warmup` no hace nada -no necesita
historia-, `start` marca el inicio de la ventana de medida, `stop` la cierra y
`dispose` suelta la suscripcion. Las seis fases existen aunque dos sean triviales
porque el contrato son seis: un componente que implementa cuatro obliga al
contenedor a preguntar cuales, y esa pregunta es la que convierte un ciclo de
vida en una sugerencia.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.events.bus import ALL_EVENTS, EventBus
from app.events.event import Event

#: Nombre con el que el componente se suscribe. Aparece en `bus.describe()` y en
#: los informes de entrega, asi que es parte de la superficie observable.
SUBSCRIBER_NAME = "runtime_metrics"

#: Prioridad de la suscripcion. Por encima del grabador (0) y por debajo del
#: defecto (100): las metricas deben contar el evento antes de que un suscriptor
#: de negocio pueda fallar y abortar la entrega, pero despues de que quede
#: grabado, porque la grabacion es la evidencia y esta es solo su resumen.
SUBSCRIBER_PRIORITY = 10


@dataclass(slots=True)
class RuntimeMetrics:
    """Contadores agregados del bus, por nombre, fuente y corrida.

    Mutable por diseno, igual que `Recorder` y por el mismo motivo: es un
    acumulador. La inmutabilidad esta donde importa -en `Event`, que es lo que se
    persiste y se compara- y no en el contador que lo resume.

    Attributes:
        by_event: Cuantos eventos de cada nombre. Es la primera vista de una
            corrida: si `signal.rejected` supera diez veces a `signal.emitted`,
            el problema esta en los filtros y no en la senal.
        by_source: Cuantos ha emitido cada componente. Detecta al emisor mudo,
            que es el sintoma de un motor que no se esta ejecutando.
        by_run: Trafico por `run_id`. En paper y live conviven varias corridas en
            el mismo proceso y agregarlas juntas haria ilegible el total.
        first_ns / last_ns: Ventana temporal cubierta, en tiempo del bus.
    """

    by_event: dict[str, int] = field(default_factory=dict)
    by_source: dict[str, int] = field(default_factory=dict)
    by_run: dict[str, int] = field(default_factory=dict)
    first_ns: int | None = None
    last_ns: int | None = None
    phase: str = "created"
    _bus: EventBus | None = field(default=None, repr=False)
    _subscribed: bool = field(default=False, repr=False)

    # -- suscripcion ---------------------------------------------------------

    def __call__(self, event: Event) -> None:
        """Recibe un evento del bus y actualiza los contadores.

        No lanza nunca. Un fallo en la contabilidad no puede tumbar la entrega:
        el hecho ya ocurrio y los demas oyentes deben enterarse. Por eso el
        acceso a los metadatos es defensivo -un evento sin sellar no deberia
        llegar aqui, pero si llega se cuenta igual y no rompe la corrida-.
        """
        self.by_event[event.name] = self.by_event.get(event.name, 0) + 1
        meta = event.meta
        if meta is None:
            return
        self.by_source[meta.source] = self.by_source.get(meta.source, 0) + 1
        if meta.run_id:
            self.by_run[meta.run_id] = self.by_run.get(meta.run_id, 0) + 1
        if self.first_ns is None:
            self.first_ns = meta.timestamp_ns
        self.last_ns = meta.timestamp_ns

    def attach(self, bus: EventBus) -> None:
        """Se engancha a todos los eventos del bus.

        Idempotente: llamarlo dos veces no duplica la suscripcion. En vivo una
        reconexion puede reintentar el enganche, y contar cada evento dos veces
        produciria un informe que dice el doble de lo que ocurrio.
        """
        if self._subscribed:
            return
        bus.subscribe(ALL_EVENTS, self, name=SUBSCRIBER_NAME, priority=SUBSCRIBER_PRIORITY)
        self._bus = bus
        self._subscribed = True

    # -- ciclo de vida (LifecyclePort) ---------------------------------------

    def initialize(self) -> None:
        """Deja los contadores a cero. Sin I/O."""
        self.by_event.clear()
        self.by_source.clear()
        self.by_run.clear()
        self.first_ns = None
        self.last_ns = None
        self.phase = "initialized"

    def load(self) -> None:
        """Se engancha al bus inyectado en construccion.

        Es `load` y no `initialize` porque suscribirse toca un objeto externo al
        componente. Si el bus no estuviera disponible, el fallo seria del mundo y
        podria reintentarse, que es exactamente la frontera que separa las dos
        fases en `LifecyclePort`.
        """
        if self._bus is not None:
            self.attach(self._bus)
        self.phase = "loaded"

    def warmup(self) -> None:
        """Sin calentamiento: la contabilidad no necesita historia previa.

        La fase existe aunque no haga trabajo. `LifecyclePort` son seis metodos y
        un componente que implementa cuatro obliga al contenedor a preguntar
        cuales, que es como un ciclo de vida se degrada a sugerencia.
        """
        self.phase = "warmed"

    def start(self) -> None:
        """Marca el componente como operativo.

        La ventana temporal de la medida NO se toma aqui: sale de los metadatos
        de los eventos (`first_ns` / `last_ns`), que llevan el instante inyectado
        por `ClockPort` en el bus. Leer un reloj propio en este punto introduciria
        una segunda fuente de tiempo y dos corridas deterministas podrian
        declarar duraciones distintas.
        """
        self.phase = "started"

    def stop(self) -> None:
        """Deja de considerarse operativo. NO borra los contadores.

        Los contadores sobreviven a `stop` a proposito: el informe de una corrida
        se consulta DESPUES de pararla, y vaciarlos aqui haria imposible saber
        que ocurrio.
        """
        self.phase = "stopped"

    def dispose(self) -> None:
        """Suelta la referencia al bus. Idempotente y no puede fallar.

        No se da de baja la suscripcion: `EventBus` no expone `unsubscribe`
        porque un bus del que se puede descolgar un oyente a mitad de corrida
        deja huecos en la auditoria. Soltar la referencia basta para que el
        componente no vuelva a engancharse tras un `dispose`.
        """
        self._bus = None
        self.phase = "disposed"

    # -- consulta ------------------------------------------------------------

    @property
    def total(self) -> int:
        return sum(self.by_event.values())

    @property
    def span_ns(self) -> int:
        """Duracion cubierta por los eventos contados, en nanosegundos."""
        if self.first_ns is None or self.last_ns is None:
            return 0
        return self.last_ns - self.first_ns

    def top_events(self, limit: int = 10) -> list[tuple[str, int]]:
        """Los eventos mas frecuentes, de mayor a menor.

        El desempate es alfabetico y no arbitrario: dos nombres con el mismo
        recuento deben aparecer siempre en el mismo orden, o dos ejecuciones
        identicas producirian informes distintos y romperian P1.
        """
        return sorted(self.by_event.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "distinct_events": len(self.by_event),
            "span_ns": self.span_ns,
            "subscribed": self._subscribed,
            "phase": self.phase,
            "by_event": dict(sorted(self.by_event.items())),
            "by_source": dict(sorted(self.by_source.items())),
            "by_run": dict(sorted(self.by_run.items())),
        }

    def describe(self) -> Mapping[str, Any]:
        """Resumen corto para `qp status` y `qp doctor`."""
        return {
            "total": self.total,
            "distinct_events": len(self.by_event),
            "top": self.top_events(5),
        }

    def __repr__(self) -> str:
        return f"RuntimeMetrics({self.total} eventos, {len(self.by_event)} tipos)"


def metrics_for(bus: EventBus) -> RuntimeMetrics:
    """Contador ya enganchado a un bus.

    Atajo para la raiz de composicion, que es el unico sitio donde se conocen a
    la vez el bus y el componente.
    """
    metrics = RuntimeMetrics(_bus=bus)
    metrics.attach(bus)
    return metrics


__all__ = ["SUBSCRIBER_NAME", "SUBSCRIBER_PRIORITY", "RuntimeMetrics", "metrics_for"]
