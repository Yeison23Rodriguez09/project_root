"""Determinismo, aislamiento y auditabilidad del bus de eventos.

El reloj se inyecta como contador, así que el tiempo también es determinista y
las aserciones sobre secuencia no dependen de la resolución del reloj real.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import InvariantViolation
from app.events.bus import ALL_EVENTS, ErrorPolicy, EventBus, SubscriberFailed
from app.events.event import Event, validate_payload
from app.events.recorder import Recorder, ReplayError, replay

pytestmark = pytest.mark.unit


class Ticker:
    """Reloj inyectado que avanza un nanosegundo por lectura."""

    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        self.value += 1
        return self.value


def bus(**kwargs: object) -> EventBus:
    return EventBus(Ticker(), **kwargs)  # type: ignore[arg-type]


def collector(log: list[str], name: str):  # noqa: ANN201
    def listen(event: Event) -> None:
        log.append(f"{name}:{event.name}")

    return listen


# ---------------------------------------------------------------------------
# Inmutabilidad
# ---------------------------------------------------------------------------


def test_payload_cannot_be_mutated_after_construction() -> None:
    """Ni siquiera por quien conserve el diccionario original."""
    original = {"price": 1.0}
    event = Event(name="Tick", payload=original)
    original["price"] = 999.0
    assert event.payload["price"] == 1.0
    with pytest.raises(TypeError):
        event.payload["price"] = 2.0  # type: ignore[index]


def test_unpublished_event_has_no_sequence() -> None:
    with pytest.raises(InvariantViolation):
        _ = Event(name="Tick").sequence


def test_non_serializable_payload_is_rejected() -> None:
    """Un evento con un objeto vivo dentro no se puede grabar ni reproducir, y
    eso solo se descubriria al auditar una corrida antigua."""
    with pytest.raises(InvariantViolation):
        validate_payload(Event(name="Tick", payload={"broker": object()}))


# ---------------------------------------------------------------------------
# Determinismo
# ---------------------------------------------------------------------------


def test_sequence_gives_a_total_order() -> None:
    """Dos eventos en el mismo instante siguen teniendo orden. Ordenar por
    timestamp produciria empates resueltos de forma distinta en cada ejecucion."""
    frozen = EventBus(lambda: 42)
    first = frozen.publish(Event(name="A"), source="test")
    second = frozen.publish(Event(name="B"), source="test")
    assert (first.sequence, second.sequence) == (1, 2)


def test_delivery_order_is_priority_then_registration() -> None:
    log: list[str] = []
    event_bus = bus()
    event_bus.subscribe("Tick", collector(log, "tarde"), name="tarde", priority=200)
    event_bus.subscribe("Tick", collector(log, "pronto"), name="pronto", priority=10)
    event_bus.subscribe("Tick", collector(log, "medio_a"), name="medio_a")
    event_bus.subscribe("Tick", collector(log, "medio_b"), name="medio_b")
    event_bus.publish(Event(name="Tick"), source="test")
    assert log == ["pronto:Tick", "medio_a:Tick", "medio_b:Tick", "tarde:Tick"]


def test_wildcard_subscribers_also_respect_priority() -> None:
    log: list[str] = []
    event_bus = bus()
    event_bus.subscribe("Tick", collector(log, "exacto"), name="exacto", priority=50)
    event_bus.subscribe(ALL_EVENTS, collector(log, "todos"), name="todos", priority=10)
    event_bus.publish(Event(name="Tick"), source="test")
    assert log == ["todos:Tick", "exacto:Tick"]


# ---------------------------------------------------------------------------
# Aislamiento de fallos
# ---------------------------------------------------------------------------


def test_a_failing_subscriber_does_not_stop_the_others() -> None:
    """El hecho ya ocurrio; que un oyente se rompa no lo deshace."""
    log: list[str] = []
    event_bus = bus()

    def broken(_e: Event) -> None:
        raise RuntimeError("roto")

    event_bus.subscribe("Tick", broken, name="roto", priority=10)
    event_bus.subscribe("Tick", collector(log, "sano"), name="sano", priority=20)
    report = event_bus.publish(Event(name="Tick"), source="test")

    assert log == ["sano:Tick"]
    assert not report.ok
    assert report.failures[0][0] == "roto"


def test_failures_are_queryable_afterwards() -> None:
    """En modo COLLECT es la unica forma de enterarse: un doctor que no mire
    aqui daria por bueno un sistema con oyentes caidos."""
    event_bus = bus()
    event_bus.subscribe("Tick", lambda _e: 1 / 0, name="roto")
    event_bus.publish(Event(name="Tick"), source="test")
    assert len(event_bus.failures()) == 1
    assert event_bus.describe()["failed_deliveries"] == 1


def test_raise_policy_aborts_the_run() -> None:
    """En CI un suscriptor roto debe romper la corrida, no degradarla en
    silencio y producir metricas incompletas que parecen completas."""
    event_bus = bus(policy=ErrorPolicy.RAISE)
    event_bus.subscribe("Tick", lambda _e: 1 / 0, name="roto")
    with pytest.raises(SubscriberFailed):
        event_bus.publish(Event(name="Tick"), source="test")


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


def test_middlewares_wrap_delivery_in_registration_order() -> None:
    log: list[str] = []
    event_bus = bus()

    def outer(event: Event, nxt) -> None:  # noqa: ANN001
        log.append("outer:in")
        nxt(event)
        log.append("outer:out")

    def inner(event: Event, nxt) -> None:  # noqa: ANN001
        log.append("inner:in")
        nxt(event)
        log.append("inner:out")

    event_bus.use(outer, name="outer")
    event_bus.use(inner, name="inner")
    event_bus.subscribe("Tick", collector(log, "sub"), name="sub")
    event_bus.publish(Event(name="Tick"), source="test")

    assert log == ["outer:in", "inner:in", "sub:Tick", "inner:out", "outer:out"]


# ---------------------------------------------------------------------------
# Grabacion y reproduccion
# ---------------------------------------------------------------------------


def test_recorder_captures_before_anyone_can_fail() -> None:
    """Si grabara al final, un fallo intermedio dejaria fuera del registro justo
    el evento que causo el problema."""
    recorder = Recorder()
    event_bus = bus()
    recorder.attach(event_bus)
    event_bus.subscribe("Tick", lambda _e: 1 / 0, name="roto", priority=50)
    event_bus.publish(Event(name="Tick"), source="test")
    assert len(recorder) == 1


def test_recorder_detects_gaps_in_the_sequence() -> None:
    """Un hueco invalida la grabacion como registro de auditoria."""
    recorder = Recorder()
    event_bus = bus()
    recorder.attach(event_bus)
    for name in ("A", "B", "C"):
        event_bus.publish(Event(name=name), source="test")
    assert recorder.is_contiguous()

    del recorder.events[1]
    assert not recorder.is_contiguous()


def test_correlation_groups_everything_from_one_stimulus() -> None:
    recorder = Recorder()
    event_bus = bus()
    recorder.attach(event_bus)
    event_bus.publish(Event(name="Bar"), source="feed", correlation_id="c1")
    event_bus.publish(Event(name="Signal"), source="signals", correlation_id="c1")
    event_bus.publish(Event(name="Bar"), source="feed", correlation_id="c2")
    assert [e.name for e in recorder.by_correlation("c1")] == ["Bar", "Signal"]


def test_bounded_recorder_drops_the_oldest() -> None:
    """En vivo el registro no puede crecer sin limite, y lo que se diagnostica
    casi siempre es lo ultimo que ocurrio."""
    recorder = Recorder(max_events=2)
    event_bus = bus()
    recorder.attach(event_bus)
    for name in ("A", "B", "C"):
        event_bus.publish(Event(name=name), source="test")
    assert [e.name for e in recorder] == ["B", "C"]


def test_replay_preserves_order_and_correlation() -> None:
    recorder = Recorder()
    origin = bus()
    recorder.attach(origin)
    origin.publish(Event(name="A", payload={"v": 1}), source="test", correlation_id="c1")
    origin.publish(Event(name="B", payload={"v": 2}), source="test", correlation_id="c1")

    target = Recorder()
    fresh = bus()
    target.attach(fresh)
    replay(list(recorder), fresh)

    assert [e.name for e in target] == ["A", "B"]
    assert all(e.meta is not None and e.meta.correlation_id == "c1" for e in target)
    assert [e.payload["v"] for e in target] == [1, 2]


def test_replay_refuses_unpublished_events() -> None:
    """Reproducir un evento sin sellar produciria un orden inventado."""
    with pytest.raises(ReplayError):
        replay([Event(name="Huerfano")], bus())
