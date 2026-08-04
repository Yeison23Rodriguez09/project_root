"""Validation: contraste estadistico sobre la evidencia de folds.

Dos tests sostienen el fichero:

`test_a_consistent_loser_never_passes` -- el contraste es unilateral. Uno
bilateral sellaria como significativa a una estrategia que pierde en todos los
folds, porque lo es: pierde de forma muy consistente.

`test_the_engine_consumes_a_real_walk_forward_run` -- el resultado real de
walk-forward satisface el puerto sin que ninguno de los dos modulos importe al
otro. Si eso deja de cumplirse, la separacion declarada en `architecture.toml`
se convierte en una promesa que solo se verifica en produccion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.core.exceptions import InvariantViolation
from app.core.types import LifecycleState, Severity, Symbol, Timeframe
from app.domain.entities.bars import Bars
from app.domain.value_objects.strategy_spec import BlockSpec, StrategySpec
from app.domain.value_objects.validation_metrics import StatisticalTestResult
from app.shared.ports import FoldTestPort, WalkForwardEvidencePort
from app.validation.engine import ValidationEngine, ValidationOutcome, Verdict
from app.validation.statistical import (
    SignFlipPermutationTest,
    SignTest,
    minimum_folds_for,
)

pytest.importorskip("pyarrow", reason="El flujo completo se apoya en artefactos Parquet")

STEP = Timeframe.M15.nanoseconds


def _spec(state: LifecycleState = LifecycleState.CANDIDATE) -> StrategySpec:
    return StrategySpec(
        symbol=Symbol("EURUSD"),
        timeframe=Timeframe.M15,
        entries=(BlockSpec(name="ema", params={"period": 14}),),  # type: ignore[arg-type]
        state=state,
    )


class FakeFold:
    def __init__(self, is_score: float, oos_score: float) -> None:
        self.is_score = is_score
        self.oos_score = oos_score


class FakeEvidence:
    """Evidencia sintetica: cumple el puerto sin pasar por walk-forward."""

    def __init__(
        self,
        oos: list[float],
        is_scores: list[float] | None = None,
        *,
        spec: StrategySpec | None = None,
        seed: int = 7,
    ) -> None:
        source = is_scores if is_scores is not None else [abs(v) for v in oos]
        self.spec = spec if spec is not None else _spec()
        self.outcomes = [FakeFold(i, o) for i, o in zip(source, oos, strict=True)]
        self.dataset_fingerprint = "deadbeef"
        self.seed = seed


def _battery() -> list[FoldTestPort]:
    return [SignTest(), SignFlipPermutationTest()]


def _engine(alpha: float = 0.05) -> ValidationEngine:
    return ValidationEngine(tests=_battery(), alpha=alpha)


# ---------------------------------------------------------------------------
# La direccion del contraste
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "scores",
    [[-1.0] * 6, [-9.0] * 6, [-0.5] * 8, [-3.0, -1.0, -2.0, -5.0, -1.0, -4.0]],
    ids=["pierde poco", "pierde mucho", "ocho folds perdiendo", "pierde irregular"],
)
def test_a_consistent_loser_never_passes(scores: list[float]) -> None:
    """El contraste es unilateral, y aqui se ve por que.

    Con contraste bilateral estas series darian p = 0.031 o menos y quedarian
    selladas como significativas. Lo serian -pierden de forma muy consistente-
    pero "distinto de cero" y "validado" solo coinciden si se mira en una
    direccion.
    """
    outcome = _engine().validate(FakeEvidence(scores))

    assert outcome.verdict is Verdict.REJECTED
    assert not outcome.passed
    for result in outcome.metrics.tests.values():
        assert not result.passed
        assert result.p_value > 0.5, "un perdedor consistente debe dar p alto, no bajo"


@pytest.mark.unit
def test_a_consistent_winner_passes() -> None:
    outcome = _engine().validate(FakeEvidence([1.0, 2.0, 1.5, 3.0, 2.5, 1.0]))

    assert outcome.verdict is Verdict.PASSED
    assert outcome.passed
    assert all(r.passed for r in outcome.metrics.tests.values())


# ---------------------------------------------------------------------------
# Exactitud de las pruebas
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(("folds", "expected"), [(5, 1 / 32), (6, 1 / 64), (7, 1 / 128)])
def test_the_sign_test_is_exact(folds: int, expected: float) -> None:
    """Suma de coeficientes binomiales, no aproximacion normal.

    Con estos tamanos de muestra una aproximacion normal se equivoca en el
    primer decimal, que es justo donde esta el umbral.
    """
    scores = np.ones(folds)
    result = SignTest().run(is_scores=scores, oos_scores=scores, alpha=0.05, seed=0)

    assert result.p_value == pytest.approx(expected, rel=1e-12)
    assert result.statistic == float(folds)


@pytest.mark.unit
def test_the_permutation_enumerates_instead_of_sampling() -> None:
    """Con pocos folds el p-valor es exacto: sin muestreo y sin semilla."""
    test = SignFlipPermutationTest(exact_max_folds=16)
    scores = np.ones(6)

    assert test.is_exact_for(6) is True
    primero = test.run(is_scores=scores, oos_scores=scores, alpha=0.05, seed=1)
    segundo = test.run(is_scores=scores, oos_scores=scores, alpha=0.05, seed=999)

    assert primero.p_value == segundo.p_value, "la via exacta no puede depender de la semilla"
    assert primero.p_value == pytest.approx(1 / 64, rel=1e-12)


@pytest.mark.unit
def test_monte_carlo_approximates_the_exact_answer() -> None:
    """La via muestreada tiene que dar lo mismo que la exacta, no algo parecido.

    Se compara contra el enumerado completo de los mismos datos. Es la unica
    comprobacion que distingue "hay un muestreo" de "el muestreo estima el
    p-valor correcto": un error de signo o un vector de signos mal construido
    seguiria produciendo numeros en [0,1] con aspecto razonable.
    """
    scores = np.array([3.0, -1.0, 2.0, -1.0, 1.0, -2.0, 4.0, 1.0, -1.0, 2.0, 1.0, -1.0])
    kwargs: dict[str, Any] = {"is_scores": scores, "oos_scores": scores, "alpha": 0.05}

    exacto = SignFlipPermutationTest(exact_max_folds=16).run(**kwargs, seed=3)
    muestreado = SignFlipPermutationTest(exact_max_folds=8, monte_carlo_samples=20_000).run(
        **kwargs, seed=3
    )

    # 0.02 son unas nueve veces el error estandar con 20.000 muestras.
    assert muestreado.p_value == pytest.approx(exacto.p_value, abs=0.02)
    assert muestreado.statistic == pytest.approx(exacto.statistic)


@pytest.mark.unit
def test_a_monte_carlo_p_value_is_never_zero() -> None:
    """Un cero afirmaria una precision que el muestreo no tiene."""
    test = SignFlipPermutationTest(exact_max_folds=8, monte_carlo_samples=5000)

    result = test.run(is_scores=np.ones(20), oos_scores=np.ones(20), alpha=0.05, seed=3)

    assert test.is_exact_for(20) is False
    assert result.p_value > 0.0
    assert result.p_value >= 1 / 5001


@pytest.mark.unit
def test_monte_carlo_is_reproducible_given_the_seed() -> None:
    scores = np.array([1.0, -2.0, 3.0, 0.5, -1.0, 2.0, 1.0, -0.5, 4.0, 1.0, 2.0, -1.0])
    kwargs: dict[str, Any] = {"is_scores": scores, "oos_scores": scores, "alpha": 0.05}

    izquierda = SignFlipPermutationTest(exact_max_folds=4).run(**kwargs, seed=42)
    derecha = SignFlipPermutationTest(exact_max_folds=4).run(**kwargs, seed=42)
    distinta = SignFlipPermutationTest(exact_max_folds=4).run(**kwargs, seed=43)

    assert izquierda.p_value == derecha.p_value
    assert izquierda.p_value != distinta.p_value, "semillas distintas deben explorar distinto"


@pytest.mark.unit
def test_the_permutation_never_goes_below_the_attainable_minimum() -> None:
    """El p-valor exacto no puede bajar de 1/2^n: el vector observado esta
    dentro de la enumeracion y siempre se cuenta a si mismo."""
    for folds in range(2, 13):
        scores = np.full(folds, 3.0)
        result = SignFlipPermutationTest().run(
            is_scores=scores, oos_scores=scores, alpha=0.05, seed=0
        )
        assert result.p_value >= 1 / 2**folds - 1e-15


@pytest.mark.unit
def test_zero_folds_do_not_count_against_the_strategy() -> None:
    """Un cero no aporta evidencia direccional; contarlo como fracaso sesgaria
    la prueba de signos contra la estrategia."""
    con_ceros = SignTest().run(
        is_scores=np.ones(8),
        oos_scores=np.array([1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0]),
        alpha=0.05,
        seed=0,
    )
    sin_ceros = SignTest().run(is_scores=np.ones(5), oos_scores=np.ones(5), alpha=0.05, seed=0)

    assert con_ceros.p_value == pytest.approx(sin_ceros.p_value)


@pytest.mark.unit
def test_the_two_tests_disagree_when_magnitude_matters() -> None:
    """Justifican existir por separado: la de signos es ciega a la magnitud.

    Un unico fold enorme entre perdedores mueve la media pero no los signos.
    """
    scores = np.array([10.0, -1.0, -1.0, -1.0, -1.0, -1.0])

    signos = SignTest().run(is_scores=scores, oos_scores=scores, alpha=0.05, seed=0)
    permutacion = SignFlipPermutationTest().run(
        is_scores=scores, oos_scores=scores, alpha=0.05, seed=0
    )

    assert signos.p_value > permutacion.p_value
    assert not signos.passed and not permutacion.passed


# ---------------------------------------------------------------------------
# Potencia: el veredicto tiene tres valores
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(("alpha", "expected"), [(0.10, 4), (0.05, 5), (0.01, 7), (0.001, 10)])
def test_the_minimum_folds_are_derived_from_alpha(alpha: float, expected: int) -> None:
    """El minimo no se declara: 1/2^n <= alpha lo fija.

    Declararlo aparte permitiria que los dos numeros se desincronizaran y que
    una bateria rechazase todo pareciendo rigurosa.
    """
    assert minimum_folds_for(alpha) == expected
    assert 1 / 2**expected <= alpha
    assert 1 / 2 ** (expected - 1) > alpha


@pytest.mark.unit
def test_too_few_folds_are_inconclusive_and_not_rejected() -> None:
    """Rechazar aqui hablaria del montaje y no de la estrategia.

    Con 4 folds y alpha=0.05 el p-valor minimo alcanzable es 0.0625: ninguna
    estrategia puede pasar, por buena que sea.
    """
    outcome = _engine().validate(FakeEvidence([1.0, 2.0, 3.0, 4.0]))

    assert outcome.verdict is Verdict.INCONCLUSIVE
    assert not outcome.passed, "un no concluyente no es un aprobado"
    assert "INSUFFICIENT_POWER" in outcome.report.codes()


@pytest.mark.unit
def test_the_power_warning_carries_the_numbers_to_act_on() -> None:
    outcome = _engine().validate(FakeEvidence([1.0, 2.0, 3.0]))
    issue = next(i for i in outcome.report if i.code == "INSUFFICIENT_POWER")

    assert issue.severity is Severity.WARNING, (
        "es un defecto del montaje, no un error del candidato"
    )
    assert issue.context["folds"] == 3
    assert issue.context["minimum_folds"] == 5


@pytest.mark.unit
def test_the_report_never_contradicts_the_verdict() -> None:
    """`report.ok` y `verdict` tienen que contar la misma historia.

    Con potencia insuficiente el fallo de una prueba no es un rechazo sino un
    dato no interpretable. Anotarlo como ERROR dejaria `report.ok` en False
    mientras el veredicto dice `INCONCLUSIVE`, y quien leyera el informe en vez
    del veredicto veria un rechazo donde no lo hay: el mismo colapso que el
    tercer valor existe para impedir.
    """
    sin_potencia = _engine().validate(FakeEvidence([-1.0, -2.0, -3.0, -4.0]))
    rechazado = _engine().validate(FakeEvidence([-1.0] * 6))

    assert sin_potencia.verdict is Verdict.INCONCLUSIVE
    assert sin_potencia.report.ok, "un no concluyente no puede llevar ERROR dentro"
    assert sin_potencia.report.max_severity is Severity.WARNING

    assert rechazado.verdict is Verdict.REJECTED
    assert not rechazado.report.ok, "un rechazo si tiene que sostenerse en un ERROR"


@pytest.mark.unit
def test_every_test_runs_even_after_one_fails() -> None:
    """Cortar al primer fallo perderia los p-valores que explican el rechazo."""
    outcome = _engine().validate(FakeEvidence([-1.0] * 6))

    assert len(outcome.metrics.tests) == 2
    assert {i.context["test"] for i in outcome.report if i.code == "TEST_NOT_SIGNIFICANT"} == {
        "sign_test",
        "sign_permutation",
    }


@pytest.mark.unit
def test_a_stricter_alpha_demands_more_folds() -> None:
    scores = [1.0, 2.0, 1.5, 3.0, 2.5, 1.0]

    assert _engine(alpha=0.05).validate(FakeEvidence(scores)).verdict is Verdict.PASSED
    assert _engine(alpha=0.01).validate(FakeEvidence(scores)).verdict is Verdict.INCONCLUSIVE


# ---------------------------------------------------------------------------
# Contrato del motor
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_tests_must_pass() -> None:
    """Conjuncion y no disyuncion: por eso no hace falta corregir por
    multiplicidad. Exigir que todas pasen hace el rechazo de la nula mas
    dificil con cada prueba anadida, no mas facil."""

    class AlwaysFails:
        @property
        def name(self) -> str:
            return "siempre_falla"

        def run(self, **_: Any) -> StatisticalTestResult:
            return StatisticalTestResult(name=self.name, statistic=0.0, p_value=1.0, passed=False)

    engine = ValidationEngine(tests=[SignTest(), AlwaysFails()], alpha=0.05)
    outcome = engine.validate(FakeEvidence([1.0, 2.0, 1.5, 3.0, 2.5, 1.0]))

    assert outcome.verdict is Verdict.REJECTED
    assert outcome.metrics.tests["sign_test"].passed
    assert not outcome.metrics.tests["siempre_falla"].passed


@pytest.mark.unit
def test_an_empty_battery_is_rejected() -> None:
    with pytest.raises(InvariantViolation):
        ValidationEngine(tests=[], alpha=0.05)


@pytest.mark.unit
def test_two_tests_cannot_share_a_name() -> None:
    """`ValidationMetrics.tests` indexa por nombre: una de las dos desapareceria
    del informe sin aviso."""
    with pytest.raises(InvariantViolation):
        ValidationEngine(tests=[SignTest(), SignTest()], alpha=0.05)


@pytest.mark.unit
def test_only_candidates_are_contrasted() -> None:
    evidence = FakeEvidence([1.0] * 6, spec=_spec(LifecycleState.VALIDATED))

    with pytest.raises(InvariantViolation):
        _engine().validate(evidence)


@pytest.mark.unit
def test_evidence_without_folds_is_rejected() -> None:
    with pytest.raises(InvariantViolation):
        _engine().validate(FakeEvidence([]))


@pytest.mark.unit
def test_every_rejection_records_its_reason() -> None:
    outcome = _engine().validate(FakeEvidence([-1.0] * 6))

    assert "TEST_NOT_SIGNIFICANT" in outcome.report.codes()
    fallos = [i for i in outcome.report if i.code == "TEST_NOT_SIGNIFICANT"]
    assert {i.context["test"] for i in fallos} == {"sign_test", "sign_permutation"}
    for issue in fallos:
        assert "p_value" in issue.context


# ---------------------------------------------------------------------------
# Sobreajuste: descriptivo, no contraste
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_overfitting_score_is_the_edge_that_did_not_survive() -> None:
    """`clip(1 - wfe, 0, 1)`. Con IS=10 y OOS=4 sobrevivio el 40%."""
    outcome = _engine().validate(FakeEvidence([4.0] * 6, [10.0] * 6))

    assert outcome.metrics.walk_forward is not None
    assert outcome.metrics.walk_forward.wfe == pytest.approx(0.4)
    assert outcome.metrics.overfitting_score == pytest.approx(0.6)


@pytest.mark.unit
def test_the_overfitting_score_stays_undefined_without_an_in_sample_edge() -> None:
    """Sin ventaja dentro de muestra no hay nada que pudiera sobrevivir."""
    outcome = _engine().validate(FakeEvidence([1.0] * 6, [-2.0] * 6))

    assert outcome.metrics.walk_forward is not None
    assert outcome.metrics.walk_forward.wfe is None
    assert outcome.metrics.overfitting_score is None


@pytest.mark.unit
def test_the_overfitting_score_does_not_decide() -> None:
    """Es descriptiva: no lleva p-valor y no participa en el veredicto."""
    catastrofico = _engine().validate(FakeEvidence([0.1] * 6, [100.0] * 6))

    assert catastrofico.metrics.overfitting_score > 0.99, "perdio casi toda la ventaja"
    assert catastrofico.verdict is Verdict.PASSED, "el veredicto sale de las pruebas, no del ratio"


# ---------------------------------------------------------------------------
# Integracion real con walk-forward
# ---------------------------------------------------------------------------


def _real_run(tmp_path: Path, count: int = 4000) -> Any:
    """Corre walk-forward de verdad y devuelve su resultado sin adaptarlo."""
    from app.research.data.catalog import ParquetDatasetCatalog
    from app.research.data.layout import DatasetLayout
    from app.research.data.parquet_writer import ParquetMarketDataWriter
    from app.walkforward.engine import WalkForwardEngine

    ts = np.arange(0, count * STEP, STEP, dtype=np.int64)
    close = np.linspace(1.10, 1.20, count)
    bars = Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=ts,
        open=close - 0.00005,
        high=close + 0.00020,
        low=close - 0.00020,
        close=close,
        volume=np.full(count, 100.0),
    )
    layout = DatasetLayout(root=tmp_path)
    ParquetMarketDataWriter(layout).write(bars, overwrite=True)
    catalog = ParquetDatasetCatalog(layout)
    fingerprint = catalog.register(bars, lineage={"provider": "FILE"})

    class Fitter:
        def fit(self, spec: StrategySpec, bars: Bars, *, seed: int) -> StrategySpec:
            return spec

    class Objective:
        def score(self, spec: StrategySpec, bars: Bars) -> float:
            return float(np.mean(np.diff(np.asarray(bars.close))) * 1e4)

    engine = WalkForwardEngine(catalog=catalog, fitter=Fitter(), objective=Objective())
    return engine.run(
        _spec(), fingerprint, folds=6, is_bars=1000, oos_bars=400, purge_bars=20, seed=11
    )


@pytest.mark.integration
def test_the_engine_consumes_a_real_walk_forward_run(tmp_path: Path) -> None:
    """El resultado real de walk-forward cumple el puerto sin conocerlo.

    `architecture.toml` no deja que `validation` importe `walkforward`: son
    capacidades hermanas. Que el acoplamiento sea estructural es lo que hace
    verdadera esa separacion en vez de una promesa.
    """
    run = _real_run(tmp_path)

    assert isinstance(run, WalkForwardEvidencePort)
    outcome = _engine().validate(run)

    assert isinstance(outcome, ValidationOutcome)
    assert outcome.folds == 6
    assert outcome.dataset_fingerprint == run.dataset_fingerprint
    assert outcome.seed == run.seed
    assert set(outcome.metrics.tests) == {"sign_test", "sign_permutation"}


@pytest.mark.integration
def test_neither_module_imports_the_other(tmp_path: Path) -> None:
    """La separacion se comprueba sobre el fuente, no sobre la intencion."""
    validation = Path("app/validation")
    walkforward = Path("app/walkforward")

    for module in validation.glob("*.py"):
        assert "app.walkforward" not in module.read_text(encoding="utf-8")
    for module in walkforward.glob("*.py"):
        assert "app.validation" not in module.read_text(encoding="utf-8")


@pytest.mark.integration
def test_the_scores_are_recomputed_and_not_taken_on_trust(tmp_path: Path) -> None:
    """El contraste no depende de como agregase quien produjo los folds.

    Se compara contra la agregacion propia de walk-forward: deben coincidir
    porque el calculo es el mismo, pero validation lo obtiene de los folds y no
    del objeto agregado, que es lo que la mantiene comparable entre productores.
    """
    run = _real_run(tmp_path)
    outcome = _engine().validate(run)

    assert outcome.metrics.walk_forward is not None
    assert outcome.metrics.walk_forward.folds == run.metrics.folds
    assert outcome.metrics.walk_forward.oos_return == pytest.approx(run.metrics.oos_return)
    assert outcome.metrics.walk_forward.stability == pytest.approx(run.metrics.stability)


@pytest.mark.integration
def test_the_whole_contrast_is_reproducible(tmp_path: Path) -> None:
    run = _real_run(tmp_path)

    primero = _engine().validate(run).to_dict()
    segundo = _engine().validate(run).to_dict()

    assert primero == segundo


@pytest.mark.integration
def test_the_outcome_is_auditable(tmp_path: Path) -> None:
    """Seis meses despues hay que poder reconstruir con que se decidio."""
    volcado = _engine().validate(_real_run(tmp_path)).to_dict()

    assert volcado["alpha"] == 0.05
    assert volcado["minimum_folds"] == 5
    assert volcado["folds"] == 6
    assert volcado["verdict"] in {"passed", "rejected", "inconclusive"}
    assert set(volcado["metrics"]["tests"]) == {"sign_test", "sign_permutation"}
    for prueba in volcado["metrics"]["tests"].values():
        assert {"name", "statistic", "p_value", "passed"} <= set(prueba)
