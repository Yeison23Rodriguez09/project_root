"""Walk-forward: particionado y motor.

El test que justifica el fichero entero es
`test_the_fitter_never_sees_out_of_sample_bars`: comprueba que la variante
puntuada fuera de muestra se ajusto SOLO con datos de dentro. Si eso falla, todo
lo demas -WFE, estabilidad, promocion- son numeros bonitos sobre nada.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.core.exceptions import InsufficientHistory, InvariantViolation
from app.core.types import LifecycleState, Symbol, Timeframe
from app.domain.entities.bars import Bars
from app.domain.value_objects.strategy_spec import BlockSpec, StrategySpec
from app.walkforward import partition
from app.walkforward.engine import ANCHORED, ROLLING, WalkForwardEngine

pytest.importorskip("pyarrow", reason="El catalogo se apoya en artefactos Parquet")

STEP = Timeframe.M15.nanoseconds


def _bars(count: int = 3000) -> Bars:
    ts = np.arange(0, count * STEP, STEP, dtype=np.int64)
    close = np.linspace(1.10, 1.20, count)
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


def _spec() -> StrategySpec:
    return StrategySpec(
        symbol=Symbol("EURUSD"),
        timeframe=Timeframe.M15,
        entries=(BlockSpec(name="ema", params={"period": 14}),),  # type: ignore[arg-type]
    )


def _catalog(root: Path, bars: Bars) -> tuple[Any, str]:
    from app.research.data.catalog import ParquetDatasetCatalog
    from app.research.data.layout import DatasetLayout
    from app.research.data.parquet_writer import ParquetMarketDataWriter

    layout = DatasetLayout(root=root)
    ParquetMarketDataWriter(layout).write(bars, overwrite=True)
    catalog = ParquetDatasetCatalog(layout)
    return catalog, catalog.register(bars, lineage={"provider": "FILE"})


class RecordingFitter:
    """Ajustador que anota exactamente que barras vio."""

    def __init__(self) -> None:
        self.seen: list[tuple[int, int]] = []

    def fit(self, spec: StrategySpec, bars: Bars, *, seed: int) -> StrategySpec:
        self.seen.append((int(bars.timestamp[0]), int(bars.timestamp[-1])))
        return spec


class LengthObjective:
    """Puntua por numero de barras: distinto en IS y en OOS, y predecible."""

    def score(self, spec: StrategySpec, bars: Bars) -> float:
        return float(len(bars))


class FixedObjective:
    def __init__(self, is_value: float, oos_value: float, is_bars: int) -> None:
        self.is_value, self.oos_value, self.is_bars = is_value, oos_value, is_bars

    def score(self, spec: StrategySpec, bars: Bars) -> float:
        return self.is_value if len(bars) == self.is_bars else self.oos_value


# ---------------------------------------------------------------------------
# Particionado
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rolling_keeps_the_window_size_and_advances() -> None:
    folds = partition.rolling(_bars(), folds=4, is_bars=500, oos_bars=200)

    assert len(folds) == 4
    duraciones = {f.in_sample.duration_ns for f in folds}
    assert len(duraciones) == 1, "la ventana rolling debe mantener su tamano"
    inicios = [f.in_sample.start_ns for f in folds]
    assert inicios == sorted(inicios) and len(set(inicios)) == 4


@pytest.mark.unit
def test_anchored_keeps_the_start_and_grows() -> None:
    folds = partition.anchored(_bars(), folds=4, oos_bars=200, min_is_bars=500)

    assert len({f.in_sample.start_ns for f in folds}) == 1, "el ancla no se mueve"
    duraciones = [f.in_sample.duration_ns for f in folds]
    assert duraciones == sorted(duraciones) and duraciones[0] < duraciones[-1]


@pytest.mark.unit
@pytest.mark.parametrize("scheme", ["rolling", "anchored"], ids=["rolling", "anchored"])
def test_out_of_sample_is_always_after_in_sample(scheme: str) -> None:
    bars = _bars()
    folds = (
        partition.rolling(bars, folds=5, is_bars=400, oos_bars=150)
        if scheme == "rolling"
        else partition.anchored(bars, folds=5, oos_bars=150, min_is_bars=400)
    )

    for fold in folds:
        assert int(fold.out_of_sample.start_ns) >= int(fold.in_sample.end_ns)
        assert not fold.in_sample.overlaps(fold.out_of_sample)


@pytest.mark.unit
def test_both_schemes_measure_on_the_same_out_of_sample() -> None:
    """Comparar rolling contra anchored solo es justo si miden lo mismo.

    Los dos esquemas se diferencian en COMO ajustan, no en donde miden: el
    out-of-sample avanza igual en ambos. Si alguna vez dejaran de coincidir, la
    comparacion entre esquemas seguiria produciendo dos numeros, pero ya no
    serian comparables y nada en la salida lo delataria.
    """
    bars = _bars()
    rolling = partition.rolling(bars, folds=4, is_bars=500, oos_bars=200, purge_bars=10)
    anchored = partition.anchored(bars, folds=4, oos_bars=200, min_is_bars=500, purge_bars=10)

    for izq, der in zip(rolling, anchored, strict=True):
        assert izq.out_of_sample == der.out_of_sample

    # Y lo que si debe diferir: rolling mantiene el tamano, anchored crece.
    assert len({f.in_sample.duration_ns for f in rolling}) == 1
    assert len({f.in_sample.duration_ns for f in anchored}) == 4


@pytest.mark.unit
def test_the_purge_separates_the_two_windows() -> None:
    """Sin purga, una feature con ventana calculada al principio del OOS usa
    datos del final del IS. El puente es invisible en los numeros."""
    folds = partition.rolling(_bars(), folds=3, is_bars=400, oos_bars=150, purge_bars=20)

    for fold in folds:
        separacion = int(fold.out_of_sample.start_ns) - int(fold.in_sample.end_ns)
        assert separacion == 20 * STEP
        assert fold.purge_ns == separacion


@pytest.mark.unit
def test_the_last_fold_covers_the_final_bar() -> None:
    """`timestamp[i]` es la APERTURA: usar la ultima apertura como final del
    rango dejaria la ultima barra fuera del fold."""
    bars = _bars(count=1000)
    folds = partition.rolling(bars, folds=1, is_bars=500, oos_bars=500)

    ultimo = folds[-1].out_of_sample
    assert int(ultimo.end_ns) > int(bars.timestamp[-1])
    assert len(bars.between(ultimo.start_ns, ultimo.end_ns)) == 500


@pytest.mark.unit
def test_a_series_too_short_is_rejected_with_the_numbers() -> None:
    with pytest.raises(InsufficientHistory) as error:
        partition.rolling(_bars(count=300), folds=10, is_bars=200, oos_bars=100)

    assert error.value.context["required"] > error.value.context["available"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "ids"),
    [
        ({"folds": 0, "is_bars": 10, "oos_bars": 10}, "sin folds"),
        ({"folds": 2, "is_bars": 0, "oos_bars": 10}, "sin in-sample"),
        ({"folds": 2, "is_bars": 10, "oos_bars": 0}, "sin out-of-sample"),
        ({"folds": 2, "is_bars": 10, "oos_bars": 10, "purge_bars": -1}, "purga negativa"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_incoherent_partitions_are_rejected(kwargs: dict[str, int], ids: str) -> None:
    with pytest.raises(InvariantViolation):
        partition.rolling(_bars(), **kwargs)


# ---------------------------------------------------------------------------
# El motor: no filtra
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_fitter_never_sees_out_of_sample_bars(tmp_path: Path) -> None:
    """El test que sostiene todo lo demas.

    Si el ajustador viera una sola barra del OOS, la puntuacion fuera de muestra
    dejaria de serlo y WFE, estabilidad y promocion serian ficcion.
    """
    bars = _bars()
    catalog, fingerprint = _catalog(tmp_path, bars)
    fitter = RecordingFitter()
    engine = WalkForwardEngine(catalog=catalog, fitter=fitter, objective=LengthObjective())

    run = engine.run(
        _spec(), fingerprint, folds=4, is_bars=500, oos_bars=200, purge_bars=10, seed=1
    )

    assert len(fitter.seen) == 4
    for (visto_inicio, visto_fin), outcome in zip(fitter.seen, run.outcomes, strict=True):
        oos = outcome.fold.out_of_sample
        assert visto_fin < int(oos.start_ns), "el ajustador vio barras del out-of-sample"
        assert visto_inicio >= int(outcome.fold.in_sample.start_ns)


@pytest.mark.integration
@pytest.mark.parametrize("purge", [0, 25], ids=["sin purga", "con purga"])
def test_scores_come_from_the_right_windows(tmp_path: Path, purge: int) -> None:
    """El motor corta las ventanas que declara el fold, ni una barra mas.

    Con purga, las barras purgadas no pueden acabar dentro del out-of-sample:
    serian precisamente las que tocan el final del in-sample, que es lo que la
    purga existe para apartar.
    """
    bars = _bars()
    catalog, fingerprint = _catalog(tmp_path, bars)
    engine = WalkForwardEngine(
        catalog=catalog, fitter=RecordingFitter(), objective=LengthObjective()
    )

    run = engine.run(
        _spec(), fingerprint, folds=3, is_bars=600, oos_bars=200, purge_bars=purge, seed=1
    )

    for outcome in run.outcomes:
        assert outcome.is_bars == 600
        assert outcome.oos_bars == 200, "la ventana medida no es la declarada"
        assert outcome.is_score == 600.0
        assert outcome.oos_score == 200.0

        primera_oos = bars.between(
            outcome.fold.out_of_sample.start_ns, outcome.fold.out_of_sample.end_ns
        ).timestamp[0]
        ultima_is = bars.between(
            outcome.fold.in_sample.start_ns, outcome.fold.in_sample.end_ns
        ).timestamp[-1]
        assert int(primera_oos) - int(ultima_is) == (purge + 1) * STEP


# ---------------------------------------------------------------------------
# Agregacion
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_efficiency_is_the_ratio_between_windows(tmp_path: Path) -> None:
    catalog, fingerprint = _catalog(tmp_path, _bars())
    engine = WalkForwardEngine(
        catalog=catalog,
        fitter=RecordingFitter(),
        objective=FixedObjective(is_value=10.0, oos_value=4.0, is_bars=500),
    )

    run = engine.run(_spec(), fingerprint, folds=3, is_bars=500, oos_bars=200, seed=1)

    assert run.metrics.is_return == pytest.approx(10.0)
    assert run.metrics.oos_return == pytest.approx(4.0)
    assert run.metrics.wfe == pytest.approx(0.4)
    assert run.metrics.stability == 1.0


@pytest.mark.integration
def test_a_non_positive_in_sample_leaves_the_efficiency_undefined(tmp_path: Path) -> None:
    """Un cociente contra un denominador no positivo no significa nada.

    Publicarlo como numero invitaria a rankear por el.
    """
    catalog, fingerprint = _catalog(tmp_path, _bars())
    engine = WalkForwardEngine(
        catalog=catalog,
        fitter=RecordingFitter(),
        objective=FixedObjective(is_value=-2.0, oos_value=1.0, is_bars=500),
    )

    run = engine.run(_spec(), fingerprint, folds=3, is_bars=500, oos_bars=200, seed=1)

    assert run.metrics.wfe is None


@pytest.mark.integration
def test_stability_counts_the_profitable_folds(tmp_path: Path) -> None:
    """Distingue una eficiencia sostenida de una que depende de un solo fold."""
    catalog, fingerprint = _catalog(tmp_path, _bars())
    engine = WalkForwardEngine(
        catalog=catalog,
        fitter=RecordingFitter(),
        objective=FixedObjective(is_value=5.0, oos_value=-1.0, is_bars=500),
    )

    run = engine.run(_spec(), fingerprint, folds=4, is_bars=500, oos_bars=200, seed=1)

    assert run.metrics.stability == 0.0
    assert all(o.degraded for o in run.outcomes)


# ---------------------------------------------------------------------------
# Contrato del motor
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_both_schemes_run(tmp_path: Path) -> None:
    catalog, fingerprint = _catalog(tmp_path, _bars())
    engine = WalkForwardEngine(
        catalog=catalog, fitter=RecordingFitter(), objective=LengthObjective()
    )

    for scheme in (ROLLING, ANCHORED):
        run = engine.run(
            _spec(), fingerprint, folds=3, is_bars=500, oos_bars=200, scheme=scheme, seed=1
        )
        assert run.scheme == scheme
        assert run.metrics.folds == 3


@pytest.mark.integration
def test_an_unknown_scheme_is_rejected(tmp_path: Path) -> None:
    catalog, fingerprint = _catalog(tmp_path, _bars())
    engine = WalkForwardEngine(
        catalog=catalog, fitter=RecordingFitter(), objective=LengthObjective()
    )

    with pytest.raises(InvariantViolation):
        engine.run(_spec(), fingerprint, folds=2, is_bars=500, oos_bars=200, scheme="magico")


@pytest.mark.integration
def test_only_candidates_are_evaluated(tmp_path: Path) -> None:
    catalog, fingerprint = _catalog(tmp_path, _bars())
    engine = WalkForwardEngine(
        catalog=catalog, fitter=RecordingFitter(), objective=LengthObjective()
    )
    base = _spec()
    promoted = StrategySpec(
        symbol=base.symbol,
        timeframe=base.timeframe,
        entries=base.entries,
        state=LifecycleState.PROMOTED,
    )

    with pytest.raises(InvariantViolation):
        engine.run(promoted, fingerprint, folds=2, is_bars=500, oos_bars=200)


@pytest.mark.integration
def test_the_engine_does_not_import_the_optimizer(tmp_path: Path) -> None:
    """La validacion es una metodologia, no un motor de busqueda.

    El ajustador entra por puerto; el motor no sabe quien lo implementa.
    """
    import inspect

    parameters = set(inspect.signature(WalkForwardEngine.__init__).parameters)

    assert parameters == {"self", "catalog", "fitter", "objective"}
    assert "optimizer" not in parameters


@pytest.mark.integration
def test_the_run_is_auditable_fold_by_fold(tmp_path: Path) -> None:
    catalog, fingerprint = _catalog(tmp_path, _bars())
    engine = WalkForwardEngine(
        catalog=catalog, fitter=RecordingFitter(), objective=LengthObjective()
    )

    volcado = engine.run(
        _spec(), fingerprint, folds=3, is_bars=500, oos_bars=200, purge_bars=5, seed=42
    ).to_dict()

    assert volcado["seed"] == 42
    assert volcado["dataset_fingerprint"] == fingerprint
    assert len(volcado["folds"]) == 3
    assert volcado["metrics"]["folds"] == 3
    assert volcado["folds"][0]["fold"]["purge_ns"] == 5 * STEP
