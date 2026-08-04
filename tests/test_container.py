"""Composicion, resolucion, sellado y ciclo de vida del contenedor.

Todos los tests usan EXCLUSIVAMENTE la superficie publica de inspeccion:
`components()`, `dependencies()`, `startup_order()`, `describe()` y `phase`.
Ninguno lee un atributo privado. Si algo hace falta para diagnosticar, se expone;
probar contra internos convierte cualquier refactor en una rotura falsa.

Todo ocurre en memoria: sin disco, sin red y sin reloj.
"""

from __future__ import annotations

from typing import Protocol

import pytest

from app.container.container import (
    ComponentDefinition,
    Container,
    DuplicateRegistration,
    IllegalPhaseTransition,
    LifecycleError,
    Phase,
    ResolutionCycle,
    Scope,
    ScopeNotImplemented,
    UnregisteredPort,
)

pytestmark = pytest.mark.unit


class PortA(Protocol): ...


class PortB(Protocol): ...


class PortC(Protocol): ...


class Recorder:
    """Componente que anota cada fase por la que pasa."""

    def __init__(self, name: str, log: list[str], *, fail_on: str | None = None) -> None:
        self.name = name
        self.log = log
        self.fail_on = fail_on

    def _record(self, phase: str) -> None:
        if self.fail_on == phase:
            raise RuntimeError(f"{self.name} falla en {phase}")
        self.log.append(f"{phase}:{self.name}")

    def initialize(self) -> None:
        self._record("initialize")

    def load(self) -> None:
        self._record("load")

    def warmup(self) -> None:
        self._record("warmup")

    def start(self) -> None:
        self._record("start")

    def stop(self) -> None:
        self._record("stop")

    def dispose(self) -> None:
        self._record("dispose")


def sealed(**ports: object) -> Container:
    """Contenedor con componentes triviales, ya sellado."""
    container = Container()
    for name, port in ports.items():
        container.register(port, lambda _c: object(), component_id=name)  # type: ignore[arg-type]
    container.seal()
    return container


# ---------------------------------------------------------------------------
# Fases
# ---------------------------------------------------------------------------


def test_starts_in_created_and_moves_to_registering() -> None:
    container = Container()
    assert container.phase is Phase.CREATED
    container.register(PortA, lambda _c: object(), component_id="A")
    assert container.phase is Phase.REGISTERING


def test_registration_after_sealing_is_a_phase_error() -> None:
    """Un componente que aparece tras sellar es un error estructural, no un
    aviso. Y tiene nombre: transicion de fase ilegal."""
    container = sealed(A=PortA)
    with pytest.raises(IllegalPhaseTransition):
        container.register(PortB, lambda _c: object(), component_id="B")


def test_resolution_before_sealing_is_refused() -> None:
    """Resolver con el registro abierto daria una instancia construida a partir
    de un grafo que todavia puede cambiar."""
    container = Container()
    container.register(PortA, lambda _c: object(), component_id="A")
    with pytest.raises(IllegalPhaseTransition):
        container.resolve(PortA)


def test_full_lifecycle_walks_the_state_machine() -> None:
    log: list[str] = []
    container = Container()
    container.register(PortA, lambda _c: Recorder("A", log), component_id="A")
    container.seal()
    assert container.phase is Phase.SEALED
    container.start()
    assert container.phase is Phase.STARTED
    container.shutdown()
    assert container.phase is Phase.DISPOSED


# ---------------------------------------------------------------------------
# Sellado
# ---------------------------------------------------------------------------


def test_sealing_detects_cycles_before_building_anything() -> None:
    container = Container()
    container.register(PortA, lambda _c: object(), depends_on=[PortB], component_id="A")
    container.register(PortB, lambda _c: object(), depends_on=[PortA], component_id="B")
    with pytest.raises(ResolutionCycle):
        container.seal()
    assert container.describe()["instantiated"] == 0


def test_sealing_detects_dependencies_without_adapter() -> None:
    container = Container()
    container.register(PortA, lambda _c: object(), depends_on=[PortB], component_id="A")
    with pytest.raises(UnregisteredPort):
        container.seal()


# ---------------------------------------------------------------------------
# Registro y resolucion
# ---------------------------------------------------------------------------


def test_duplicate_registration_is_fatal() -> None:
    """No gana el ultimo: cual ganase dependeria del orden de importacion."""
    container = Container()
    container.register(PortA, lambda _c: 1, component_id="primero")
    with pytest.raises(DuplicateRegistration):
        container.register(PortA, lambda _c: 2, component_id="segundo")


def test_unregistered_port_lists_what_is_available() -> None:
    container = sealed(A=PortA)
    with pytest.raises(UnregisteredPort) as caught:
        container.resolve(PortB)
    assert "A" in str(caught.value)


def test_singleton_returns_the_same_instance() -> None:
    container = sealed(A=PortA)
    assert container.resolve(PortA) is container.resolve(PortA)


def test_transient_returns_a_new_instance() -> None:
    container = Container()
    container.register(PortA, lambda _c: object(), scope=Scope.TRANSIENT, component_id="A")
    container.seal()
    assert container.resolve(PortA) is not container.resolve(PortA)


def test_undeclared_scope_fails_explicitly() -> None:
    """El vocabulario completo esta fijado; lo no implementado falla con nombre
    en lugar de descubrirse a mitad de camino."""
    with pytest.raises(ScopeNotImplemented):
        ComponentDefinition(
            component_id="A", port=PortA, factory=lambda _c: None, scope=Scope.SESSION
        )


def test_resolution_cycle_through_factories_is_detected() -> None:
    """El ciclo declarado lo caza `seal()`; este es el que solo aparece al
    ejecutar las factorias."""
    container = Container()
    container.register(PortA, lambda c: c.resolve(PortB), component_id="A")
    container.register(PortB, lambda c: c.resolve(PortA), component_id="B")
    container.seal()
    with pytest.raises(ResolutionCycle) as caught:
        container.resolve(PortA)
    assert "A" in str(caught.value) and "B" in str(caught.value)


# ---------------------------------------------------------------------------
# Orden
# ---------------------------------------------------------------------------


def test_startup_is_topological() -> None:
    container = Container()
    container.register(PortC, lambda _c: object(), component_id="C")
    container.register(PortB, lambda _c: object(), depends_on=[PortC], component_id="B")
    container.register(PortA, lambda _c: object(), depends_on=[PortB], component_id="A")
    container.seal()
    assert container.startup_order() == ("C", "B", "A")


def test_independent_components_break_ties_alphabetically() -> None:
    """Sin desempate, dos ejecuciones podrian inicializar en orden distinto y
    emitir secuencias de eventos diferentes con la misma configuracion."""
    container = Container()
    container.register(PortB, lambda _c: object(), component_id="zeta")
    container.register(PortA, lambda _c: object(), component_id="alfa")
    container.seal()
    assert container.startup_order() == ("alfa", "zeta")


def test_dependencies_are_exposed_by_identifier() -> None:
    container = Container()
    container.register(PortB, lambda _c: object(), component_id="base")
    container.register(PortA, lambda _c: object(), depends_on=[PortB], component_id="top")
    container.seal()
    assert container.dependencies() == {"base": (), "top": ("base",)}


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------


def test_each_phase_completes_for_everyone_before_the_next() -> None:
    """Si cada componente recorriera sus cuatro fases antes del siguiente, el
    primero estaria operando mientras el ultimo aun no ha cargado."""
    log: list[str] = []
    container = Container()
    container.register(PortA, lambda _c: Recorder("A", log), component_id="A")
    container.register(PortB, lambda _c: Recorder("B", log), depends_on=[PortA], component_id="B")
    container.seal()
    container.start()
    assert log[:4] == ["initialize:A", "initialize:B", "load:A", "load:B"]


def test_shutdown_runs_in_reverse_order() -> None:
    log: list[str] = []
    container = Container()
    container.register(PortA, lambda _c: Recorder("A", log), component_id="A")
    container.register(PortB, lambda _c: Recorder("B", log), depends_on=[PortA], component_id="B")
    container.seal()
    container.start()
    log.clear()
    container.shutdown()
    assert log == ["stop:B", "stop:A", "dispose:B", "dispose:A"]


def test_failed_start_cleans_up_and_reports_the_original_error() -> None:
    log: list[str] = []
    container = Container()
    container.register(PortA, lambda _c: Recorder("A", log), component_id="A")
    container.register(
        PortB,
        lambda _c: Recorder("B", log, fail_on="warmup"),
        depends_on=[PortA],
        component_id="B",
    )
    container.seal()
    with pytest.raises(LifecycleError) as caught:
        container.start()
    assert "warmup" in str(caught.value)
    assert "dispose:A" in log


def test_shutdown_closes_everything_even_if_one_component_fails() -> None:
    """Un cierre abortado a mitad deja recursos abiertos justo cuando el sistema
    intentaba soltarlos."""
    log: list[str] = []
    container = Container()
    container.register(PortC, lambda _c: Recorder("C", log), component_id="C")
    container.register(
        PortB,
        lambda _c: Recorder("B", log, fail_on="dispose"),
        depends_on=[PortC],
        component_id="B",
    )
    container.register(PortA, lambda _c: Recorder("A", log), depends_on=[PortB], component_id="A")
    container.seal()
    container.start()
    log.clear()
    reports = container.shutdown()

    disposed = next(r for r in reports if r.phase == "dispose")
    assert disposed.failed == "B"
    assert "dispose:A" in log and "dispose:C" in log


def test_components_without_hooks_are_skipped() -> None:
    """Una configuracion no tiene ciclo de vida y no debe estorbarlo."""
    container = Container()
    container.register_instance(PortA, {"seed": 7}, component_id="config")
    container.seal()
    container.start()
    assert container.phase is Phase.STARTED


# ---------------------------------------------------------------------------
# Diagnostico
# ---------------------------------------------------------------------------


def test_describe_reports_the_composed_graph() -> None:
    container = Container()
    container.register(PortA, lambda _c: object(), component_id="A")
    container.register(PortB, lambda _c: object(), depends_on=[PortA], component_id="B")
    container.seal()
    described = container.describe()
    assert described["registered"] == 2
    assert described["startup_order"] == ["A", "B"]
    assert described["cycle"] is None
    assert described["sealed"] is True


def test_describe_survives_a_cycle() -> None:
    """El diagnostico debe funcionar sobre un sistema roto: es cuando se usa."""
    container = Container()
    container.register(PortA, lambda _c: object(), depends_on=[PortB], component_id="A")
    container.register(PortB, lambda _c: object(), depends_on=[PortA], component_id="B")
    described = container.describe()
    assert described["cycle"] is not None
    assert described["startup_order"] == []


def test_definitions_never_expose_instances() -> None:
    """Inspeccionar el sistema no debe poder forzar su construccion."""
    container = sealed(A=PortA)
    definition = container.components()[0]
    assert set(definition.to_dict()) == {"id", "port", "scope", "depends_on"}
    assert container.describe()["instantiated"] == 0
