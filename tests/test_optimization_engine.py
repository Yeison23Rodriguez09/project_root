"""`OptimizationEngine`: ajusta parametros, no aporta evidencia.

Lo que mas importa verificar es que su salida no se pueda confundir con
rendimiento: la puntuacion es dentro de muestra por construccion, y el
optimizador es exactamente el componente que mas facilmente produce numeros
bonitos y falsos.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.core.exceptions import DataSourceError, InvariantViolation
from app.core.registry.metadata import ComponentEntry
from app.core.registry.params import ParamSpec
from app.core.registry.registry import Registry
from app.core.types import LifecycleState, Symbol, Timeframe
from app.discovery.generator.search_space import BlockSearchSpace
from app.domain.entities.bars import Bars
from app.domain.value_objects.strategy_spec import StrategySpec
from app.optimization.engine import OptimizationEngine, OptimizationResult
from app.shared.ports import ObjectivePort

pytest.importorskip("pyarrow", reason="El catalogo se apoya en artefactos Parquet")


def _bars(count: int = 40) -> Bars:
    step = Timeframe.M15.nanoseconds
    ts = np.arange(0, count * step, step, dtype=np.int64)
    close = np.linspace(1.10, 1.12, count)
    return Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=ts,
        open=close - 0.00005,
        high=close + 0.00020,
        low=close - 0.00020,
        close=close,
        volume=np.full(count, 100.0),
    )


def _catalog(root: Path) -> tuple[Any, str]:
    from app.research.data.catalog import ParquetDatasetCatalog
    from app.research.data.layout import DatasetLayout
    from app.research.data.parquet_writer import ParquetMarketDataWriter

    layout = DatasetLayout(root=root)
    bars = _bars()
    ParquetMarketDataWriter(layout).write(bars, overwrite=True)
    catalog = ParquetDatasetCatalog(layout)
    return catalog, catalog.register(bars, lineage={"provider": "FILE"})


def _space() -> BlockSearchSpace:
    registry: Registry[Any] = Registry("signal")
    for name, family in (("ema", "trend"), ("rsi", "momentum")):
        registry.add(
            ComponentEntry(
                name=name,
                fn=lambda **_: None,
                tags=frozenset({family}),
                params=(ParamSpec(name="period", default=14, choices=(7, 14, 21, 28)),),
            )
        )
    return BlockSearchSpace(symbol=Symbol("EURUSD"), timeframe=Timeframe.M15, entries=registry)


class PeriodObjective:
    """Objetivo sintetico: puntua mejor cuanto mayor es el periodo.

    Deliberadamente trivial y con optimo conocido. Un objetivo realista haria
    que el test comprobara la calidad del backtest en lugar de la del buscador.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def score(self, spec: StrategySpec, bars: Bars) -> float:
        self.calls.append(str(spec.strategy_id))
        return float(sum(int(b.params.get("period", 0)) for b in spec.entries))


class ConstantObjective:
    def __init__(self, value: float = 1.0) -> None:
        self.value = value
        self.calls = 0

    def score(self, spec: StrategySpec, bars: Bars) -> float:
        self.calls += 1
        return self.value


def _candidate(space: BlockSearchSpace) -> StrategySpec:
    from app.core.determinism import rng_for

    return next(iter(space.sample(rng_for(1, "test"), 1)))


def _engine(catalog: Any, objective: Any) -> OptimizationEngine:
    return OptimizationEngine(catalog=catalog, space=_space(), objective=objective)


# ---------------------------------------------------------------------------
# Optimiza
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_objective_is_a_port() -> None:
    assert isinstance(PeriodObjective(), ObjectivePort)


@pytest.mark.integration
def test_optimization_improves_on_the_starting_point(tmp_path: Path) -> None:
    catalog, fingerprint = _catalog(tmp_path)
    engine = _engine(catalog, PeriodObjective())

    result = engine.optimize(_candidate(_space()), fingerprint, iterations=40, seed=5)

    assert result.improved
    assert result.in_sample_score > result.baseline_score
    assert result.gain > 0


@pytest.mark.integration
def test_the_baseline_is_reported_so_the_gain_is_auditable(tmp_path: Path) -> None:
    """Sin la puntuacion de partida no se sabe si la busqueda aporto algo o solo
    gasto computo."""
    catalog, fingerprint = _catalog(tmp_path)

    result = _engine(catalog, ConstantObjective()).optimize(
        _candidate(_space()), fingerprint, iterations=10, seed=1
    )

    assert result.baseline_score == 1.0
    assert result.in_sample_score == 1.0
    assert not result.improved
    assert result.gain == 0.0


@pytest.mark.integration
def test_the_same_seed_reproduces_the_search(tmp_path: Path) -> None:
    catalog, fingerprint = _catalog(tmp_path)
    candidate = _candidate(_space())

    first = _engine(catalog, PeriodObjective()).optimize(
        candidate, fingerprint, iterations=25, seed=9
    )
    second = _engine(catalog, PeriodObjective()).optimize(
        candidate, fingerprint, iterations=25, seed=9
    )

    assert first.best.strategy_id == second.best.strategy_id
    assert first.in_sample_score == second.in_sample_score
    assert first.evaluations == second.evaluations


@pytest.mark.integration
def test_a_variant_is_never_evaluated_twice(tmp_path: Path) -> None:
    """Reevaluar gastaria computo y, peor, inflaria el contador haciendo creer
    que la busqueda exploro mas de lo que exploro."""
    catalog, fingerprint = _catalog(tmp_path)
    objective = PeriodObjective()

    result = _engine(catalog, objective).optimize(
        _candidate(_space()), fingerprint, iterations=50, seed=3
    )

    assert len(objective.calls) == len(set(objective.calls))
    assert result.evaluations == len(objective.calls)


# ---------------------------------------------------------------------------
# Lo que el motor NO hace
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_result_stays_a_candidate(tmp_path: Path) -> None:
    """Optimizar no aporta evidencia: la variante sigue sin haber sido validada."""
    catalog, fingerprint = _catalog(tmp_path)

    result = _engine(catalog, PeriodObjective()).optimize(
        _candidate(_space()), fingerprint, iterations=15, seed=2
    )

    assert result.best.state is LifecycleState.CANDIDATE


@pytest.mark.integration
def test_optimizing_something_already_validated_is_rejected(tmp_path: Path) -> None:
    """Ajustar sobre algo validado invalidaria su evidencia sin decirlo: los
    numeros archivados dejarian de corresponder al spec."""
    catalog, fingerprint = _catalog(tmp_path)
    candidate = _candidate(_space())
    validated = StrategySpec(
        symbol=candidate.symbol,
        timeframe=candidate.timeframe,
        entries=candidate.entries,
        state=LifecycleState.VALIDATED,
    )

    with pytest.raises(InvariantViolation) as error:
        _engine(catalog, PeriodObjective()).optimize(validated, fingerprint, iterations=5, seed=1)

    assert "candidatos" in str(error.value)


@pytest.mark.unit
def test_the_score_is_named_as_in_sample() -> None:
    """Un nombre neutro invitaria a leerlo como rendimiento esperado, y esa
    confusion es la que walk-forward existe para deshacer.
    """
    campos = set(OptimizationResult.__dataclass_fields__)

    assert "in_sample_score" in campos
    assert "score" not in campos
    assert not campos & {"sharpe", "expected_return", "performance", "pnl"}


@pytest.mark.unit
def test_the_engine_only_knows_ports() -> None:
    """Toda dependencia del motor entra como Protocol, nunca como adaptador.

    Se comprueba por anotacion y no por nombre exacto de parametros: lo que
    importa es que ninguna colaboracion sea concreta, no que la lista sea
    literalmente esta. Los escalares de ajuste no son dependencias y no cuentan.
    """
    import inspect

    from app.shared import ports as contracts

    firma = inspect.signature(OptimizationEngine.__init__)
    escalares = {"int", "float", "str", "bool"}
    protocolos = {name for name in dir(contracts) if name.endswith("Port")}

    dependencias = {
        name: str(p.annotation)
        for name, p in firma.parameters.items()
        if name != "self" and str(p.annotation).split("|")[0].strip() not in escalares
    }

    assert dependencias, "el motor tiene que recibir sus colaboraciones"
    for name, annotation in dependencias.items():
        assert annotation in protocolos, f"{name} no entra por puerto: {annotation}"
    assert not set(firma.parameters) & {"source", "broker", "terminal", "mt5"}


# ---------------------------------------------------------------------------
# Entradas invalidas
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_zero_iterations_is_rejected(tmp_path: Path) -> None:
    catalog, fingerprint = _catalog(tmp_path)

    with pytest.raises(InvariantViolation):
        _engine(catalog, PeriodObjective()).optimize(
            _candidate(_space()), fingerprint, iterations=0, seed=1
        )


@pytest.mark.integration
def test_an_unregistered_dataset_is_rejected(tmp_path: Path) -> None:
    catalog, _ = _catalog(tmp_path)

    with pytest.raises(DataSourceError):
        _engine(catalog, PeriodObjective()).optimize(
            _candidate(_space()), "huella-inventada", iterations=5, seed=1
        )


# ---------------------------------------------------------------------------
# Lote
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_a_batch_optimizes_every_candidate(tmp_path: Path) -> None:
    from app.core.determinism import rng_for

    catalog, fingerprint = _catalog(tmp_path)
    space = _space()
    candidates = tuple(space.sample(rng_for(4, "test"), 4))

    results = _engine(catalog, PeriodObjective()).optimize_all(
        candidates, fingerprint, iterations=12, seed=6
    )

    assert len(results) == len(candidates)
    assert all(r.best.state is LifecycleState.CANDIDATE for r in results)


@pytest.mark.integration
def test_reordering_the_batch_does_not_change_any_result(tmp_path: Path) -> None:
    """La semilla se deriva del `strategy_id`, no de la posicion en la lista."""
    from app.core.determinism import rng_for

    catalog, fingerprint = _catalog(tmp_path)
    space = _space()
    candidates = tuple(space.sample(rng_for(8, "test"), 3))
    engine = _engine(catalog, PeriodObjective())

    straight = engine.optimize_all(candidates, fingerprint, iterations=10, seed=2)
    reversed_ = engine.optimize_all(candidates[::-1], fingerprint, iterations=10, seed=2)

    by_id = {str(r.best.strategy_id): r.in_sample_score for r in straight}
    for result in reversed_:
        assert by_id[str(result.best.strategy_id)] == result.in_sample_score


@pytest.mark.integration
def test_the_report_records_what_makes_it_reproducible(tmp_path: Path) -> None:
    catalog, fingerprint = _catalog(tmp_path)

    volcado = (
        _engine(catalog, PeriodObjective())
        .optimize(_candidate(_space()), fingerprint, iterations=10, seed=77)
        .to_dict()
    )

    assert volcado["seed"] == 77
    assert volcado["dataset_fingerprint"] == fingerprint
    assert "in_sample_score" in volcado
    assert "baseline_score" in volcado
    assert volcado["evaluations"] >= 1
