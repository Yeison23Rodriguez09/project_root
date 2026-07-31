"""`DiscoveryEngine`: lee del catalogo y propone. Nada mas.

Lo mas importante que se verifica aqui es lo que el motor NO puede hacer: hablar
con MT5, optimizar, validar o promocionar. Un motor de descubrimiento que hiciera
cualquiera de esas cosas saltaria la puerta cientifica desde el primer eslabon.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.core.exceptions import DataSourceError, InvariantViolation
from app.core.registry.metadata import ComponentEntry
from app.core.registry.params import ParamSpec
from app.core.registry.registry import Registry
from app.core.types import LifecycleState, Symbol, Timeframe
from app.discovery.engine import DiscoveryEngine, DiscoveryResult
from app.discovery.generator.search_space import BlockSearchSpace
from app.domain.entities.bars import Bars
from app.domain.value_objects.strategy_spec import BlockSpec, StrategySpec

pytest.importorskip("pyarrow", reason="El catalogo se apoya en artefactos Parquet")

M15_NS = Timeframe.M15.nanoseconds


def _bars(symbol: str = "EURUSD", timeframe: Timeframe = Timeframe.M15, count: int = 60) -> Bars:
    step = timeframe.nanoseconds
    ts = np.arange(0, count * step, step, dtype=np.int64)
    close = np.linspace(1.10, 1.12, count)
    return Bars.from_arrays(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=ts,
        open=close - 0.00005,
        high=close + 0.00020,
        low=close - 0.00020,
        close=close,
        volume=np.full(count, 100.0),
    )


def _catalog(root: Path, bars: Bars) -> tuple[Any, str]:
    """Serie materializada y registrada; devuelve el catalogo y su huella."""
    from app.research.data.catalog import ParquetDatasetCatalog
    from app.research.data.layout import DatasetLayout
    from app.research.data.parquet_writer import ParquetMarketDataWriter

    layout = DatasetLayout(root=root)
    ParquetMarketDataWriter(layout).write(bars, overwrite=True)
    catalog = ParquetDatasetCatalog(layout)
    return catalog, catalog.register(bars, lineage={"provider": "FILE"})


def _space(symbol: str = "EURUSD", timeframe: Timeframe = Timeframe.M15) -> BlockSearchSpace:
    registry: Registry[Any] = Registry("signal")
    for name, family in (("ema_cross", "trend"), ("rsi_rev", "momentum"), ("atr_brk", "volatility")):
        registry.add(
            ComponentEntry(
                name=name,
                fn=lambda **_: None,
                tags=frozenset({family}),
                params=(ParamSpec(name="period", default=14, choices=(7, 14, 21, 28)),),
            )
        )
    return BlockSearchSpace(symbol=Symbol(symbol), timeframe=timeframe, entries=registry)


# ---------------------------------------------------------------------------
# Descubre
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_candidates_are_produced_from_a_catalogued_series(tmp_path: Path) -> None:
    catalog, fingerprint = _catalog(tmp_path, _bars())
    engine = DiscoveryEngine(catalog=catalog, space=_space())

    result = engine.discover(fingerprint, count=8, seed=42)

    assert isinstance(result, DiscoveryResult)
    assert len(result.candidates) == 8
    assert result.dataset_fingerprint == fingerprint
    assert not result.exhausted


@pytest.mark.integration
def test_every_produced_strategy_is_a_candidate(tmp_path: Path) -> None:
    """Discovery no puede emitir nada con evidencia que no tiene."""
    catalog, fingerprint = _catalog(tmp_path, _bars())
    engine = DiscoveryEngine(catalog=catalog, space=_space())

    result = engine.discover(fingerprint, count=6, seed=1)

    assert all(spec.state is LifecycleState.CANDIDATE for spec in result.candidates)


@pytest.mark.integration
def test_the_same_seed_and_dataset_reproduce_the_search(tmp_path: Path) -> None:
    """Una corrida de discovery que no se repite no es evidencia de nada."""
    catalog, fingerprint = _catalog(tmp_path, _bars())
    engine = DiscoveryEngine(catalog=catalog, space=_space())

    first = engine.discover(fingerprint, count=10, seed=7)
    second = engine.discover(fingerprint, count=10, seed=7)

    assert [s.strategy_id for s in first.candidates] == [
        s.strategy_id for s in second.candidates
    ]


@pytest.mark.integration
def test_a_different_seed_explores_elsewhere(tmp_path: Path) -> None:
    catalog, fingerprint = _catalog(tmp_path, _bars())
    engine = DiscoveryEngine(catalog=catalog, space=_space())

    first = {s.strategy_id for s in engine.discover(fingerprint, count=10, seed=1).candidates}
    second = {s.strategy_id for s in engine.discover(fingerprint, count=10, seed=2).candidates}

    assert first != second


@pytest.mark.integration
def test_exhaustion_is_reported_not_hidden(tmp_path: Path) -> None:
    """Que el espacio se agote es informacion, no un fallo.

    Significa que la busqueda cubrio todo lo que habia. Ocultarlo haria creer que
    se exploro una fraccion cuando se exploro el total.
    """
    catalog, fingerprint = _catalog(tmp_path, _bars())
    tiny: Registry[Any] = Registry("signal")
    tiny.add(
        ComponentEntry(
            name="solo",
            fn=lambda **_: None,
            tags=frozenset({"trend"}),
            params=(ParamSpec(name="period", default=5, choices=(5,)),),
        )
    )
    space = BlockSearchSpace(
        symbol=Symbol("EURUSD"), timeframe=Timeframe.M15, entries=tiny
    )

    result = DiscoveryEngine(catalog=catalog, space=space).discover(
        fingerprint, count=100, seed=3
    )

    assert result.exhausted
    assert 0 < len(result.candidates) < 100
    assert result.to_dict()["exhausted"] is True


# ---------------------------------------------------------------------------
# Lo que el motor NO hace
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_engine_only_knows_the_catalog_never_a_broker() -> None:
    """`discovery` no tiene `broker` en su `depends`, asi que un import hacia el
    terminal no compila. Esto fija ademas que no llegue por via indirecta: el
    motor recibe el catalogo, no una fuente de mercado.
    """
    import inspect

    parameters = set(inspect.signature(DiscoveryEngine.__init__).parameters)

    assert parameters == {"self", "catalog", "space"}
    assert not parameters & {"source", "broker", "terminal", "mt5", "feed"}


@pytest.mark.unit
def test_the_engine_does_not_evaluate_or_rank() -> None:
    """No optimiza: solo descubre.

    Un `DiscoveryResult` no lleva metricas de rendimiento porque en este punto no
    existen. Anadirlas haria que la busqueda pudiera ordenar por rentabilidad
    antes de que nadie haya validado nada.
    """
    campos = set(DiscoveryResult.__dataclass_fields__)

    assert not campos & {"metrics", "performance", "sharpe", "ranking", "score", "best"}


@pytest.mark.integration
def test_the_engine_refuses_to_emit_a_non_candidate(tmp_path: Path) -> None:
    """Si un espacio devolviera algo ya validado, el motor lo rechaza.

    Es la puerta que impide saltarse la promocion desde el primer eslabon.
    """
    catalog, fingerprint = _catalog(tmp_path, _bars())

    class PromotedSpace:
        symbol = Symbol("EURUSD")
        timeframe = Timeframe.M15

        def sample(self, rng: Any, count: int) -> Iterable[StrategySpec]:
            return (
                StrategySpec(
                    symbol=Symbol("EURUSD"),
                    timeframe=Timeframe.M15,
                    entries=(BlockSpec(name="x"),),  # type: ignore[arg-type]
                    state=LifecycleState.PROMOTED,
                ),
            )

        def mutate(self, spec: StrategySpec, rng: Any) -> StrategySpec:  # pragma: no cover
            return spec

        def recombine(
            self, left: StrategySpec, right: StrategySpec, rng: Any
        ) -> StrategySpec:  # pragma: no cover
            return left

        def cardinality(self) -> int | None:
            return 1

    engine = DiscoveryEngine(catalog=catalog, space=PromotedSpace())

    with pytest.raises(InvariantViolation) as error:
        engine.discover(fingerprint, count=1, seed=1)

    assert "candidatos" in str(error.value)


# ---------------------------------------------------------------------------
# Coherencia con la serie
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_series_must_match_the_search_space(tmp_path: Path) -> None:
    """Buscar arquitecturas de EURUSD sobre una serie de XAUUSD produce
    candidatos sintacticamente validos y semanticamente absurdos, y nada aguas
    abajo lo detectaria: el spec lleva su propio simbolo y parece coherente
    consigo mismo.
    """
    catalog, fingerprint = _catalog(tmp_path, _bars(symbol="XAUUSD"))
    engine = DiscoveryEngine(catalog=catalog, space=_space(symbol="EURUSD"))

    with pytest.raises(InvariantViolation) as error:
        engine.discover(fingerprint, count=4, seed=1)

    assert "instrumentos distintos" in str(error.value)


@pytest.mark.integration
def test_an_unregistered_dataset_is_rejected(tmp_path: Path) -> None:
    """Proponer estrategias para un dataset ausente produciria candidatos que
    nadie puede reevaluar."""
    catalog, _ = _catalog(tmp_path, _bars())
    engine = DiscoveryEngine(catalog=catalog, space=_space())

    with pytest.raises(DataSourceError):
        engine.discover("huella-inventada", count=4, seed=1)


@pytest.mark.integration
def test_a_reprocessed_dataset_is_rejected(tmp_path: Path) -> None:
    """La comprobacion de huella del catalogo protege tambien a discovery."""
    from app.research.data.layout import DatasetLayout
    from app.research.data.parquet_writer import ParquetMarketDataWriter

    catalog, fingerprint = _catalog(tmp_path, _bars())
    ParquetMarketDataWriter(DatasetLayout(root=tmp_path)).write(
        _bars(count=45), overwrite=True
    )
    engine = DiscoveryEngine(catalog=catalog, space=_space())

    with pytest.raises(DataSourceError):
        engine.discover(fingerprint, count=4, seed=1)


@pytest.mark.integration
def test_asking_for_no_candidates_is_rejected(tmp_path: Path) -> None:
    catalog, fingerprint = _catalog(tmp_path, _bars())
    engine = DiscoveryEngine(catalog=catalog, space=_space())

    with pytest.raises(InvariantViolation):
        engine.discover(fingerprint, count=0, seed=1)


# ---------------------------------------------------------------------------
# El informe
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_result_records_what_makes_it_reproducible(tmp_path: Path) -> None:
    catalog, fingerprint = _catalog(tmp_path, _bars())
    engine = DiscoveryEngine(catalog=catalog, space=_space())

    volcado: Mapping[str, Any] = engine.discover(fingerprint, count=5, seed=99).to_dict()

    assert volcado["seed"] == 99
    assert volcado["dataset_fingerprint"] == fingerprint
    assert volcado["requested"] == 5
    assert volcado["produced"] == 5
    assert volcado["space"]["cardinality"] is not None
    assert len(volcado["candidates"]) == 5
