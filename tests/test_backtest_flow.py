"""El primer recorrido ejecutable de la plataforma, de extremo a extremo.

    Parquet -> DatasetRepository -> Strategy -> BacktestEngine -> Risk
            -> ExecutionSimulator -> Analytics -> ArtifactStore

Importa mas como PRUEBA DE LA ARQUITECTURA que como prueba de funcionalidad. La
pregunta que responde no es "sale el numero correcto" -de eso se ocupan
`test_backtest.py`, `test_risk.py` y `test_analytics.py` sobre cada pieza- sino
"puede construirse un sistema real sin tocar la arquitectura", que es el
criterio literal de la Fase 4.5.

Por eso se ejercita con adaptadores REALES -Parquet en disco, catalogo de
ficheros, almacen de artefactos- y no con dobles. Un doble en cada extremo
verificaria que el servicio llama a lo que dice llamar; lo que aqui se quiere
saber es si las piezas encajan de verdad cuando ninguna sabe de las otras.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.application.backtest import (
    BacktestApplicationService,
    BacktestRequest,
    build_backtest_service,
)
from app.config.strategies import load_strategy_spec
from app.container.bootstrap import FrozenClock, platform_services
from app.core.exceptions import (
    ConfigNotFound,
    ConfigValidationError,
    DataSourceError,
    InvariantViolation,
)
from app.core.types import Symbol, Timeframe
from app.domain.entities.bars import Bars
from app.domain.value_objects.strategy_spec import BlockSpec, StrategySpec
from app.research.data.catalog import ParquetDatasetCatalog
from app.research.data.layout import DatasetLayout
from app.research.data.parquet_writer import ParquetMarketDataWriter

ROOT = Path(__file__).resolve().parent.parent
M15_NS = 15 * 60 * 1_000_000_000
REFERENCE_STRATEGY = ROOT / "configs" / "strategies" / "ema_cross_m15.toml"


def _bars(count: int = 600) -> Bars:
    """Serie sintetica con una oscilacion que cruza las medias varias veces.

    Sintetica y no descargada: `configs/runtime.toml` exige
    `require_synthetic_data_only` en modo `ci`, de modo que la suite no puede
    depender de un terminal ni de un fichero descargado por alguien.

    La oscilacion importa. Con una rampa monotona el cruce de medias entra una
    vez y no vuelve a operar, y el recorrido pasaria en verde sin haber
    ejercitado ni el cierre por senal ni el stop.
    """
    timestamps = np.arange(0, count * M15_NS, M15_NS, dtype=np.int64)
    index = np.arange(count, dtype=np.float64)
    close = 1.10 + 0.01 * np.sin(index / 18.0) + 0.0004 * np.sin(index / 3.0)
    return Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=timestamps,
        open=close - 0.00005,
        high=close + 0.00030,
        low=close - 0.00030,
        close=close,
        volume=np.full(count, 100.0),
    )


def _whipsaw_bars(count: int = 600) -> Bars:
    """Serie que hace PERDER al cruce de medias: oscila mas rapido que la media.

    Cada cruce se revierte antes de que la posicion avance, de modo que la
    estrategia entra y sale pagando costes. Es el reverso deliberado de `_bars`:
    una cadena verificada solo con corridas ganadoras no distingue una metrica
    bien calculada de una con el signo cambiado.
    """
    timestamps = np.arange(0, count * M15_NS, M15_NS, dtype=np.int64)
    index = np.arange(count, dtype=np.float64)
    close = 1.10 + 0.004 * np.sin(index / 2.5)
    return Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=timestamps,
        open=close - 0.00005,
        high=close + 0.00030,
        low=close - 0.00030,
        close=close,
        volume=np.full(count, 100.0),
    )


@pytest.fixture
def losing_dataset(tmp_path: Path) -> tuple[Path, str]:
    """Serie registrada cuya evaluacion termina en perdida."""
    layout = DatasetLayout(root=tmp_path / "data")
    ParquetMarketDataWriter(layout).write(_whipsaw_bars())
    fingerprint = ParquetDatasetCatalog(layout).register(
        _whipsaw_bars(), lineage={"provider": "SYNTHETIC"}
    )
    return tmp_path, fingerprint


@pytest.fixture
def dataset(tmp_path: Path) -> tuple[Path, str]:
    """Escribe una serie en Parquet y la registra. Devuelve raiz y huella.

    Es el punto de partida REAL del flujo: lo que `qp download` deja en disco.
    """
    layout = DatasetLayout(root=tmp_path / "data")
    ParquetMarketDataWriter(layout).write(_bars())
    catalog = ParquetDatasetCatalog(layout)
    fingerprint = catalog.register(_bars(), lineage={"provider": "SYNTHETIC"})
    return tmp_path, fingerprint


def _service(root: Path, data_root: Path, **overrides: object) -> BacktestApplicationService:
    """Servicio compuesto con la raiz real del proyecto para instrumentos.

    `platform_services` compone la plataforma de verdad -no un doble- porque
    parte de lo que se verifica es que el contenedor entregue el catalogo de
    instrumentos: hasta esta etapa era un adaptador que nadie inyectaba.
    """
    platform = platform_services(ROOT)
    return build_backtest_service(
        root,
        clock=platform.clock,
        instruments=platform.instruments,
        config_hash=platform.config_hash,
        limits=platform.risk_limits,
        data_root=data_root,
        **overrides,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 1. El recorrido completo
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_whole_chain_runs_on_real_adapters(dataset: tuple[Path, str]) -> None:
    """De Parquet a artefacto, sin un solo doble por el camino."""
    root, fingerprint = dataset
    service = _service(root, root / "data")

    report = service.run(
        BacktestRequest(
            spec=load_strategy_spec(REFERENCE_STRATEGY),
            dataset_fingerprint=fingerprint,
            initial_equity=10_000.0,
        )
    )

    assert report.n_trades > 0, "la serie no llego a producir ninguna operacion"
    assert report.metrics.n_trades == report.n_trades
    assert report.metrics.bars > 0
    assert len(report.artifacts) == 2


@pytest.mark.integration
def test_the_artifact_carries_everything_needed_to_repeat_the_run(
    dataset: tuple[Path, str],
) -> None:
    """Un artefacto sin procedencia no es reconstruible (P1).

    Se exige que esten los CUATRO componentes que la Constitucion nombra:
    semilla, hash de configuracion, hash de datos y version de codigo.
    """
    root, fingerprint = dataset
    service = _service(root, root / "data")

    report = service.run(
        BacktestRequest(
            spec=load_strategy_spec(REFERENCE_STRATEGY),
            dataset_fingerprint=fingerprint,
            initial_equity=10_000.0,
            seed=7,
        )
    )

    payload = json.loads(Path(report.artifacts[0]).read_text(encoding="utf-8"))
    provenance = payload["provenance"]

    assert provenance["seed"] == 7
    assert provenance["config_hash"]
    assert provenance["dataset_hash"] == fingerprint
    assert provenance["code_version"]
    assert payload["strategy"]["strategy_id"] == report.strategy_id
    assert payload["metrics"]["n_trades"] == report.n_trades
    assert payload["run"]["ambiguous_bars"] == report.ambiguous_bars


@pytest.mark.integration
def test_a_losing_run_cannot_report_a_positive_sharpe(
    losing_dataset: tuple[Path, str],
) -> None:
    """Una corrida que pierde dinero no puede salir bien parada del ratio.

    Es coherencia de signo, y es ademas lo unico que detecta un error de
    ANCLAJE: los retornos por barra se miden contra el capital de la corrida, y
    el primero contra el capital INICIAL. Una mutacion que pasaba a analytics un
    capital falso sobrevivia a todos los demas tests -`net_profit` sale de las
    operaciones y no cambia, y el drawdown apenas se mueve porque la curva
    alcanza su nivel en la primera barra- pero convertia ese primer retorno en
    un +100.000%, y con el, una corrida perdedora en una de Sharpe altisimo.

    Se ejercita ademas el caso que ningun otro test cubria: el recorrido
    completo sobre una estrategia que PIERDE. Una cadena verificada solo con
    resultados ganadores esconde justo los signos invertidos.
    """
    root, fingerprint = losing_dataset
    equity = 10_000.0
    report = _service(root, root / "data").run(
        BacktestRequest(
            spec=load_strategy_spec(REFERENCE_STRATEGY),
            dataset_fingerprint=fingerprint,
            initial_equity=equity,
        )
    )

    assert report.metrics.net_profit < 0.0, "la serie no llego a producir perdidas"
    assert report.final_equity < equity
    assert report.metrics.sharpe < 0.0
    assert report.metrics.net_profit == pytest.approx(report.final_equity - equity, abs=1e-6)


@pytest.mark.integration
def test_the_trades_table_holds_one_line_per_operation(dataset: tuple[Path, str]) -> None:
    """JSON Lines: una operacion por linea, legible en flujo."""
    root, fingerprint = dataset
    service = _service(root, root / "data")

    report = service.run(
        BacktestRequest(
            spec=load_strategy_spec(REFERENCE_STRATEGY),
            dataset_fingerprint=fingerprint,
            initial_equity=10_000.0,
        )
    )

    lines = Path(report.artifacts[1]).read_text(encoding="utf-8").splitlines()
    assert len(lines) == report.n_trades
    first = json.loads(lines[0])
    assert first["symbol"] == "EURUSD"
    assert "net_pnl" in first and "exit_reason" in first


# ---------------------------------------------------------------------------
# 2. Determinismo del recorrido
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_two_identical_runs_produce_the_same_identity_and_the_same_bytes(
    dataset: tuple[Path, str],
) -> None:
    """Misma entrada, mismo `run_id` y mismo artefacto byte a byte (P1).

    Es lo que justifica que `qp backtest` componga en modo `deterministic`: con
    el reloj de pared el instante entraria en el `run_id` y cada ejecucion
    escribiria en una carpeta nueva, de modo que la reproducibilidad no podria
    demostrarse ni desmentirse.
    """
    root, fingerprint = dataset

    def once() -> tuple[str, bytes]:
        report = _service(root, root / "data").run(
            BacktestRequest(
                spec=load_strategy_spec(REFERENCE_STRATEGY),
                dataset_fingerprint=fingerprint,
                initial_equity=10_000.0,
                seed=3,
            )
        )
        return str(report.run_id), Path(report.artifacts[0]).read_bytes()

    first_id, first_bytes = once()
    second_id, second_bytes = once()

    assert first_id == second_id
    assert first_bytes == second_bytes


@pytest.mark.integration
def test_a_different_seed_produces_a_different_run(dataset: tuple[Path, str]) -> None:
    """La semilla entra en la identidad, aunque hoy el recorrido no muestree.

    Si no entrase, dos corridas con semillas distintas compartirian carpeta y la
    segunda sobrescribiria a la primera el dia que algo empiece a muestrear.
    """
    root, fingerprint = dataset
    service = _service(root, root / "data")

    def with_seed(seed: int) -> str:
        report = service.run(
            BacktestRequest(
                spec=load_strategy_spec(REFERENCE_STRATEGY),
                dataset_fingerprint=fingerprint,
                initial_equity=10_000.0,
                seed=seed,
            )
        )
        return str(report.run_id)

    assert with_seed(1) != with_seed(2)


# ---------------------------------------------------------------------------
# 3. El modelo de llenado cambia el resultado, y por eso se puede elegir
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_adverse_fill_never_flatters_the_result(dataset: tuple[Path, str]) -> None:
    """El modelo pesimista no puede producir un neto mejor que el optimista.

    `ExecutionSimulatorPort` existe, segun su propio contrato, para poder
    comparar los dos: la diferencia mide la fragilidad de la estrategia frente a
    la calidad de ejecucion. Si el adverso saliera mejor, el modelo estaria
    invertido y el backtest seria optimista sin que nada lo delatara.

    La desigualdad es ESTRICTA. Con `<=` sobrevivia una mutacion que hacia
    heredar al modelo adverso del optimista: los dos daban el mismo numero, la
    medida de fragilidad quedaba en cero para toda estrategia y nada lo
    detectaba. Un modelo pesimista que produce exactamente el mismo resultado
    que el realista no esta midiendo nada.
    """
    root, fingerprint = dataset

    def net_with(fill_model: str) -> float:
        report = _service(root, root / "data", fill_model=fill_model).run(
            BacktestRequest(
                spec=load_strategy_spec(REFERENCE_STRATEGY),
                dataset_fingerprint=fingerprint,
                initial_equity=10_000.0,
            )
        )
        return float(report.metrics.net_profit)

    assert net_with("ADVERSE") < net_with("OPEN")


@pytest.mark.integration
def test_the_two_fill_models_do_not_overwrite_each_other(dataset: tuple[Path, str]) -> None:
    """Dos supuestos de ejecucion, dos artefactos, y cada uno dice cual es.

    Es el defecto que encontro la ejecucion real y que ningun test veia: la
    corrida adversa producia un neto distinto y escribia ENCIMA de la optimista,
    porque la ruta colgaba del `run_id` -que no incluye ni la estrategia, ni el
    dataset, ni el supuesto de ejecucion- y el reloj estaba detenido. El
    resultado archivado no permitia saber cual de los dos modelos lo produjo.
    """
    root, fingerprint = dataset

    def run_with(fill_model: str) -> tuple[str, dict[str, object]]:
        report = _service(root, root / "data", fill_model=fill_model).run(
            BacktestRequest(
                spec=load_strategy_spec(REFERENCE_STRATEGY),
                dataset_fingerprint=fingerprint,
                initial_equity=10_000.0,
            )
        )
        payload = json.loads(Path(report.artifacts[0]).read_text(encoding="utf-8"))
        return report.artifacts[0], payload

    open_path, open_payload = run_with("OPEN")
    adverse_path, adverse_payload = run_with("ADVERSE")

    assert open_path != adverse_path
    assert Path(open_path).is_file(), "la corrida adversa sobrescribio a la optimista"
    assert open_payload["execution"] == {"fill_model": "OPEN"}
    assert adverse_payload["execution"] == {"fill_model": "ADVERSE"}


@pytest.mark.integration
def test_two_different_strategies_do_not_share_an_artifact(dataset: tuple[Path, str]) -> None:
    """La estrategia entra en la ruta. Con `run_id` no entraba.

    `derive_run_id` compone configuracion, codigo, semilla e instante; con el
    reloj detenido, dos estrategias distintas sobre el mismo dataset producian
    el MISMO identificador. El `digest` de `RunFingerprint` si las distingue.
    """
    root, fingerprint = dataset
    service = _service(root, root / "data")
    other = StrategySpec(
        symbol=Symbol("EURUSD"),
        timeframe=Timeframe.M15,
        entries=(BlockSpec(name="ema_cross", params={"fast": 5, "slow": 21}),),
    )

    first = service.run(
        BacktestRequest(
            spec=load_strategy_spec(REFERENCE_STRATEGY),
            dataset_fingerprint=fingerprint,
            initial_equity=10_000.0,
        )
    )
    second = service.run(
        BacktestRequest(spec=other, dataset_fingerprint=fingerprint, initial_equity=10_000.0)
    )

    assert first.strategy_id != second.strategy_id
    assert first.artifacts[0] != second.artifacts[0]
    assert Path(first.artifacts[0]).is_file()


@pytest.mark.integration
def test_an_unknown_fill_model_is_rejected_instead_of_defaulting(dataset: tuple[Path, str]) -> None:
    """Un nombre mal escrito no puede caer en la simulacion MAS favorable."""
    root, _ = dataset
    with pytest.raises(ConfigValidationError, match="llenado"):
        _service(root, root / "data", fill_model="OPTIMISTA")


# ---------------------------------------------------------------------------
# 4. Lo que el flujo se niega a hacer
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_an_unknown_dataset_fails_at_the_catalog(dataset: tuple[Path, str]) -> None:
    """Se pide una huella; una huella que nadie registro no se inventa."""
    root, _ = dataset
    service = _service(root, root / "data")

    with pytest.raises(DataSourceError, match="no registrada"):
        service.run(
            BacktestRequest(
                spec=load_strategy_spec(REFERENCE_STRATEGY),
                dataset_fingerprint="sha256:noexiste",
                initial_equity=10_000.0,
            )
        )


@pytest.mark.integration
def test_a_strategy_on_an_undeclared_instrument_is_rejected(dataset: tuple[Path, str]) -> None:
    """Sin especificacion de instrumento no hay dimensionamiento posible.

    El catalogo de instrumentos no inventa un `value_per_point_per_lot`: un
    valor asumido produce un tamano silenciosamente equivocado, y ese error no se
    nota hasta que la posicion es diez veces mayor de lo previsto.
    """
    root, fingerprint = dataset
    service = _service(root, root / "data")
    spec = StrategySpec(
        symbol=Symbol("NOEXISTE"),
        timeframe=Timeframe.M15,
        entries=(BlockSpec(name="ema_cross", params={"fast": 12, "slow": 50}),),
    )

    with pytest.raises(ConfigNotFound, match="instrumento"):
        service.run(
            BacktestRequest(spec=spec, dataset_fingerprint=fingerprint, initial_equity=10_000.0)
        )


@pytest.mark.unit
def test_a_request_without_capital_is_rejected() -> None:
    """El capital inicial es la base del dimensionamiento fraccional."""
    spec = StrategySpec(
        symbol=Symbol("EURUSD"),
        timeframe=Timeframe.M15,
        entries=(BlockSpec(name="ema_cross"),),
    )
    with pytest.raises(InvariantViolation, match="capital"):
        BacktestRequest(spec=spec, dataset_fingerprint="sha256:x", initial_equity=0.0)


@pytest.mark.unit
def test_a_request_without_a_dataset_is_rejected() -> None:
    """Una corrida sin serie declarada no es reconstruible."""
    spec = StrategySpec(
        symbol=Symbol("EURUSD"),
        timeframe=Timeframe.M15,
        entries=(BlockSpec(name="ema_cross"),),
    )
    with pytest.raises(InvariantViolation, match="dataset"):
        BacktestRequest(spec=spec, dataset_fingerprint="", initial_equity=10_000.0)


# ---------------------------------------------------------------------------
# 5. La declaracion de estrategia
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_reference_strategy_is_readable_and_complete() -> None:
    """El fichero de referencia describe una estrategia valida y compilable."""
    spec = load_strategy_spec(REFERENCE_STRATEGY)

    assert spec.symbol == Symbol("EURUSD")
    assert spec.timeframe is Timeframe.M15
    assert spec.entries and spec.exits
    assert spec.risk["stop_atr_multiple"] == 2.0


@pytest.mark.unit
def test_a_missing_strategy_file_is_named_in_the_error(tmp_path: Path) -> None:
    """El error dice QUE fichero falta; sin la ruta habria que adivinarla."""
    with pytest.raises(ConfigNotFound, match="estrategia"):
        load_strategy_spec(tmp_path / "no_existe.toml")


@pytest.mark.unit
def test_an_incomplete_declaration_names_the_missing_keys(tmp_path: Path) -> None:
    path = tmp_path / "rota.toml"
    path.write_text('symbol = "EURUSD"\n', encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="incompleta"):
        load_strategy_spec(path)


@pytest.mark.unit
def test_a_malformed_declaration_is_reported_as_configuration(tmp_path: Path) -> None:
    """Un TOML roto es un error de configuracion, no una excepcion de libreria."""
    path = tmp_path / "rota.toml"
    path.write_text("symbol = [unclosed\n", encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="TOML"):
        load_strategy_spec(path)


# ---------------------------------------------------------------------------
# 6. La plataforma entrega de verdad lo que el caso de uso necesita
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_container_now_provides_the_instrument_catalog() -> None:
    """Hasta esta etapa era un adaptador que nadie inyectaba.

    Un adaptador que la raiz de composicion no registra es un adaptador que en
    produccion no existe, por correcto que sea su codigo.
    """
    platform = platform_services(ROOT)

    assert platform.instruments.get(Symbol("EURUSD")).symbol == Symbol("EURUSD")
    assert platform.config_hash.startswith("sha256:")


@pytest.mark.integration
def test_platform_services_hands_out_a_frozen_clock_by_default() -> None:
    """Quien pide servicios para EJECUTAR algo recibe un reloj detenido.

    Es lo que hace repetible el `run_id`. Un reloj de pared por defecto haria
    que la reproducibilidad dependiera de que cada llamante se acuerde de
    pedirlo.
    """
    platform = platform_services(ROOT)

    assert isinstance(platform.clock, FrozenClock)
    assert platform.clock.now_ns() == platform.clock.now_ns()


# ---------------------------------------------------------------------------
# 7. La configuracion GOBIERNA la corrida (ADR-0015)
# ---------------------------------------------------------------------------


def _project_with_equity(tmp_path: Path, equity: float) -> Path:
    """Copia los contratos del proyecto cambiando el capital declarado."""
    import shutil

    root = tmp_path / "proyecto"
    shutil.copytree(ROOT / "configs", root / "configs")
    contract = root / "configs" / "backtest.toml"
    contract.write_text(
        contract.read_text(encoding="utf-8").replace(
            "initial_equity = 10000.0", f"initial_equity = {equity}"
        ),
        encoding="utf-8",
    )
    return root


@pytest.mark.integration
def test_the_declared_capital_governs_the_run(tmp_path: Path, dataset: tuple[Path, str]) -> None:
    """El capital de la corrida sale de `configs/backtest.toml`, no del codigo.

    Se comprueba con un valor DISTINTO del que trae el repositorio. Con el mismo,
    el test pasaria igual aunque la cifra estuviera escrita en el comando -y de
    hecho una mutacion que la devolvia a una constante sobrevivio a todos los
    demas tests, porque la constante coincidia con el fichero-. Lo que hay que
    demostrar es la PROCEDENCIA, no la igualdad.
    """
    from app.interfaces.cli.backtest_cmd import main

    data_root, fingerprint = dataset
    root = _project_with_equity(tmp_path, 55_000.0)

    code = main(
        [
            "--strategy",
            str(REFERENCE_STRATEGY),
            "--dataset",
            fingerprint,
            "--root",
            str(root),
            "--data",
            str(data_root / "data"),
        ]
    )

    assert code == 0
    artifacts = sorted((root / "artifacts").rglob("result.json"))
    assert len(artifacts) == 1
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert payload["request"]["initial_equity"] == 55_000.0


@pytest.mark.integration
def test_the_declared_risk_policy_governs_the_sizing(
    tmp_path: Path, dataset: tuple[Path, str]
) -> None:
    """Bajar `risk_fraction` en el fichero reduce lo que se opera.

    Es la comprobacion que faltaba para que `configs/risk.toml` deje de ser
    decorativo: antes de ADR-0015 se podia dividir entre diez la fraccion de
    riesgo sin que cambiara una sola operacion.
    """
    import shutil

    from app.interfaces.cli.backtest_cmd import main

    data_root, fingerprint = dataset

    def net_with_fraction(fraction: float, name: str) -> float:
        root = tmp_path / name
        shutil.copytree(ROOT / "configs", root / "configs")
        contract = root / "configs" / "risk.toml"
        contract.write_text(
            contract.read_text(encoding="utf-8").replace(
                "risk_fraction = 0.01", f"risk_fraction = {fraction}"
            ),
            encoding="utf-8",
        )
        assert (
            main(
                [
                    "--strategy",
                    str(REFERENCE_STRATEGY),
                    "--dataset",
                    fingerprint,
                    "--root",
                    str(root),
                    "--data",
                    str(data_root / "data"),
                ]
            )
            == 0
        )
        artifact = next((root / "artifacts").rglob("result.json"))
        return float(json.loads(artifact.read_text(encoding="utf-8"))["metrics"]["net_profit"])

    assert abs(net_with_fraction(0.001, "prudente")) < abs(net_with_fraction(0.01, "normal"))
