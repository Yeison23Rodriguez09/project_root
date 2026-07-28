"""Observabilidad: sink estructurado, contexto de corrida y metricas del bus.

Estos tests son la evidencia de que ADR-0008 se uso para lo que dice: un
suscriptor REAL sobre un bus REAL. No hay dobles del bus ni del sink -el bus es
`EventBus` y el sink escribe JSON de verdad a un flujo inyectado-, porque un
consumidor simulado demostraria que el simulacro funciona.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app.core.types import TimestampNs
from app.events.bus import ErrorPolicy, EventBus
from app.events.event import Event
from app.events.recorder import Recorder
from app.monitoring.run_context import (
    DIRTY_SUFFIX,
    UNKNOWN_CODE_VERSION,
    RunContext,
    code_version,
    derive_run_id,
)
from app.monitoring.runtime_metrics import (
    SUBSCRIBER_NAME,
    SUBSCRIBER_PRIORITY,
    RuntimeMetrics,
    metrics_for,
)
from app.monitoring.sink import NullEventSink, StructlogEventSink

ROOT = Path(__file__).resolve().parent.parent


def _bus(start_ns: int = 1_000) -> EventBus:
    """Bus con reloj determinista que avanza un nanosegundo por lectura.

    Avanza para que `first_ns` y `last_ns` sean distinguibles; si el reloj
    estuviera del todo detenido, `span_ns` seria siempre cero y el test no
    distinguiria "mide la ventana" de "devuelve cero".
    """
    counter = {"t": start_ns}

    def clock() -> int:
        counter["t"] += 1
        return counter["t"]

    return EventBus(clock, policy=ErrorPolicy.RAISE)


# ---------------------------------------------------------------------------
# 1. Sink estructurado
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sink_emits_valid_json_with_the_event_name() -> None:
    """Lo que sale es JSON parseable, no texto con aspecto de JSON."""
    stream = io.StringIO()
    StructlogEventSink(stream=stream).emit("platform.started", components=6, mode="ci")
    payload = json.loads(stream.getvalue())
    assert payload["event"] == "platform.started"
    assert payload["components"] == 6
    assert payload["mode"] == "ci"
    assert "timestamp" in payload, "sin marca temporal el evento no es agregable"


@pytest.mark.unit
def test_sink_keys_are_sorted_so_output_is_byte_stable() -> None:
    """Mismo contenido, mismos bytes.

    Es P1 aplicado a la observabilidad: si el orden de claves variase, dos
    corridas identicas producirian logs que `diff` marca como distintos y la
    comparacion dejaria de servir para detectar cambios reales.
    """
    first, second = io.StringIO(), io.StringIO()
    StructlogEventSink(stream=first).emit("e", zeta=1, alpha=2, mid=3)
    StructlogEventSink(stream=second).emit("e", mid=3, alpha=2, zeta=1)
    strip = lambda line: {k: v for k, v in json.loads(line).items() if k != "timestamp"}  # noqa: E731
    assert strip(first.getvalue()) == strip(second.getvalue())
    assert first.getvalue().index('"alpha"') < first.getvalue().index('"zeta"')


@pytest.mark.unit
def test_bind_returns_a_new_sink_and_leaves_the_original_clean() -> None:
    """Dos motores que compartan el sink no pueden contaminarse el contexto."""
    stream = io.StringIO()
    base = StructlogEventSink(stream=stream)
    research = base.bind(run_id="R1", symbol="EURUSD")
    execution = base.bind(run_id="R1", symbol="XAUUSD")

    assert base.context == {}
    assert research.context["symbol"] == "EURUSD"
    assert execution.context["symbol"] == "XAUUSD"
    assert research is not base and execution is not research


@pytest.mark.unit
def test_bound_sink_keeps_writing_to_the_injected_stream() -> None:
    """`bind` propaga el destino.

    Sin esto un sink enlazado escribiria a stdout aunque el original tuviera
    flujo inyectado, y el desvio solo se notaria buscando en los logs eventos
    que se emitieron a otro sitio.
    """
    stream = io.StringIO()
    StructlogEventSink(stream=stream).bind(run_id="R1").emit("bound.event")
    assert json.loads(stream.getvalue())["run_id"] == "R1"


@pytest.mark.unit
def test_explicit_field_overrides_the_bound_context() -> None:
    """Lo mas cercano a la llamada gana."""
    stream = io.StringIO()
    StructlogEventSink(stream=stream).bind(symbol="EURUSD").emit("e", symbol="XAUUSD")
    assert json.loads(stream.getvalue())["symbol"] == "XAUUSD"


@pytest.mark.unit
def test_null_sink_accepts_the_same_calls_and_writes_nothing() -> None:
    """El llamante nunca pregunta si hay sink.

    Un `if sink is not None` repartido por los motores es la via por la que la
    observabilidad acaba siendo opcional y por tanto ausente donde importa.
    """
    sink = NullEventSink().bind(run_id="R1")
    sink.emit("descartado", value=1)
    assert sink.context == {"run_id": "R1"}


# ---------------------------------------------------------------------------
# 2. Contexto de corrida
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_id_is_derived_not_random() -> None:
    """Dos corridas con los mismos insumos comparten identificador.

    Es lo que permite responder "esto ya se calculo" sin recalcularlo. Un
    `uuid4()` haria que dos ejecuciones identicas produjeran artefactos con
    nombres distintos.
    """
    args = {"config_hash": "abc", "code": "deadbeef", "seed": 42, "at_ns": TimestampNs(1_000)}
    assert derive_run_id(**args) == derive_run_id(**args)


@pytest.mark.unit
@pytest.mark.parametrize(
    "field,value",
    [("config_hash", "otra"), ("code", "cafe"), ("seed", 43), ("at_ns", TimestampNs(2_000))],
)
def test_any_change_in_the_inputs_changes_the_run_id(field: str, value: object) -> None:
    """El reciproco: un identificador que no cambia no identifica nada."""
    base = {"config_hash": "abc", "code": "deadbeef", "seed": 42, "at_ns": TimestampNs(1_000)}
    assert derive_run_id(**base) != derive_run_id(**{**base, field: value})  # type: ignore[arg-type]


@pytest.mark.unit
def test_code_version_reports_the_commit_and_flags_a_dirty_tree() -> None:
    """La version del codigo sale de git, no de un fichero que se olvida."""
    version = code_version(ROOT)
    assert version != UNKNOWN_CODE_VERSION, "este repositorio es un arbol git"
    assert version.replace(DIRTY_SUFFIX, "").isalnum()


@pytest.mark.unit
def test_code_version_admits_not_knowing_instead_of_inventing(tmp_path: Path) -> None:
    """Un despliegue sin repositorio arranca, pero no miente sobre su version."""
    assert code_version(tmp_path) == UNKNOWN_CODE_VERSION


@pytest.mark.unit
def test_context_refuses_to_seal_an_artifact_without_data_provenance() -> None:
    """`RunFingerprint` exige el hash del dataset.

    Falla al sellar y no al reproducir: un artefacto de investigacion sin
    procedencia de datos no es reconstruible, y descubrirlo meses despues es
    descubrirlo cuando ya no se puede arreglar.
    """
    from app.core.exceptions import ConfigError

    context = RunContext.create(
        root=ROOT, config_hash="abc", seed=1, at_ns=TimestampNs(0)
    )
    with pytest.raises(ConfigError):
        context.fingerprint()


@pytest.mark.unit
def test_with_dataset_keeps_the_run_id_and_returns_a_new_object() -> None:
    """El hash de datos se conoce despues; el identificador no puede cambiar."""
    context = RunContext.create(root=ROOT, config_hash="abc", seed=1, at_ns=TimestampNs(0))
    sealed = context.with_dataset("d1")

    assert sealed is not context
    assert sealed.run_id == context.run_id
    assert context.dataset_hash == ""
    assert sealed.fingerprint().dataset == "d1"


# ---------------------------------------------------------------------------
# 3. Metricas sobre trafico REAL del bus
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_runtime_metrics_counts_real_bus_traffic() -> None:
    """Suscriptor real, bus real, eventos reales. Es la evidencia de ADR-0008."""
    bus = _bus()
    metrics = metrics_for(bus)

    bus.publish(Event(name="bar.closed", payload={"i": 1}), source="feed", run_id="R1")
    bus.publish(Event(name="bar.closed", payload={"i": 2}), source="feed", run_id="R1")
    bus.publish(Event(name="config.resolved"), source="container", run_id="R1")

    assert metrics.total == 3
    assert metrics.by_event == {"bar.closed": 2, "config.resolved": 1}
    assert metrics.by_source == {"feed": 2, "container": 1}
    assert metrics.by_run == {"R1": 3}
    assert metrics.span_ns > 0, "la ventana temporal debe medirse, no asumirse"


@pytest.mark.unit
def test_metrics_are_registered_with_a_priority_between_recorder_and_default() -> None:
    """Contar despues de grabar, antes de que un oyente de negocio pueda fallar.

    La grabacion es la evidencia; las metricas son su resumen. Si el resumen se
    tomara antes, un fallo posterior dejaria un recuento que no corresponde a
    ninguna grabacion.
    """
    bus = _bus()
    Recorder().attach(bus)
    metrics_for(bus)

    order = [s.name for s in bus.subscribers_for("cualquier.evento")]
    assert order == ["recorder", SUBSCRIBER_NAME]
    assert Recorder.PRIORITY < SUBSCRIBER_PRIORITY < 100


@pytest.mark.unit
def test_attach_is_idempotent_so_a_reconnection_does_not_double_count() -> None:
    """En vivo una reconexion puede reintentar el enganche."""
    bus = _bus()
    metrics = RuntimeMetrics()
    metrics.attach(bus)
    metrics.attach(bus)

    bus.publish(Event(name="tick"), source="feed")
    assert metrics.total == 1, "el evento se conto dos veces"


@pytest.mark.unit
def test_lifecycle_hooks_run_in_order_and_leave_the_counters_readable() -> None:
    """Las seis fases existen y `stop` NO borra lo contado.

    El informe de una corrida se consulta despues de pararla; vaciar en `stop`
    haria imposible saber que ocurrio.
    """
    bus = _bus()
    metrics = RuntimeMetrics(_bus=bus)

    metrics.initialize()
    assert metrics.phase == "initialized"
    metrics.load()
    assert metrics.phase == "loaded"
    metrics.warmup()
    metrics.start()
    assert metrics.phase == "started"

    bus.publish(Event(name="tick"), source="feed")
    metrics.stop()

    assert metrics.phase == "stopped"
    assert metrics.total == 1, "los contadores deben sobrevivir a stop()"

    metrics.dispose()
    assert metrics.phase == "disposed"
    metrics.dispose()  # idempotente: no puede fallar


@pytest.mark.unit
def test_a_metrics_failure_would_never_stop_delivery() -> None:
    """Un evento sin sellar se cuenta igual y no rompe la corrida."""
    metrics = RuntimeMetrics()
    metrics(Event(name="huerfano"))
    assert metrics.by_event == {"huerfano": 1}
    assert metrics.by_source == {}


@pytest.mark.unit
def test_top_events_breaks_ties_alphabetically() -> None:
    """Dos corridas identicas deben producir el mismo informe.

    Un desempate arbitrario haria que el orden dependiera de la insercion en el
    diccionario, y con el el informe dejaria de ser comparable.
    """
    metrics = RuntimeMetrics(by_event={"zeta": 2, "alpha": 2, "mid": 5})
    assert metrics.top_events(3) == [("mid", 5), ("alpha", 2), ("zeta", 2)]


@pytest.mark.unit
def test_metrics_and_recorder_are_two_objects_on_purpose() -> None:
    """`Recorder` guarda la secuencia; `RuntimeMetrics` solo la resume.

    Se comprueba que ambos ven el mismo trafico y que solo uno crece con el.
    """
    bus = _bus()
    recorder = Recorder()
    recorder.attach(bus)
    metrics = metrics_for(bus)

    for i in range(5):
        bus.publish(Event(name="bar.closed", payload={"i": i}), source="feed")

    assert len(recorder) == 5
    assert metrics.total == 5
    assert metrics.by_event == {"bar.closed": 5}
    assert recorder.is_contiguous(), "un hueco invalida la grabacion como auditoria"


__all__: list[str] = []
