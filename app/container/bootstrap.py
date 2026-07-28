"""Raiz de composicion: arma la plataforma a partir de los contratos.

Es el unico lugar del sistema autorizado a conocer implementaciones concretas.
Todo lo demas recibe puertos.

Lo que compone hoy -fase 3- es la plataforma, no el trading: reloj,
configuracion resuelta, bus de eventos y catalogos de componentes. Ni broker, ni
datos, ni estrategias. Si `build_platform` funciona, `qp doctor` puede decir
`Platform READY` sobre una maquina sin MT5 y sin una sola vela.
"""

from __future__ import annotations

import time
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from app.config.providers.base import ConfigProvider
from app.config.providers.cli import CommandLineProvider, MappingProvider
from app.config.providers.env import EnvironmentProvider
from app.config.providers.toml import TomlFileProvider
from app.container.container import Container, Scope
from app.core.config.fingerprint import ConfigFingerprint, fingerprint
from app.core.config.provenance import Priority, ResolutionTrace
from app.core.config.resolver import ConfigLayer, resolve
from app.core.exceptions import ConfigValidationError
from app.core.registry.registry import Registry
from app.core.types import TimestampNs
from app.events.bus import DeliveryReport, ErrorPolicy, EventBus
from app.events.event import Event
from app.events.recorder import Recorder
from app.shared.ports import ClockPort

#: Modos donde un suscriptor roto debe romper la corrida en lugar de degradarla
#: en silencio y producir metricas incompletas que parecen completas.
_STRICT_EVENT_MODES: frozenset[str] = frozenset({"ci", "benchmark"})


# ---------------------------------------------------------------------------
# Puertos de plataforma
#
# Se declaran aqui y no en `shared/ports.py` porque son piezas de la plataforma,
# no fronteras del dominio: `shared` describe como el dominio habla con el
# mundo, y un bus de eventos interno no es el mundo.
# ---------------------------------------------------------------------------


@runtime_checkable
class EventBusPort(Protocol):
    """Publicacion de eventos, vista desde quien emite.

    La firma reproduce la de `EventBus.publish` en lugar de aplanar el contexto
    en `**context: str`. Un `**kwargs` haria que confundir `causation_id` con
    `correlation_id` -o escribir `corelation_id`- pasara el chequeo de tipos y
    apareciera como un evento huerfano meses despues, al auditar.
    """

    def publish(
        self,
        event: Event,
        *,
        source: str,
        correlation_id: str = ...,
        causation_id: str = ...,
        run_id: str = ...,
    ) -> DeliveryReport: ...


@runtime_checkable
class ConfigPort(Protocol):
    """Configuracion efectiva, vista desde quien la consume.

    Expone `describe()` ademas de `get()` porque `qp status` y `qp doctor`
    necesitan la huella y las claves no-por-defecto sin conocer `ResolvedConfig`.
    """

    def get(self, key: str, default: Any = None) -> Any: ...

    def describe(self) -> dict[str, Any]: ...


class SystemClock:
    """Reloj real. Unica lectura del reloj de pared en todo el sistema.

    Vive en la raiz de composicion y no en un paquete propio a proposito: son
    cinco lineas cuyo unico valor es ser sustituibles, y crear un paquete para
    ellas anadiria una arista al grafo sin crear ninguna frontera. Todo lo demas
    recibe `ClockPort` y en los tests recibe un reloj falso.
    """

    def now_ns(self) -> TimestampNs:
        return TimestampNs(time.time_ns())


class FrozenClock:
    """Reloj detenido. Para backtest, CI y cualquier corrida determinista."""

    def __init__(self, at_ns: int = 0) -> None:
        self._at = TimestampNs(at_ns)

    def now_ns(self) -> TimestampNs:
        return self._at


class ResolvedConfig:
    """Configuracion efectiva con su procedencia y su huella.

    Envuelve la traza en lugar de exponer un diccionario suelto para que
    cualquier consumidor pueda preguntar de donde salio un valor sin tener que
    conocer el resolvedor.
    """

    __slots__ = ("_fingerprint", "_trace", "_values")

    def __init__(self, trace: ResolutionTrace) -> None:
        self._trace = trace
        self._values = trace.flat()
        self._fingerprint = fingerprint(trace)

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def origin_of(self, key: str) -> str:
        return str(self._trace.get(key).origin)

    @property
    def fingerprint(self) -> ConfigFingerprint:
        return self._fingerprint

    @property
    def trace(self) -> ResolutionTrace:
        return self._trace

    def describe(self) -> dict[str, Any]:
        return {
            "keys": len(self._values),
            "fingerprint": str(self._fingerprint),
            "non_default": [v.key for v in self._trace.non_default()],
            "contested": [v.key for v in self._trace.contested()],
        }


# ---------------------------------------------------------------------------
# Composicion
# ---------------------------------------------------------------------------


def load_configuration(
    root: Path,
    *,
    mode: str,
    overrides: Mapping[str, Any] | None = None,
    cli_assignments: tuple[str, ...] = (),
) -> ResolvedConfig:
    """Resuelve la configuracion aplicando la precedencia declarada.

    El perfil se elige por el modo. Las capas ausentes no son un error: no tener
    `configs/local.toml` es lo normal, y su ausencia queda registrada en la traza
    para distinguirla de un fichero presente pero vacio.

    Raises:
        ConfigValidationError: si hay claves en conflicto irresoluble.
    """
    providers: list[ConfigProvider] = [
        TomlFileProvider(root / "configs" / "global.toml", Priority.GLOBAL_FILE),
        TomlFileProvider(
            root / "configs" / "profiles" / f"{mode}.toml", Priority.PROFILE_FILE
        ),
        TomlFileProvider(root / "configs" / "local.toml", Priority.LOCAL_FILE),
        EnvironmentProvider(),
        CommandLineProvider(cli_assignments),
    ]
    if overrides:
        providers.append(MappingProvider(overrides))

    layers: list[ConfigLayer] = []
    for provider in providers:
        layer = provider.load()
        if layer is not None:
            layers.append(layer)

    trace, report = resolve(layers)
    report.raise_if_failed(ConfigValidationError)
    return ResolvedConfig(trace)


def build_platform(
    root: Path,
    *,
    mode: str = "research",
    clock: ClockPort | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Container:
    """Compone la plataforma y devuelve el contenedor sellado.

    No arranca nada: devolver un contenedor sellado y no arrancado permite que
    `qp doctor` inspeccione el grafo sin provocar efectos. Arrancar es una
    decision del llamante.

    Args:
        clock: Reloj a usar. Si se omite, se elige por modo: detenido en los
            modos deterministas, real en los operativos. Elegirlo por modo y no
            por defecto fijo evita el error de ejecutar un backtest contra el
            reloj de pared.
    """
    container = Container()
    config = load_configuration(root, mode=mode, overrides=overrides)

    effective_clock = clock if clock is not None else _clock_for(mode, config)
    container.register_instance(ClockPort, effective_clock, component_id="clock")
    container.register_instance(ConfigPort, config, component_id="config")

    policy = (
        ErrorPolicy.RAISE if mode in _STRICT_EVENT_MODES else ErrorPolicy.COLLECT
    )
    container.register(
        EventBusPort,
        lambda c: _build_bus(c.resolve(ClockPort), policy),
        component_id="event_bus",
        depends_on=[ClockPort],
        scope=Scope.SINGLETON,
    )

    for kind in ("feature", "signal", "strategy"):
        container.register(
            _registry_port(kind),
            _registry_factory(kind),
            component_id=f"registry.{kind}",
        )

    container.seal()
    return container


def _clock_for(mode: str, config: ResolvedConfig) -> ClockPort:
    """Reloj apropiado para el modo, salvo que la configuracion lo fije."""
    pinned = config.get("runtime.frozen_clock_ns")
    if pinned is not None:
        return FrozenClock(int(pinned))
    if mode in ("backtest", "ci", "benchmark", "deterministic"):
        return FrozenClock(0)
    return SystemClock()


def _build_bus(clock: ClockPort, policy: ErrorPolicy) -> EventBus:
    """Bus con grabador enganchado.

    El grabador va siempre: sin el, una corrida no deja secuencia auditable, y
    esa secuencia es la mitad de lo que hace reconstruible un resultado.
    """
    bus = EventBus(clock.now_ns, policy=policy)
    Recorder().attach(bus)
    return bus


def _registry_factory(kind: str) -> Callable[[Container], Registry[Any]]:
    """Factoria de catalogo con `kind` capturado por cierre.

    Funcion aparte y no un `lambda` dentro del bucle. Un `lambda` capturaria la
    variable del bucle por referencia y los tres catalogos acabarian siendo el
    de `strategy`; el truco del argumento por defecto lo evita, pero a costa de
    una firma que no es la del puerto y de la supresion que hace falta para
    silenciarla. Mismo criterio que `_wrap` en `app.events.bus`.

    `Registry[Any]` y no un tipo concreto: la raiz de composicion crea los tres
    catalogos vacios antes de que exista un solo bloque, y features, signals y
    strategies tienen firmas distintas. El tipo se estrecha donde se consume,
    que es donde se sabe cual de los tres se esta pidiendo.
    """

    def build(_container: Container) -> Registry[Any]:
        return Registry(kind)

    return build


_REGISTRY_PORTS: dict[str, type] = {}


def _registry_port(kind: str) -> type:
    """Puerto nominal por familia de catalogo.

    Se generan tipos distintos porque el contenedor indexa por tipo: un unico
    `Registry` como puerto haria que features y signals compitieran por la misma
    clave y solo sobreviviera uno.
    """
    if kind not in _REGISTRY_PORTS:
        # `Protocol` es una forma especial y `type()` espera clases reales; el
        # cast lo declara sin cambiar nada en ejecucion. El puerto solo se usa
        # como CLAVE del contenedor: nunca se instancia ni se comprueba con
        # isinstance, asi que su unica propiedad relevante es ser unico.
        bases = cast("tuple[type, ...]", (Protocol,))
        _REGISTRY_PORTS[kind] = type(f"{kind.capitalize()}RegistryPort", bases, {})
    return _REGISTRY_PORTS[kind]


def platform_report(root: Path, *, mode: str = "research") -> dict[str, Any]:
    """Estado de la plataforma, calculado. Es lo que consume `qp status`.

    No lee ningun fichero de estado escrito a mano: compone el sistema y observa
    el resultado. Un `platform_state.toml` mantenido a mano mentiria en cuanto
    alguien olvidara actualizarlo en el commit correcto.
    """
    report: dict[str, Any] = {"mode": mode, "errors": []}
    try:
        container = build_platform(root, mode=mode)
    except Exception as exc:
        report["composed"] = False
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        return report

    config = container.resolve(ConfigPort)
    report["composed"] = True
    report["container"] = container.describe()
    report["config"] = config.describe()

    delivery = root / "configs" / "delivery.toml"
    if delivery.is_file():
        states = tomllib.loads(delivery.read_text(encoding="utf-8")).get("state", {})
        report["capabilities"] = {
            name: str(spec.get("status", "not_started"))
            for name, spec in sorted(states.items())
        }
    return report


__all__ = [
    "ConfigPort",
    "EventBusPort",
    "FrozenClock",
    "ResolvedConfig",
    "SystemClock",
    "build_platform",
    "load_configuration",
    "platform_report",
]
