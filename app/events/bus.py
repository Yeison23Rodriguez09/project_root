"""Bus de eventos sincrono, determinista y con aislamiento de fallos.

Sincrono a proposito. Un bus asincrono introduce un orden de entrega que depende
del planificador, y con el desaparece el determinismo: la misma corrida
produciria secuencias distintas. Cuando haga falta concurrencia sera en los
bordes -un sink que escribe a disco en otro hilo-, nunca en el encaminamiento.

Tres garantias:

* **Orden estable.** Los suscriptores se invocan por (prioridad, orden de alta).
  Nunca por el orden de un `set` ni de un diccionario.
* **Aislamiento por suscriptor.** Un suscriptor que falla no impide que los
  demas reciban el evento. El hecho ya ocurrio; que un oyente se rompa no lo
  deshace.
* **Sin reloj propio.** El instante se inyecta como `Callable[[], int]`. El bus
  vive en capa `core`, donde P6 prohibe el reloj de pared, y no puede importar
  `ClockPort` de `shared` sin subir de capa.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.exceptions import PlatformError
from app.events.event import Event, EventMeta, validate_payload

#: Un suscriptor recibe el evento y no devuelve nada. Si necesitara devolver
#: algo, no seria un suscriptor: seria una llamada, y el emisor tendria que
#: conocerlo.
Subscriber = Callable[[Event], None]

#: Un middleware envuelve la entrega completa de un evento.
Middleware = Callable[[Event, Callable[[Event], None]], None]

#: Comodin de suscripcion a todos los eventos.
ALL_EVENTS = "*"


class EventBusError(PlatformError):
    code = "EVENT_BUS_ERROR"


class SubscriberFailed(EventBusError):
    """Un suscriptor lanzo durante la entrega."""

    code = "SUBSCRIBER_FAILED"


class ErrorPolicy(StrEnum):
    """Que hacer cuando un suscriptor falla.

    `COLLECT` es el defecto operativo: el hecho ocurrio y los demas oyentes deben
    enterarse. `RAISE` es el defecto en CI y en benchmark, donde un suscriptor
    roto debe romper la corrida en lugar de degradarla en silencio y producir
    metricas incompletas que parecen completas.
    """

    COLLECT = "collect"
    RAISE = "raise"


@dataclass(frozen=True, slots=True)
class Subscription:
    """Alta de un suscriptor, con su desempate declarado.

    `order` se asigna al suscribir y es lo que hace la entrega determinista entre
    suscriptores de igual prioridad. Sin el, el orden dependeria de la estructura
    interna que los almacene.
    """

    event_name: str
    subscriber: Subscriber
    name: str
    priority: int = 100
    order: int = 0

    @property
    def sort_key(self) -> tuple[int, int]:
        return (self.priority, self.order)


@dataclass(slots=True)
class DeliveryReport:
    """Que suscriptores recibieron un evento y cuales fallaron.

    Los fallos son datos, no excepciones perdidas. Sin este informe, un
    suscriptor roto en modo `COLLECT` desapareceria sin dejar rastro, que es
    exactamente lo contrario de lo que un bus con aislamiento debe ofrecer.
    """

    event: str
    sequence: int
    delivered: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "sequence": self.sequence,
            "delivered": list(self.delivered),
            "failures": [{"subscriber": s, "error": e} for s, e in self.failures],
        }


class EventBus:
    """Encaminador sincrono de eventos.

    Sin estado global: es una instancia. Dos buses pueden coexistir -uno real y
    uno de test- sin compartir nada. Un bus global haria que el orden de
    importacion de los tests alterara su resultado.
    """

    __slots__ = ("_clock", "_middlewares", "_policy", "_reports", "_sequence", "_subs", "_validate")

    def __init__(
        self,
        clock: Callable[[], int],
        *,
        policy: ErrorPolicy = ErrorPolicy.COLLECT,
        validate: bool = True,
    ) -> None:
        """
        Args:
            clock: Devuelve el instante actual en nanosegundos. Inyectado.
            policy: Que hacer ante un suscriptor que falla.
            validate: Comprobar que la carga es serializable al publicar. Se
                puede desactivar en rutas calientes, asumiendo que un evento no
                grabable solo se descubrira al auditar.
        """
        self._clock = clock
        self._policy = policy
        self._validate = validate
        self._subs: dict[str, list[Subscription]] = {}
        self._sequence: int = 0
        self._middlewares: list[tuple[str, Middleware]] = []
        self._reports: list[DeliveryReport] = []

    # -- suscripcion --------------------------------------------------------

    def subscribe(
        self,
        event_name: str,
        subscriber: Subscriber,
        *,
        name: str,
        priority: int = 100,
    ) -> Subscription:
        """Da de alta un suscriptor.

        Args:
            event_name: Nombre exacto, o `ALL_EVENTS` para recibirlo todo.
            priority: Menor va primero. Se usa para que un grabador de auditoria
                registre el evento antes de que cualquier otro pueda fallar.
        """
        subscription = Subscription(
            event_name=event_name,
            subscriber=subscriber,
            name=name,
            priority=priority,
            order=sum(len(v) for v in self._subs.values()),
        )
        self._subs.setdefault(event_name, []).append(subscription)
        return subscription

    def use(self, middleware: Middleware, *, name: str) -> None:
        """Añade un middleware que envuelve la entrega completa.

        Se aplican en orden de registro, el primero por fuera. Envuelven la
        entrega entera y no cada suscriptor: medir "cuanto costo este evento" es
        util; medir cada oyente por separado es el trabajo de las metricas.
        """
        self._middlewares.append((name, middleware))

    def subscribers_for(self, event_name: str) -> tuple[Subscription, ...]:
        """Suscriptores que recibiran un evento, en orden de entrega."""
        matching = [*self._subs.get(event_name, []), *self._subs.get(ALL_EVENTS, [])]
        return tuple(sorted(matching, key=lambda s: s.sort_key))

    # -- publicacion --------------------------------------------------------

    def publish(
        self,
        event: Event,
        *,
        source: str,
        correlation_id: str = "",
        causation_id: str = "",
        run_id: str = "",
    ) -> DeliveryReport:
        """Sella el evento con sus metadatos y lo entrega.

        El emisor no conoce el reloj ni el contador de secuencia: los añade el
        bus. Eso permite emitir el mismo evento desde un backtest y desde live
        sin que el emisor sepa en cual esta.
        """
        if self._validate:
            validate_payload(event)

        self._sequence += 1
        sealed = event.with_meta(
            EventMeta(
                sequence=self._sequence,
                timestamp_ns=self._clock(),
                source=source,
                correlation_id=correlation_id,
                causation_id=causation_id,
                run_id=run_id,
            )
        )

        report = DeliveryReport(event=sealed.name, sequence=self._sequence)

        def deliver(current: Event) -> None:
            for subscription in self.subscribers_for(current.name):
                try:
                    subscription.subscriber(current)
                    report.delivered.append(subscription.name)
                except Exception as exc:
                    detail = f"{type(exc).__name__}: {exc}"
                    report.failures.append((subscription.name, detail))
                    if self._policy is ErrorPolicy.RAISE:
                        raise SubscriberFailed(
                            "Un suscriptor fallo durante la entrega",
                            event=current.name,
                            subscriber=subscription.name,
                            cause=detail,
                        ) from exc

        chain = deliver
        for _name, middleware in reversed(self._middlewares):
            chain = _wrap(middleware, chain)
        chain(sealed)

        self._reports.append(report)
        return report

    # -- inspeccion ---------------------------------------------------------

    @property
    def sequence(self) -> int:
        """Numero de eventos publicados. Es tambien el ultimo `sequence`."""
        return self._sequence

    def reports(self) -> tuple[DeliveryReport, ...]:
        return tuple(self._reports)

    def failures(self) -> tuple[DeliveryReport, ...]:
        """Entregas con al menos un suscriptor roto.

        En modo `COLLECT` es la unica forma de enterarse. Un `qp doctor` que no
        mire aqui daria por bueno un sistema con oyentes caidos.
        """
        return tuple(r for r in self._reports if not r.ok)

    def __iter__(self) -> Iterator[Subscription]:
        return iter(
            sorted(
                (s for subs in self._subs.values() for s in subs),
                key=lambda s: s.sort_key,
            )
        )

    def describe(self) -> dict[str, Any]:
        return {
            "published": self._sequence,
            "policy": str(self._policy),
            "middlewares": [name for name, _m in self._middlewares],
            "subscriptions": [
                {
                    "event": s.event_name,
                    "subscriber": s.name,
                    "priority": s.priority,
                    "order": s.order,
                }
                for s in self
            ],
            "failed_deliveries": len(self.failures()),
        }

    def __repr__(self) -> str:
        total = sum(len(v) for v in self._subs.values())
        return f"EventBus({total} suscriptores, {self._sequence} publicados)"


def _wrap(middleware: Middleware, nxt: Callable[[Event], None]) -> Callable[[Event], None]:
    """Cierra un middleware sobre el siguiente eslabon.

    Funcion aparte y no `lambda` dentro del bucle: un `lambda` capturaria la
    variable del bucle por referencia y todos los middlewares acabarian
    llamando al ultimo eslabon.
    """

    def link(event: Event) -> None:
        middleware(event, nxt)

    return link


def sequence_of(events: Sequence[Event]) -> list[int]:
    """Secuencias de una lista de eventos. Utilidad para aserciones."""
    return [e.sequence for e in events]


__all__ = [
    "ALL_EVENTS",
    "DeliveryReport",
    "ErrorPolicy",
    "EventBus",
    "EventBusError",
    "Middleware",
    "Subscriber",
    "SubscriberFailed",
    "Subscription",
    "sequence_of",
]
