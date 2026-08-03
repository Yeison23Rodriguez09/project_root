"""Puertos: los contratos entre el dominio y el mundo.

Regla de dependencia (arquitectura hexagonal): el dominio y la aplicacion
dependen de estos `Protocol`; la infraestructura los implementa. Nunca al
reves. Un `import` desde `app/domain` hacia `app/infrastructure` es un fallo de
arquitectura y debe rechazarse en revision.

Se usan `Protocol` estructurales y no clases base abstractas porque:

* permiten que un adaptador de terceros cumpla el contrato sin heredar de
  nuestro codigo;
* permiten sustituir cualquier puerto por un doble de test sin registro previo;
* mypy verifica el cumplimiento en tiempo de analisis, sin coste en ejecucion.

Consecuencia practica: backtest, paper y live comparten el mismo caso de uso.
Lo unico que cambia es que `BrokerPort` lo implemente un simulador o MT5.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from app.core.types import (
    FloatArray,
    RunId,
    StrategyId,
    Symbol,
    Timeframe,
    TimestampNs,
)
from app.core.validation import ValidationReport
from app.domain.entities.bars import Bars
from app.domain.entities.order import Order, OrderIntent
from app.domain.entities.trade import Position, Trade
from app.domain.value_objects.dataset import WriteResult
from app.domain.value_objects.instrument import Instrument
from app.domain.value_objects.metrics import PerformanceMetrics
from app.domain.value_objects.signal import SignalOutput
from app.domain.value_objects.strategy_spec import StrategySpec
from app.domain.value_objects.validation_metrics import StatisticalTestResult

# ---------------------------------------------------------------------------
# Tiempo
# ---------------------------------------------------------------------------


@runtime_checkable
class ClockPort(Protocol):
    """Fuente de tiempo inyectable.

    Existe para que ninguna funcion llame a `datetime.now()` directamente. Sin
    este puerto un backtest no es reproducible: cualquier regla que dependa de
    "ahora" produce un resultado distinto en cada ejecucion, y las pruebas de
    logica horaria requieren esperar al reloj real.
    """

    def now_ns(self) -> TimestampNs:
        """Instante actual en nanosegundos UTC."""
        ...


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------


@runtime_checkable
class MarketDataPort(Protocol):
    """Acceso a historico ya normalizado.

    El adaptador es responsable de devolver `Bars` validado. Si no puede
    garantizar las invariantes, debe fallar en lugar de entregar datos dudosos:
    el coste de un backtest sobre datos corruptos es mayor que el de no tenerlo.
    """

    def load(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        *,
        start_ns: TimestampNs | None = None,
        end_ns: TimestampNs | None = None,
    ) -> Bars: ...

    def available_symbols(self) -> Sequence[Symbol]: ...


@runtime_checkable
class LiveFeedPort(Protocol):
    """Acceso a datos en tiempo real.

    Se separa de `MarketDataPort` porque la semantica es distinta: aqui la
    ultima barra puede estar **incompleta**. `latest_closed_bar_ns` es la
    frontera que impide que el motor evalue una barra en formacion, que es la
    forma mas comun de look-ahead accidental en produccion.
    """

    def latest_bars(self, symbol: Symbol, timeframe: Timeframe, count: int) -> Bars: ...

    def latest_closed_bar_ns(self, symbol: Symbol, timeframe: Timeframe) -> TimestampNs: ...

    def current_spread_points(self, symbol: Symbol) -> float: ...


@runtime_checkable
class MarketDataWriterPort(Protocol):
    """Persistencia de historicos ya validados. Serializa y nada mas.

    Contrapartida de `MarketDataPort`: aquel lee, este escribe. La simetria es
    intencionada -el mismo `Bars` entra y sale sin cambiar de forma- y es lo que
    permite que un historico descargado hoy se relea manana con las mismas
    garantias.

    Recibe `Bars` y ningun dato adicional porque no lo necesita: el simbolo y el
    marco temporal son campos obligatorios de la serie, asi que el escritor no
    puede depositarla bajo una identidad equivocada. El dato dice a que
    instrumento pertenece.

    Lo que este puerto NO hace, y la lista es el contrato (ADR-0011):

    * no decide la ubicacion, el nombre ni la organizacion del repositorio: eso
      lo resuelve una estrategia de disposicion que el adaptador recibe, de modo
      que lector y escritor comparten convencion y no pueden divergir;
    * no valida -si le llega un `Bars` es porque el tipo ya lo garantizo-;
    * no normaliza, no deduplica y no descarga incrementalmente;
    * no conoce proveedor, version, reloj ni catalogo de datasets.

    Todo eso pertenece al servicio de almacenamiento. Un adaptador que decidiera
    cualquiera de esas cosas tendria reglas de negocio dentro, y entonces
    cambiar la politica obligaria a tocar la infraestructura.
    """

    def write(self, bars: Bars, *, overwrite: bool = False) -> WriteResult:
        """Persiste la serie y devuelve los hechos tecnicos de la escritura.

        `overwrite` va aqui y no en el llamante porque solo el adaptador sabe si
        el destino ya existe. Por defecto es `False`: sobrescribir un historico
        debe pedirse, nunca ocurrir por descuido.

        Raises:
            StorageError: el destino existe y `overwrite` es `False`, o el medio
                no admitio la escritura.
        """
        ...

    def exists(self, symbol: Symbol, timeframe: Timeframe) -> bool: ...


@runtime_checkable
class InstrumentCatalogPort(Protocol):
    """Especificaciones de contrato por instrumento."""

    def get(self, symbol: Symbol) -> Instrument: ...


# ---------------------------------------------------------------------------
# Calculo
# ---------------------------------------------------------------------------


@runtime_checkable
class FeatureFn(Protocol):
    """Funcion de feature: barras y parametros a una serie alineada.

    Contrato obligatorio, verificado por `tests/test_no_lookahead.py`:

    1. La salida tiene exactamente `len(bars)` elementos.
    2. `salida[i]` depende unicamente de `bars[0..i]`.
    3. Las primeras `warmup` posiciones son `NaN`, nunca ceros ni valores
       rellenados hacia atras. Un cero es un valor legitimo y confundirlo con
       "aun no calculable" genera senales fantasma al inicio de cada fold.
    4. Es pura: sin estado, sin I/O, sin aleatoriedad.
    """

    def __call__(self, bars: Bars, **params: Any) -> FloatArray: ...


@runtime_checkable
class SignalBlockFn(Protocol):
    """Bloque de senal: features y parametros a decisiones por barra.

    Recibe el contenedor de features ya calculado (no `Bars` crudo) para forzar
    que todo indicador pase por el registro de features y quede cacheado y
    trazable.
    """

    def __call__(self, frame: Any, **params: Any) -> SignalOutput: ...


# ---------------------------------------------------------------------------
# Riesgo y ejecucion
# ---------------------------------------------------------------------------


@runtime_checkable
class RiskPolicyPort(Protocol):
    """Traduce una decision direccional en una intencion de orden dimensionada.

    Es el unico lugar autorizado para decidir tamano. Ni la estrategia ni el
    motor de ejecucion pueden calcular lotes: si pudieran, seria imposible
    auditar por que se arriesgo lo que se arriesgo.

    Devuelve `None` cuando la operacion no debe realizarse; el motivo viaja en
    el `ValidationReport` para que el rechazo sea explicable.
    """

    def size_order(
        self,
        *,
        intent_direction: int,
        instrument: Instrument,
        equity: float,
        stop_distance: float,
        open_positions: Sequence[Position],
        at_ns: TimestampNs,
    ) -> tuple[OrderIntent | None, ValidationReport]: ...


@runtime_checkable
class BrokerPort(Protocol):
    """Adaptador de broker. Unico punto de contacto con dinero real.

    Toda implementacion debe ser idempotente frente a reintentos: en una
    reconexion el motor puede reenviar una orden ya aceptada, y duplicarla es
    inaceptable. El adaptador resuelve la idempotencia mediante `client_id`.
    """

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    def account_equity(self) -> float: ...

    def account_balance(self) -> float: ...

    def open_positions(self, symbol: Symbol | None = None) -> Sequence[Position]: ...

    def submit(self, intent: OrderIntent, *, client_id: str) -> Order: ...

    def cancel(self, order_id: str) -> Order: ...

    def close_position(self, position: Position, *, client_id: str) -> Order: ...


@runtime_checkable
class ExecutionSimulatorPort(Protocol):
    """Modelo de llenado usado por backtest y paper.

    Se declara como puerto para poder sustituir el modelo optimista por uno
    pesimista y comparar. La diferencia entre ambos es una medida directa de la
    fragilidad de la estrategia frente a la calidad de ejecucion.
    """

    @property
    def name(self) -> str:
        """Identidad del modelo: sin ella, dos corridas iguales bajo supuestos
        de ejecucion distintos dan numeros distintos y el artefacto no explica
        por que (ADR-0013)."""
        ...

    def fill_price(
        self,
        *,
        intent: OrderIntent,
        instrument: Instrument,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        atr: float,
    ) -> float: ...


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------


@runtime_checkable
class ArtifactStorePort(Protocol):
    """Escritura de artefactos con metadatos obligatorios.

    El puerto exige `metadata` de forma explicita porque un artefacto sin
    origen, semilla y version no es auditable y por tanto no vale nada seis
    meses despues.
    """

    def write_json(self, relative_path: str, payload: Mapping[str, Any]) -> str: ...

    def write_table(self, relative_path: str, rows: Sequence[Mapping[str, Any]]) -> str: ...

    def read_json(self, relative_path: str) -> Mapping[str, Any]: ...

    def exists(self, relative_path: str) -> bool: ...


@runtime_checkable
class LifecyclePort(Protocol):
    """Ciclo de vida de un componente gestionado por el contenedor.

    Seis fases, en este orden exacto. La separacion no es ceremonia: cada
    frontera existe porque algo puede fallar ahi y el diagnostico cambia segun
    donde falle.

    * `initialize`  construir estado interno. Sin I/O. Si falla, es un bug de
                    configuracion y el sistema no debe arrancar.
    * `load`        traer del exterior lo que haga falta (conectar, leer datos).
                    Si falla, es el mundo y puede reintentarse.
    * `warmup`      preparar lo que necesita historia previa: calentar
                    indicadores, llenar buffers. Se separa de `load` porque en
                    vivo el calentamiento consume barras reales y su duracion es
                    la ventana en la que el sistema todavia no puede decidir.
    * `start`       empezar a operar.
    * `stop`        dejar de aceptar trabajo nuevo, terminar el que hay.
    * `dispose`     liberar recursos. Debe ser idempotente y no puede fallar:
                    se invoca tambien durante una parada de emergencia, y una
                    excepcion aqui dejaria recursos abiertos.

    El contenedor las ejecuta en orden topologico de dependencias -nada arranca
    antes de aquello de lo que depende- y en orden inverso al parar.
    """

    def initialize(self) -> None: ...

    def load(self) -> None: ...

    def warmup(self) -> None: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def dispose(self) -> None: ...


@runtime_checkable
class TradeRepositoryPort(Protocol):
    """Persistencia de operaciones cerradas.

    Se separa de `StrategyRepositoryPort` porque el volumen y el patron de acceso
    son distintos: las estrategias son cientos y se leen por identificador; las
    operaciones son millones y se leen por rango y por estrategia. Un unico
    repositorio obligaria a que el adaptador optimizara para dos cargas opuestas.
    """

    def append(self, trades: Sequence[Trade], *, run_id: RunId) -> None: ...

    def by_strategy(self, strategy_id: StrategyId) -> Sequence[Trade]: ...

    def by_run(self, run_id: RunId) -> Sequence[Trade]: ...


@runtime_checkable
class DatasetRepositoryPort(Protocol):
    """Catalogo de series con identidad propia.

    Cada dataset tiene huella, linaje y validacion registrados. Es lo que permite
    que un artefacto declare sobre que datos exactos se calculo, y detectar que
    una serie se reproceso desde entonces: mismo simbolo y mismo rango, huella
    distinta.
    """

    def register(self, bars: Bars, *, lineage: Mapping[str, Any]) -> str: ...

    def get(self, fingerprint: str) -> Bars: ...

    def lineage(self, fingerprint: str) -> Mapping[str, Any]: ...

    def exists(self, fingerprint: str) -> bool: ...


@runtime_checkable
class ConfigurationRepositoryPort(Protocol):
    """Archivo de configuraciones efectivas por corrida.

    Guarda la configuracion resuelta junto a su traza de procedencia. Sin esto,
    reproducir una corrida antigua exige adivinar que ficheros estaban activos y
    con que valores.
    """

    def save(
        self, run_id: RunId, *, effective: Mapping[str, Any], trace: Mapping[str, Any]
    ) -> str: ...

    def get(self, run_id: RunId) -> Mapping[str, Any]: ...

    def by_fingerprint(self, fingerprint: str) -> Sequence[RunId]:
        """Corridas que compartieron exactamente la misma configuracion.

        Es la consulta que responde "esto ya se probo": si dos corridas con la
        misma huella de configuracion y de datos dieron resultados distintos, hay
        un fallo de determinismo que hay que investigar antes de seguir.
        """
        ...


@runtime_checkable
class PromotionRepositoryPort(Protocol):
    """Historial de decisiones de promocion y aprobacion.

    Registra ambas por separado, porque son dos autoridades distintas: la
    promocion es tecnica y la aprobacion es humana. Una estrategia puede estar
    promovida y no aprobada, y el sistema debe poder decir cual de las dos falta.
    """

    def record_promotion(
        self, strategy_id: StrategyId, *, verdict: bool, evidence: Mapping[str, Any]
    ) -> None: ...

    def record_approval(
        self, strategy_id: StrategyId, *, approved_by: str, at_ns: TimestampNs, notes: str
    ) -> None: ...

    def history(self, strategy_id: StrategyId) -> Sequence[Mapping[str, Any]]: ...

    def is_approved(self, strategy_id: StrategyId) -> bool: ...


@runtime_checkable
class StrategyRepositoryPort(Protocol):
    """El zoo: catalogo de estrategias con su estado de ciclo de vida.

    Guarda `StrategySpec` serializado, nunca objetos binarios. Una estrategia
    debe poder reconstruirse anos despues con el codigo vigente, y la
    discrepancia entre el resultado archivado y el recalculado es, en si misma,
    una senal de alarma valiosa.
    """

    def save(
        self, spec: StrategySpec, metrics: PerformanceMetrics, evidence: Mapping[str, Any]
    ) -> None: ...

    def get(self, strategy_id: StrategyId) -> StrategySpec: ...

    def list_by_state(self, state: str) -> Sequence[StrategySpec]: ...

    def exists(self, strategy_id: StrategyId) -> bool: ...


# ---------------------------------------------------------------------------
# Observabilidad
# ---------------------------------------------------------------------------


@runtime_checkable
class EventSinkPort(Protocol):
    """Destino de eventos estructurados.

    Los eventos son datos, no cadenas de texto. Un log que solo se puede leer
    con los ojos no permite construir el embudo de descarte ni responder "por
    que no entro" sobre 200.000 barras.
    """

    def emit(self, event: str, **fields: Any) -> None: ...

    def bind(self, **fields: Any) -> EventSinkPort:
        """Devuelve un sink con contexto fijo (run_id, simbolo, estrategia)."""
        ...


@runtime_checkable
class RunContextPort(Protocol):
    """Identidad y procedencia de una corrida.

    Todo artefacto producido debe poder responder: quien lo genero, con que
    codigo, con que configuracion, con que datos y con que semilla.
    """

    @property
    def run_id(self) -> RunId: ...

    @property
    def seed(self) -> int: ...

    @property
    def config_hash(self) -> str: ...

    @property
    def code_version(self) -> str: ...

    def to_dict(self) -> Mapping[str, Any]: ...


# ---------------------------------------------------------------------------
# Motores de investigacion
# ---------------------------------------------------------------------------


@runtime_checkable
class BacktestEnginePort(Protocol):
    """Motor de simulacion historica.

    Contrato de determinismo: dos llamadas con los mismos argumentos devuelven
    exactamente la misma secuencia de operaciones. Se verifica en CI.
    """

    def run(
        self,
        *,
        spec: StrategySpec,
        bars: Bars,
        instrument: Instrument,
        initial_equity: float,
        seed: int,
    ) -> tuple[Sequence[Trade], FloatArray]: ...


@runtime_checkable
class StatisticalTestPort(Protocol):
    """Prueba de validacion estadistica.

    Toda prueba devuelve la misma forma: estadistico, p-valor y veredicto. Asi
    la capa de promocion puede componer pruebas heterogeneas sin conocerlas.
    """

    @property
    def name(self) -> str: ...

    def run(
        self,
        *,
        trades: Sequence[Trade],
        equity: FloatArray,
        bars: Bars,
        seed: int,
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class PromotionPolicyPort(Protocol):
    """Decide si un candidato entra al zoo.

    Devuelve siempre la razon, tanto en aceptacion como en rechazo. Un rechazo
    sin motivo registrado obliga a repetir el trabajo para entender la decision.
    """

    def evaluate(
        self,
        *,
        spec: StrategySpec,
        folds: Sequence[PerformanceMetrics],
        tests: Mapping[str, Mapping[str, Any]],
    ) -> tuple[bool, ValidationReport]: ...


@runtime_checkable
class StrategyFitterPort(Protocol):
    """Ajusta un candidato sobre un tramo de datos y devuelve la variante.

    Es lo que permite que walk-forward orqueste IS/OOS sin conocer al
    optimizador: la matriz no deja que `walkforward` importe `optimization`, y
    la direccion es la correcta -la validacion es una metodologia, no un motor
    de busqueda-. Quien compone inyecta el ajustador.

    Recibe BARRAS y no una huella de dataset: los tramos de un fold son rebanadas
    en memoria, no series catalogadas.
    """

    def fit(self, spec: StrategySpec, bars: Bars, *, seed: int) -> StrategySpec: ...


@runtime_checkable
class ObjectivePort(Protocol):
    """Puntua un candidato sobre una serie. Mayor es mejor.

    Es la frontera que permite optimizar sin conocer como se evalua. Hoy lo
    implementa un doble; manana lo implementara la cadena backtest + analytics
    sin que el optimizador cambie una linea.

    La puntuacion es SIEMPRE dentro de muestra: el optimizador ve los mismos
    datos sobre los que ajusta. Por eso su resultado no es evidencia de nada
    todavia, y por eso existe walk-forward.
    """

    def score(self, spec: StrategySpec, bars: Bars) -> float: ...


@runtime_checkable
class FoldEvidencePort(Protocol):
    """Un fold ya evaluado, visto por quien lo juzga.

    Solo las dos puntuaciones. Quien valida no necesita saber que rango temporal
    ocupaba el fold ni que variante se ajusto en el: pedirlo ataria la validacion
    a la forma concreta del resultado de walk-forward.
    """

    @property
    def is_score(self) -> float: ...

    @property
    def oos_score(self) -> float: ...


@runtime_checkable
class WalkForwardEvidencePort(Protocol):
    """Evidencia de un walk-forward, vista por quien la somete a contraste.

    Existe porque `architecture.toml` declara `validation` y `walkforward` como
    capacidades HERMANAS -misma `capability = "Validation"`, y `walkforward` no
    esta en el `depends` de `validation`-, no como proveedor y consumidor. Quien
    puede ver a las dos es `promotion`.

    La consecuencia es deseable y no un rodeo: la validacion estadistica opera
    sobre puntuaciones por fold, y le da igual si las produjo un walk-forward, un
    combinatorial purged CV o una reejecucion archivada. Al ser estructural, el
    resultado de walk-forward la cumple sin importar este modulo ni conocerlo.
    """

    @property
    def spec(self) -> StrategySpec: ...

    @property
    def outcomes(self) -> Sequence[FoldEvidencePort]: ...

    @property
    def dataset_fingerprint(self) -> str: ...

    @property
    def seed(self) -> int: ...


@runtime_checkable
class FoldTestPort(Protocol):
    """Contraste de hipotesis sobre las puntuaciones por fold.

    Hermano de `StatisticalTestPort` y deliberadamente distinto: aquel recibe
    `trades`, `equity` y `bars` -opera sobre UN backtest-, mientras que este
    recibe la serie de puntuaciones de N folds. Forzar las pruebas de fold en
    aquella firma obligaria a inventar trades que no existen.

    `alpha` entra como parametro y no se lee dentro: el umbral que separa
    "significativo" de "ruido" es politica, no propiedad de la prueba. La prueba
    calcula el p-valor; el veredicto se sella con el umbral vigente para poder
    auditar despues con cual se decidio.
    """

    @property
    def name(self) -> str: ...

    def run(
        self,
        *,
        is_scores: FloatArray,
        oos_scores: FloatArray,
        alpha: float,
        seed: int,
    ) -> StatisticalTestResult: ...


@runtime_checkable
class SearchSpacePort(Protocol):
    """Espacio de busqueda enumerable y muestreable de discovery."""

    def sample(self, rng: Any, count: int) -> Iterable[StrategySpec]: ...

    def mutate(self, spec: StrategySpec, rng: Any) -> StrategySpec: ...

    def recombine(self, left: StrategySpec, right: StrategySpec, rng: Any) -> StrategySpec: ...

    def cardinality(self) -> int | None:
        """Tamano del espacio, o `None` si es continuo o intratable.

        Se declara para que discovery pueda informar de la cobertura real de la
        busqueda en lugar de sugerir exhaustividad donde no la hay.
        """
        ...


__all__ = [
    "ArtifactStorePort",
    "BacktestEnginePort",
    "BrokerPort",
    "ClockPort",
    "ConfigurationRepositoryPort",
    "DatasetRepositoryPort",
    "EventSinkPort",
    "ExecutionSimulatorPort",
    "FeatureFn",
    "FoldEvidencePort",
    "FoldTestPort",
    "InstrumentCatalogPort",
    "LifecyclePort",
    "LiveFeedPort",
    "MarketDataPort",
    "MarketDataWriterPort",
    "PromotionPolicyPort",
    "PromotionRepositoryPort",
    "RiskPolicyPort",
    "RunContextPort",
    "SearchSpacePort",
    "SignalBlockFn",
    "StatisticalTestPort",
    "StrategyFitterPort",
    "StrategyRepositoryPort",
    "TradeRepositoryPort",
    "WalkForwardEvidencePort",
]
