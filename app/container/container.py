"""Contenedor de composicion. Resuelve puertos y ordena el ciclo de vida.

Composition Root, no Service Locator. Se usa en un unico punto -el arranque- para
armar el grafo completo, y despues se entrega ya construido. Ningun componente
recibe el contenedor: si lo recibiera, volveria a conocerlo y sus dependencias
dejarian de ser visibles en su firma.

Cuatro decisiones estructurales:

* **Definicion separada de instancia.** `ComponentDefinition` describe como
  construir; la instancia es otra cosa. Doctor, el grafo, los plugins y las
  metricas solo necesitan definiciones, y no deben poder forzar la construccion
  de un objeto para inspeccionarlo.
* **Dependencias declaradas, no introspeccionadas.** El grafo existe antes de
  crear un solo objeto. Eso permite validar ciclos, exportar el DAG y generar
  documentacion sin instanciar nada, y no depende de reflexion ni de anotaciones.
* **Registro sellado.** Tras `seal()` nadie registra nada. Un componente que
  aparece despues es un error estructural, no un aviso.
* **Estado explicito.** No hay booleanos: hay una maquina de estados con
  transiciones validadas. "Registrar tarde" deja de ser un despiste y pasa a ser
  una transicion ilegal con nombre.

Los fallos viven en `app.container.exceptions` y se reexportan aqui: una
jerarquia de excepciones crece con cada frontera nueva, y este modulo describe
un mecanismo que no debe crecer con ella.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar, cast

from app.container.exceptions import (
    ContainerError,
    DuplicateRegistration,
    IllegalPhaseTransition,
    LifecycleError,
    ResolutionCycle,
    ScopeNotImplemented,
    UnregisteredPort,
)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Vocabulario
# ---------------------------------------------------------------------------


class Scope(StrEnum):
    """Cuantas instancias existen de un componente y cuanto viven.

    `SESSION` y `PROCESS` se declaran sin implementar. Fijar el vocabulario
    completo ahora evita que, cuando haga falta el tercero, se resuelva con un
    booleano añadido de urgencia.
    """

    SINGLETON = "singleton"
    TRANSIENT = "transient"
    SESSION = "session"
    PROCESS = "process"


IMPLEMENTED_SCOPES: frozenset[Scope] = frozenset({Scope.SINGLETON, Scope.TRANSIENT})


class Phase(StrEnum):
    """Estado del contenedor. Las transiciones estan declaradas y se validan."""

    CREATED = "created"
    REGISTERING = "registering"
    SEALED = "sealed"
    INITIALIZED = "initialized"
    LOADED = "loaded"
    WARMED = "warmed"
    STARTED = "started"
    STOPPED = "stopped"
    DISPOSED = "disposed"
    FAILED = "failed"


#: Transiciones legales. `FAILED` es alcanzable desde cualquier fase y por eso
#: no aparece aqui: se trata aparte.
_TRANSITIONS: dict[Phase, frozenset[Phase]] = {
    Phase.CREATED: frozenset({Phase.REGISTERING, Phase.SEALED}),
    Phase.REGISTERING: frozenset({Phase.REGISTERING, Phase.SEALED}),
    Phase.SEALED: frozenset({Phase.INITIALIZED, Phase.DISPOSED}),
    Phase.INITIALIZED: frozenset({Phase.LOADED, Phase.STOPPED}),
    Phase.LOADED: frozenset({Phase.WARMED, Phase.STOPPED}),
    Phase.WARMED: frozenset({Phase.STARTED, Phase.STOPPED}),
    Phase.STARTED: frozenset({Phase.STOPPED}),
    Phase.STOPPED: frozenset({Phase.DISPOSED}),
    Phase.DISPOSED: frozenset(),
    Phase.FAILED: frozenset({Phase.DISPOSED}),
}

#: Fase del ciclo de vida que produce cada estado del contenedor.
_STARTUP_STEPS: tuple[tuple[str, Phase], ...] = (
    ("initialize", Phase.INITIALIZED),
    ("load", Phase.LOADED),
    ("warmup", Phase.WARMED),
    ("start", Phase.STARTED),
)

_SHUTDOWN_STEPS: tuple[tuple[str, Phase], ...] = (
    ("stop", Phase.STOPPED),
    ("dispose", Phase.DISPOSED),
)


# ---------------------------------------------------------------------------
# Definicion
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComponentDefinition:
    """Como construir un componente. NO contiene la instancia.

    La separacion es deliberada: doctor, el exportador del grafo, el registro de
    plugins y las metricas trabajan solo con definiciones. Si la definicion
    llevara la instancia dentro, inspeccionar el sistema forzaria a construirlo.
    """

    component_id: str
    port: type
    factory: Callable[[Container], Any]
    scope: Scope = Scope.SINGLETON
    depends_on: tuple[type, ...] = ()

    def __post_init__(self) -> None:
        if not self.component_id.strip():
            raise ContainerError("Una definicion necesita identificador")
        if self.scope not in IMPLEMENTED_SCOPES:
            raise ScopeNotImplemented(
                f"El scope {self.scope} esta declarado pero no implementado",
                component=self.component_id,
                scope=str(self.scope),
                implemented=sorted(str(s) for s in IMPLEMENTED_SCOPES),
            )

    @property
    def port_name(self) -> str:
        return getattr(self.port, "__name__", str(self.port))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.component_id,
            "port": self.port_name,
            "scope": str(self.scope),
            "depends_on": [getattr(d, "__name__", str(d)) for d in self.depends_on],
        }


@dataclass(slots=True)
class PhaseReport:
    """Que se ejecuto y en que orden durante una fase."""

    phase: str
    order: list[str] = field(default_factory=list)
    failed: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "order": list(self.order),
            "failed": self.failed,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Contenedor
# ---------------------------------------------------------------------------


class Container:
    """Registro de puertos, raiz de composicion y maquina de ciclo de vida."""

    __slots__ = ("_definitions", "_instances", "_phase", "_resolving")

    def __init__(self) -> None:
        self._definitions: dict[type, ComponentDefinition] = {}
        self._instances: dict[type, Any] = {}
        self._resolving: list[type] = []
        self._phase: Phase = Phase.CREATED

    # -- fase ---------------------------------------------------------------

    @property
    def phase(self) -> Phase:
        return self._phase

    def _transition(self, target: Phase) -> None:
        if target is not Phase.FAILED and target not in _TRANSITIONS[self._phase]:
            raise IllegalPhaseTransition(
                "Transicion de fase no permitida",
                current=str(self._phase),
                target=str(target),
                allowed=sorted(str(p) for p in _TRANSITIONS[self._phase]),
            )
        self._phase = target

    def _require_phase(self, *allowed: Phase, action: str) -> None:
        if self._phase not in allowed:
            raise IllegalPhaseTransition(
                f"No se puede {action} en la fase actual",
                current=str(self._phase),
                allowed=sorted(str(p) for p in allowed),
            )

    # -- registro -----------------------------------------------------------

    def register(
        self,
        port: type[T],
        factory: Callable[[Container], T],
        *,
        component_id: str,
        scope: Scope = Scope.SINGLETON,
        depends_on: Sequence[type] = (),
    ) -> None:
        """Asocia un puerto con la factoria que lo construye.

        Raises:
            IllegalPhaseTransition: si el registro ya esta sellado.
            DuplicateRegistration: si el puerto ya tiene adaptador.
        """
        self._require_phase(Phase.CREATED, Phase.REGISTERING, action="registrar")
        if port in self._definitions:
            raise DuplicateRegistration(
                "Dos adaptadores reclaman el mismo puerto",
                port=getattr(port, "__name__", str(port)),
                existing=self._definitions[port].component_id,
                incoming=component_id,
            )
        self._definitions[port] = ComponentDefinition(
            component_id=component_id,
            port=port,
            factory=cast("Callable[[Container], Any]", factory),
            scope=scope,
            depends_on=tuple(depends_on),
        )
        self._transition(Phase.REGISTERING)

    def register_instance(self, port: type[T], instance: T, *, component_id: str) -> None:
        """Registra un objeto ya construido.

        Via para inyectar dobles en tests y para valores que no requieren
        construccion, como una configuracion ya resuelta.
        """
        self.register(port, lambda _c: instance, component_id=component_id)
        self._instances[port] = instance

    def seal(self) -> None:
        """Cierra el registro. Valida el grafo antes de permitir resolver.

        Sellar comprueba el DAG completo sin construir nada. Si hay un ciclo o
        una dependencia sin registrar, se sabe aqui -antes de que exista un solo
        objeto- y no a mitad del arranque con medio sistema vivo.
        """
        self._require_phase(Phase.CREATED, Phase.REGISTERING, action="sellar")
        self.startup_order()  # levanta ResolutionCycle si el grafo no es un DAG
        missing = sorted(
            f"{d.component_id} -> {getattr(dep, '__name__', dep)}"
            for d in self._definitions.values()
            for dep in d.depends_on
            if dep not in self._definitions
        )
        if missing:
            raise UnregisteredPort(
                "Dependencias declaradas sin adaptador registrado", missing=missing
            )
        self._transition(Phase.SEALED)

    @property
    def is_sealed(self) -> bool:
        return self._phase not in (Phase.CREATED, Phase.REGISTERING)

    # -- resolucion ---------------------------------------------------------

    def resolve(self, port: type[T]) -> T:
        """Devuelve la implementacion de un puerto, construyendola si hace falta.

        Solo despues de sellar. Resolver con el registro abierto daria una
        instancia construida a partir de un grafo que aun puede cambiar.
        """
        if not self.is_sealed:
            raise IllegalPhaseTransition(
                "No se puede resolver antes de sellar el registro",
                current=str(self._phase),
                hint="Llama a seal() cuando termines de registrar.",
            )

        definition = self._definitions.get(port)
        if definition is None:
            raise UnregisteredPort(
                "Puerto sin adaptador registrado",
                port=getattr(port, "__name__", str(port)),
                registered=sorted(d.component_id for d in self._definitions.values()),
            )

        if definition.scope is Scope.SINGLETON and port in self._instances:
            return cast("T", self._instances[port])

        if port in self._resolving:
            raise ResolutionCycle(
                "Ciclo al resolver dependencias",
                chain=[self._definitions[p].component_id for p in (*self._resolving, port)],
            )

        self._resolving.append(port)
        try:
            instance = definition.factory(self)
        finally:
            self._resolving.pop()

        if definition.scope is Scope.SINGLETON:
            self._instances[port] = instance
        return cast("T", instance)

    # -- inspeccion publica -------------------------------------------------

    def components(self) -> tuple[ComponentDefinition, ...]:
        """Definiciones registradas, en orden alfabetico estable.

        Superficie publica de inspeccion. Ningun test ni herramienta debe leer
        atributos privados: si algo hace falta para diagnosticar, se expone aqui.
        """
        return tuple(sorted(self._definitions.values(), key=lambda d: d.component_id))

    def dependencies(self) -> Mapping[str, tuple[str, ...]]:
        """Grafo de dependencias por identificador de componente."""
        return {
            d.component_id: tuple(
                self._definitions[dep].component_id
                for dep in d.depends_on
                if dep in self._definitions
            )
            for d in self.components()
        }

    def definition_for(self, port: type) -> ComponentDefinition:
        definition = self._definitions.get(port)
        if definition is None:
            raise UnregisteredPort(
                "Puerto sin adaptador registrado",
                port=getattr(port, "__name__", str(port)),
            )
        return definition

    def __contains__(self, port: object) -> bool:
        return port in self._definitions

    def __len__(self) -> int:
        return len(self._definitions)

    def __iter__(self) -> Iterator[ComponentDefinition]:
        return iter(self.components())

    # -- orden --------------------------------------------------------------

    def startup_order(self) -> tuple[str, ...]:
        """Identificadores en orden topologico, con desempate alfabetico.

        Nada arranca antes de aquello de lo que depende. El desempate entre
        componentes independientes no es cosmetico: sin el, dos ejecuciones
        podrian inicializar en orden distinto y emitir secuencias de eventos
        diferentes con la misma configuracion, rompiendo P1.
        """
        return tuple(self._definitions[p].component_id for p in self._startup_ports())

    def _startup_ports(self) -> list[type]:
        pending = {
            port: set(d.depends_on) & set(self._definitions)
            for port, d in self._definitions.items()
        }
        ordered: list[type] = []
        while pending:
            ready = sorted(
                (p for p, deps in pending.items() if not deps),
                key=lambda p: self._definitions[p].component_id,
            )
            if not ready:
                raise ResolutionCycle(
                    "Ciclo en las dependencias declaradas",
                    involved=sorted(self._definitions[p].component_id for p in pending),
                )
            ordered.extend(ready)
            for port in ready:
                del pending[port]
            for deps in pending.values():
                deps.difference_update(ready)
        return ordered

    # -- ciclo de vida ------------------------------------------------------

    def start(self) -> list[PhaseReport]:
        """Construye y arranca todo, fase a fase.

        Cada fase se completa para TODOS los componentes antes de pasar a la
        siguiente. Si cada uno recorriera sus cuatro fases antes del siguiente,
        el primero estaria operando -fase `start`- mientras el ultimo aun no ha
        cargado, y recibiria eventos de un sistema a medio construir.

        Raises:
            LifecycleError: ante el primer fallo. Lo ya arrancado se para antes
                de propagar, para no dejar recursos abiertos.
        """
        if not self.is_sealed:
            self.seal()
        self._require_phase(Phase.SEALED, action="arrancar")

        ports = self._startup_ports()
        reports: list[PhaseReport] = []

        for hook_name, reached in _STARTUP_STEPS:
            report = PhaseReport(phase=hook_name)
            for port in ports:
                definition = self._definitions[port]
                # `port` es `type` sin parametrizar -viene del grafo, no de una
                # llamada tipada-, asi que `resolve` no puede inferir nada mejor.
                component: Any = self.resolve(port)
                hook = getattr(component, hook_name, None)
                if not callable(hook):
                    continue
                try:
                    hook()
                except Exception as exc:
                    report.failed = definition.component_id
                    report.error = f"{type(exc).__name__}: {exc}"
                    reports.append(report)
                    self._phase = Phase.FAILED
                    self.shutdown(quiet=True)
                    raise LifecycleError(
                        f"Fallo en la fase {hook_name!r}",
                        component=definition.component_id,
                        phase=hook_name,
                        cause=report.error,
                    ) from exc
                report.order.append(definition.component_id)
            reports.append(report)
            self._transition(reached)

        return reports

    def shutdown(self, *, quiet: bool = False) -> list[PhaseReport]:
        """Para y libera en orden inverso al de arranque.

        Shutdown intenta cerrarlo todo. Nunca aborta porque un componente falle: el
        informe recoge que se cerro y que no, y el resto sigue cerrandose. Un
        cierre abortado a mitad deja recursos abiertos justo cuando el sistema
        intentaba soltarlos.

        Args:
            quiet: No propaga tampoco los fallos de `stop`. Lo usa `start()` al
                deshacer un arranque fallido: el error importante es el original.
        """
        if self._phase is Phase.DISPOSED:
            return []

        ports = list(reversed(self._startup_ports()))
        reports: list[PhaseReport] = []
        first_failure: LifecycleError | None = None

        for hook_name, reached in _SHUTDOWN_STEPS:
            report = PhaseReport(phase=hook_name)
            for port in ports:
                if port not in self._instances:
                    continue
                definition = self._definitions[port]
                hook = getattr(self._instances[port], hook_name, None)
                if not callable(hook):
                    continue
                try:
                    hook()
                    report.order.append(definition.component_id)
                except Exception as exc:
                    report.failed = definition.component_id
                    report.error = f"{type(exc).__name__}: {exc}"
                    if hook_name == "stop" and not quiet and first_failure is None:
                        first_failure = LifecycleError(
                            "Fallo al parar un componente",
                            component=definition.component_id,
                            cause=report.error,
                        )
            reports.append(report)
            self._phase = reached

        self._instances.clear()
        if first_failure is not None:
            raise first_failure
        return reports

    # -- diagnostico --------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """Volcado del grafo compuesto, para `qp doctor` y para artefactos.

        Funciona sobre un sistema roto: ante un ciclo devuelve el ciclo en lugar
        de lanzar. Un diagnostico que solo funciona cuando todo esta bien no
        sirve para nada, porque es cuando algo falla cuando se ejecuta.
        """
        try:
            order = list(self.startup_order())
            cycle: str | None = None
        except ResolutionCycle as exc:
            order = []
            cycle = str(exc)
        return {
            "phase": str(self._phase),
            "sealed": self.is_sealed,
            "registered": len(self._definitions),
            "instantiated": len(self._instances),
            "startup_order": order,
            "cycle": cycle,
            "components": [d.to_dict() for d in self.components()],
        }

    def __repr__(self) -> str:
        return f"Container({len(self._definitions)} puertos, {self._phase})"


__all__ = [
    "IMPLEMENTED_SCOPES",
    "ComponentDefinition",
    "Container",
    "ContainerError",
    "DuplicateRegistration",
    "IllegalPhaseTransition",
    "LifecycleError",
    "Phase",
    "PhaseReport",
    "ResolutionCycle",
    "Scope",
    "ScopeNotImplemented",
    "UnregisteredPort",
]
