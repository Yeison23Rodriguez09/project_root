"""Raiz de composicion: arma la plataforma a partir de los contratos.

Es el unico lugar del sistema autorizado a conocer implementaciones concretas.
Todo lo demas recibe puertos.

Lo que compone es la plataforma, no el trading: reloj, configuracion resuelta,
bus de eventos, catalogos de componentes y el catalogo de instrumentos. Ni
broker, ni datos, ni estrategias. Si `build_platform` funciona, `qp doctor` puede
decir `Platform READY` sobre una maquina sin MT5 y sin una sola vela.

`load_strategy_spec` se reexporta aqui sin envolverlo. No es composicion, y se
dice en lugar de disimularlo: es el unico camino por el que `interfaces` puede
leer una estrategia declarada. La matriz deja a la interfaz ver `core`,
`application` y `container`, y el lector vive en `config` -que es, por ADR-0005,
el unico paquete autorizado a abrir un fichero de configuracion-. Este modulo ya
es esa frontera para la configuracion via `load_configuration`, y la estrategia
entra por la misma puerta en lugar de abrir una segunda.
"""

from __future__ import annotations

import time
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from app.config.instruments import TomlInstrumentCatalog
from app.config.providers.base import ConfigProvider
from app.config.providers.cli import CommandLineProvider, MappingProvider
from app.config.providers.env import EnvironmentProvider
from app.config.providers.toml import TomlFileProvider
from app.config.strategies import load_strategy_spec
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
from app.portfolio.limits import RiskLimits
from app.portfolio.policy import limits_from_mapping
from app.shared.ports import ClockPort, InstrumentCatalogPort

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
        # Contratos de dominio, al mismo nivel que `global.toml` y bajo su propio
        # espacio de nombres. Hasta ADR-0015 NADIE los leia: los valores de
        # riesgo vivian a la vez en el fichero y en los defectos de `RiskLimits`,
        # y el capital inicial en `backtest.toml` y en el comando. Dos fuentes
        # para el mismo dato es P5 roto, y la que mandaba era la que menos se
        # revisa -el codigo-, mientras el fichero documentado quedaba de adorno.
        TomlFileProvider(root / "configs" / "risk.toml", Priority.GLOBAL_FILE, prefix="risk"),
        TomlFileProvider(
            root / "configs" / "backtest.toml", Priority.GLOBAL_FILE, prefix="backtest"
        ),
        TomlFileProvider(root / "configs" / "profiles" / f"{mode}.toml", Priority.PROFILE_FILE),
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

    policy = ErrorPolicy.RAISE if mode in _STRICT_EVENT_MODES else ErrorPolicy.COLLECT
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

    # Primer adaptador de dominio que la plataforma inyecta de verdad. Entra aqui
    # y no en el caso de uso porque su implementacion vive en `config`, que
    # `application` no puede importar: un caso de uso capaz de abrir un fichero
    # de configuracion introduciria una entrada no declarada y el resultado
    # dejaria de depender solo de (datos, configuracion, semilla).
    #
    # Perezoso -`register` y no `register_instance`- para que `qp doctor` siga
    # componiendo la plataforma en una maquina cuyo `configs/symbols/` este vacio
    # o mal formado: el fallo aparece al pedir un instrumento, que es cuando de
    # verdad hace falta, y no al arrancar cualquier comando.
    container.register(
        InstrumentCatalogPort,
        lambda _c: TomlInstrumentCatalog(root / "configs" / "symbols"),
        component_id="instruments",
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


@dataclass(frozen=True, slots=True)
class PlatformServices:
    """Lo que la plataforma entrega a un caso de uso, ya resuelto.

    Existe por una restriccion de la matriz que resulta ser la correcta:
    `interfaces` solo ve `core`, `application` y `container`, de modo que la CLI
    NO puede importar `app.shared` y por tanto no puede nombrar un puerto para
    pedirselo al contenedor. Sin este objeto, la unica salida seria declarar
    `shared` como dependencia de la interfaz, y entonces cualquier comando podria
    resolver cualquier puerto y saltarse los casos de uso.

    La consecuencia practica es la deseable: el comando recibe piezas ya
    resueltas y no sabe de que tipo son ni quien las implementa.

    Es tambien la via por la que el reloj de una corrida pasa a salir del
    CONTENEDOR. `qp download` construye hoy un `SystemClock()` por su cuenta, que
    funciona pero deja fuera la eleccion de reloj por modo -y con ella la
    reproducibilidad que el modo determinista promete-.
    """

    clock: ClockPort
    instruments: InstrumentCatalogPort
    config_hash: str
    mode: str
    risk_limits: RiskLimits
    initial_equity: float


def platform_services(root: Path, *, mode: str = "deterministic") -> PlatformServices:
    """Compone la plataforma y devuelve lo que un caso de uso necesita de ella.

    El defecto es `deterministic` y no `research` a proposito: quien pide
    servicios de plataforma para EJECUTAR algo -un backtest, una busqueda- debe
    obtener un reloj detenido, porque el instante entra en la identidad de la
    corrida. Un caso de uso que quiera el reloj de pared tiene que pedirlo.
    """
    container = build_platform(root, mode=mode)
    config = container.resolve(ConfigPort)
    return PlatformServices(
        clock=container.resolve(ClockPort),
        instruments=container.resolve(InstrumentCatalogPort),
        config_hash=str(config.describe()["fingerprint"]),
        mode=mode,
        risk_limits=_risk_limits(config),
        initial_equity=float(config.get("backtest.account.initial_equity", 0.0)),
    )


def _risk_limits(config: ConfigPort) -> RiskLimits:
    """Convierte la configuracion resuelta en el objeto de valor tipado.

    La conversion vive en la raiz de composicion y no en el caso de uso porque es
    justo la frontera que ADR-0005 describe: infraestructura lee, valida y
    coacciona; hacia dentro cruza un objeto propio, nunca un diccionario. Un
    runner que preguntara `config.get("risk.sizing.risk_fraction")` conoceria la
    forma del fichero, y cambiar la disposicion del TOML obligaria a tocarlo.

    Se exige que las claves esten: un `risk.toml` ausente o incompleto NO cae a
    los defectos del tipo. Caer en silencio a un `risk_fraction` por defecto es
    la forma en que una politica de riesgo deja de aplicarse sin que nadie borre
    una linea.
    """
    raw = {
        "sizing": {"risk_fraction": config.get("risk.sizing.risk_fraction")},
        "limits": {
            name: config.get(f"risk.limits.{name}")
            for name in (
                "min_stop_cost_multiple",
                "absolute_min_stop_points",
                "max_lots_per_order",
                "max_lots_per_symbol",
                "max_open_positions",
                "max_margin_utilization",
            )
        },
    }
    missing = sorted(
        key
        for section, values in raw.items()
        for key, value in values.items()
        if value is None
        for key in (f"risk.{section}.{key}",)
    )
    if missing:
        raise ConfigValidationError(
            "La politica de riesgo esta incompleta en configs/risk.toml",
            missing=missing,
        )
    return limits_from_mapping(raw)


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
            name: str(spec.get("status", "not_started")) for name, spec in sorted(states.items())
        }
    return report


__all__ = [
    "ConfigPort",
    "EventBusPort",
    "FrozenClock",
    "PlatformServices",
    "ResolvedConfig",
    "SystemClock",
    "build_platform",
    "load_configuration",
    "load_strategy_spec",
    "platform_report",
    "platform_services",
]
