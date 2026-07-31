"""El contrato de resultado: que se mide, con que unidades y donde vive.

Valida ADR-0010. Cubre ademas un hueco anterior: `app/domain/` no tenia NINGUN
test propio pese a gobernar toda la cadena -de MT5 al zoo-, asi que sus
invariantes solo se ejercitaban de rebote desde `test_conventions.py`, que
comprueba forma y no comportamiento.

Se prueban INVARIANTES, no ejemplos. Un test que construye un objeto valido y
comprueba que sus campos son los que se le pasaron no verifica nada: verifica
que Python asigna atributos. Lo que importa es que el tipo RECHACE lo que
promete rechazar, porque de eso depende que "si existe, es correcto" sea una
propiedad y no una esperanza.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import InvariantViolation
from app.core.types import Direction, Symbol, Timeframe, TimestampNs
from app.domain.entities.trade import ExitReason, Trade
from app.domain.value_objects.instrument import CostModel
from app.domain.value_objects.metrics import MIN_TRADES_FOR_INFERENCE, PerformanceMetrics
from app.domain.value_objects.validation_metrics import (
    StatisticalTestResult,
    ValidationMetrics,
    WalkForwardMetrics,
)

# ---------------------------------------------------------------------------
# Utilidades de construccion
# ---------------------------------------------------------------------------


def _metrics(**overrides: object) -> PerformanceMetrics:
    """Metricas validas, con los campos que interese romper sobrescritos."""
    base: dict[str, object] = {
        "timeframe": Timeframe.M15,
        "bars": 1_000,
        "n_trades": 50,
        "net_profit": 1_000.0,
        "gross_profit": 1_500.0,
        "gross_loss": -400.0,
        "profit_factor": 3.75,
        "win_rate": 0.6,
        "expectancy": 20.0,
        "max_drawdown": 250.0,
        "max_drawdown_pct": 0.12,
        "cagr": 0.34,
        "sharpe": 1.4,
        "sortino": 2.1,
        "calmar": 2.83,
        "exposure": 0.35,
        "avg_bars_held": 12.0,
        "turnover": 180.0,
        "cost_ratio": 0.07,
    }
    base.update(overrides)
    return PerformanceMetrics(**base)  # type: ignore[arg-type]


def _trade(**overrides: object) -> Trade:
    base: dict[str, object] = {
        "symbol": Symbol("EURUSD"),
        "direction": Direction.LONG,
        "lots": 0.1,
        "entry_time": TimestampNs(1_000),
        "entry_price": 1.1000,
        "exit_time": TimestampNs(2_000),
        "exit_price": 1.1050,
        "exit_reason": ExitReason.TAKE_PROFIT,
        "gross_pnl": 50.0,
    }
    base.update(overrides)
    return Trade(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# A. El coste de financiacion entra en el neto
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_net_pnl_deducts_financing() -> None:
    """Sin esto, toda estrategia mantenida entre sesiones parece mejor de lo que es.

    Es el defecto que motivo ADR-0010: `metrics.py` declara que todo se mide
    sobre PnL neto "para que sea imposible presentar por descuido un resultado
    sin costes", y la financiacion quedaba fuera del neto. La omision era
    invisible porque no existe una metrica bruta que la delate.
    """
    sin_swap = _trade(commission=2.0, spread_cost=3.0, slippage_cost=1.0)
    con_swap = _trade(commission=2.0, spread_cost=3.0, slippage_cost=1.0, financing_cost=4.0)

    assert sin_swap.total_cost == pytest.approx(6.0)
    assert con_swap.total_cost == pytest.approx(10.0)
    assert con_swap.net_pnl == pytest.approx(sin_swap.net_pnl - 4.0)


@pytest.mark.unit
def test_favourable_carry_is_representable() -> None:
    """La financiacion es el unico coste con signo, y debe poder ser negativa.

    Un carry a favor es un hecho de mercado. Un modelo que solo admita coste
    positivo no puede representar el lado barato de la operacion, y el error
    seria conservador en la direccion equivocada: penalizaria estrategias que en
    realidad cobran por mantener la posicion.
    """
    favorable = _trade(gross_pnl=50.0, financing_cost=-3.0)

    assert favorable.total_cost == pytest.approx(-3.0)
    assert favorable.net_pnl == pytest.approx(53.0)


@pytest.mark.unit
def test_the_other_three_costs_still_reject_negatives() -> None:
    """Que la financiacion admita signo no relaja a las demas."""
    for campo in ("commission", "spread_cost", "slippage_cost"):
        with pytest.raises(InvariantViolation):
            _trade(**{campo: -1.0})


@pytest.mark.unit
def test_cost_model_admits_signed_financing_only() -> None:
    """`CostModel` deja pasar la financiacion negativa y sigue rechazando el resto."""
    CostModel(financing_per_lot_per_day=-0.5)

    for campo in ("commission_per_lot", "spread_points", "slippage_points", "slippage_atr_multiple"):
        with pytest.raises(InvariantViolation):
            CostModel(**{campo: -1.0})


# ---------------------------------------------------------------------------
# B. Las invariantes prometidas se aplican
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("gross_loss", 10.0),          # debe ser negativo o cero
        ("max_drawdown_pct", 1.5),     # es una fraccion
        ("max_drawdown_pct", -0.1),
        ("profit_factor", -1.0),       # no puede ser negativo
        ("cost_ratio", -0.5),
        ("win_rate", 1.5),
        ("exposure", -0.1),
        ("max_drawdown", -1.0),
        ("n_trades", -1),
        ("bars", -1),
    ],
    ids=lambda v: str(v),
)
def test_metrics_reject_what_the_contract_forbids(campo: str, valor: float) -> None:
    """Cada promesa del docstring tiene que ser una propiedad del tipo.

    Cuatro de estas no se comprobaban antes de ADR-0010: el objeto aceptaba un
    `gross_loss` positivo, un drawdown fuera de [0,1] y factores negativos, de
    modo que "si existe, es correcto" era media verdad.
    """
    with pytest.raises(InvariantViolation):
        _metrics(**{campo: valor})


@pytest.mark.unit
def test_cost_ratio_and_profit_factor_admit_none() -> None:
    """El mismo problema no puede recibir dos tratamientos en el mismo objeto.

    Ambos son cocientes cuyo denominador puede ser cero. `profit_factor` ya se
    tipaba `float | None` "para no propagar infinitos a los artefactos JSON ni a
    los rankings"; `cost_ratio` era `float` y compartia exactamente el problema.
    """
    metricas = _metrics(profit_factor=None, cost_ratio=None)

    assert metricas.profit_factor is None
    assert metricas.cost_ratio is None
    assert metricas.to_dict()["cost_ratio"] is None


# ---------------------------------------------------------------------------
# C. El resultado declara su periodo
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_metrics_cannot_be_built_without_declaring_their_period() -> None:
    """Anualizar exige saber cuantas barras hay en un ano.

    Sin `timeframe`, un Sharpe de M15 y otro de H1 son incomparables y nada en el
    objeto lo delata, mientras discovery los ordena en la misma lista.
    """
    completo = {
        f: getattr(_metrics(), f)
        for f in PerformanceMetrics.__dataclass_fields__
    }
    for obligatorio in ("timeframe", "bars"):
        incompleto = {k: v for k, v in completo.items() if k != obligatorio}
        with pytest.raises(TypeError):
            PerformanceMetrics(**incompleto)  # type: ignore[arg-type]


@pytest.mark.unit
def test_period_survives_serialisation() -> None:
    """El periodo tiene que llegar al artefacto, no quedarse en memoria."""
    volcado = _metrics().to_dict()

    assert volcado["timeframe"] == str(Timeframe.M15)
    assert volcado["bars"] == 1_000
    assert "cagr" in volcado


@pytest.mark.unit
def test_empty_requires_a_timeframe_and_is_degenerate() -> None:
    """El vacio tambien pertenece a un marco temporal.

    Permitir construirlo sin declararlo reabriria por la puerta de atras la
    incomparabilidad que el resto del contrato evita.
    """
    vacio = PerformanceMetrics.empty(Timeframe.H1)

    assert vacio.timeframe is Timeframe.H1
    assert vacio.n_trades == 0
    assert vacio.is_degenerate

    with pytest.raises(TypeError):
        PerformanceMetrics.empty()  # type: ignore[call-arg]


@pytest.mark.unit
def test_degeneracy_threshold_is_the_documented_one() -> None:
    """Justo por debajo del umbral es degenerado; justo encima, no."""
    assert _metrics(n_trades=MIN_TRADES_FOR_INFERENCE - 1).is_degenerate
    assert not _metrics(n_trades=MIN_TRADES_FOR_INFERENCE).is_degenerate
    # La exposicion nula tambien degenera, aunque haya operaciones suficientes.
    assert _metrics(n_trades=100, exposure=0.0).is_degenerate


# ---------------------------------------------------------------------------
# D. La separacion entre rendimiento y evidencia
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_walk_forward_efficiency_is_not_a_performance_field() -> None:
    """Asercion sobre lo que el contrato NO contiene.

    Existe para que la decision C de ADR-0010 no se erosione anadiendo el campo
    "porque hacia falta en un sitio". WFE es un cociente entre dos evaluaciones:
    un backtest simple no conoce la particion IS/OOS y tendria que declarar
    `wfe=None` por construccion, y un `None` estructural ensena a los
    consumidores a ignorar el campo.
    """
    campos = set(PerformanceMetrics.__dataclass_fields__)

    assert "wfe" not in campos
    assert "walk_forward_efficiency" not in campos
    assert not campos & {"is_return", "oos_return", "folds", "stability"}


@pytest.mark.unit
def test_walk_forward_metrics_carry_no_promotion_decision() -> None:
    """Quien mide no decide.

    La promocion la emite `PromotionPolicyPort`. Guardar su veredicto en el
    resultado del walk-forward haria que el motor que mide cargue la decision de
    quien decide, que es la misma frontera que `architecture.toml` protege al
    mantener `promotion` fuera del `depends` de `discovery`.
    """
    campos = set(WalkForwardMetrics.__dataclass_fields__)

    assert not campos & {"promotion_decision", "promoted", "approved", "verdict"}


@pytest.mark.unit
def test_wfe_requires_positive_in_sample_return() -> None:
    """Un cociente contra un denominador no positivo no significa nada."""
    WalkForwardMetrics(folds=6, is_return=0.20, oos_return=0.14, wfe=0.7, stability=0.83)

    with pytest.raises(InvariantViolation):
        WalkForwardMetrics(folds=6, is_return=0.0, oos_return=0.14, wfe=99.0, stability=0.5)

    # Sin rendimiento dentro de muestra, `wfe` debe ser None, no un numero.
    WalkForwardMetrics(folds=6, is_return=-0.05, oos_return=0.14, wfe=None, stability=0.5)


@pytest.mark.unit
@pytest.mark.parametrize("stability", [-0.1, 1.1])
def test_stability_is_a_fraction(stability: float) -> None:
    with pytest.raises(InvariantViolation):
        WalkForwardMetrics(folds=4, is_return=0.1, oos_return=0.05, wfe=0.5, stability=stability)


# ---------------------------------------------------------------------------
# E. Las pruebas estadisticas son extensibles, no fijas
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_statistical_test_result_rejects_impossible_p_values() -> None:
    StatisticalTestResult(name="reality_check", statistic=2.1, p_value=0.03, passed=True)

    for p in (-0.01, 1.01):
        with pytest.raises(InvariantViolation):
            StatisticalTestResult(name="spa", statistic=1.0, p_value=p, passed=False)


@pytest.mark.unit
def test_validation_metrics_accept_tests_they_do_not_know() -> None:
    """Anadir una prueba nueva no debe obligar a tocar este tipo.

    `StatisticalTestPort` existe para que "la capa de promocion pueda componer
    pruebas heterogeneas sin conocerlas". Campos fijos por prueba -pbo,
    reality_check, spa- contradirian ese diseno.
    """
    evidencia = ValidationMetrics(
        tests={
            "pbo": StatisticalTestResult(name="pbo", statistic=0.31, p_value=0.31, passed=True),
            "una_prueba_futura": StatisticalTestResult(
                name="una_prueba_futura", statistic=0.0, p_value=0.5, passed=False
            ),
        }
    )

    assert set(evidencia.tests) == {"pbo", "una_prueba_futura"}
    assert evidencia.is_evaluated


@pytest.mark.unit
def test_test_keys_must_match_their_names() -> None:
    """Una clave que no coincide con el nombre produce trazas irrastreables."""
    with pytest.raises(InvariantViolation):
        ValidationMetrics(
            tests={"pbo": StatisticalTestResult(name="spa", statistic=1.0, p_value=0.2, passed=True)}
        )


@pytest.mark.unit
def test_evidence_is_immutable_after_construction() -> None:
    """Nadie anade una prueba despues de emitida la evidencia."""
    original = {
        "pbo": StatisticalTestResult(name="pbo", statistic=0.3, p_value=0.3, passed=True)
    }
    evidencia = ValidationMetrics(tests=original)

    original["colada"] = StatisticalTestResult(
        name="colada", statistic=0.0, p_value=0.0, passed=True
    )

    assert set(evidencia.tests) == {"pbo"}
    with pytest.raises(TypeError):
        evidencia.tests["otra"] = original["colada"]  # type: ignore[index]


@pytest.mark.unit
def test_unevaluated_candidate_is_distinguishable_from_a_bad_one() -> None:
    """Sin evidencia no es lo mismo que con evidencia mala.

    La primera pide mas datos; la segunda se descarta. Confundirlas hace que un
    candidato sin evaluar se rechace como si hubiera fallado.
    """
    assert not ValidationMetrics().is_evaluated
    assert ValidationMetrics(
        walk_forward=WalkForwardMetrics(
            folds=5, is_return=0.2, oos_return=0.1, wfe=0.5, stability=0.6
        )
    ).is_evaluated


# ---------------------------------------------------------------------------
# F. El resultado tecnico de una escritura (ADR-0011)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_write_result_carries_only_technical_facts() -> None:
    """Cuatro campos, y la ausencia del resto es el contrato.

    Si llevara proveedor, version o fecha, el escritor tendria que recibirlos y
    un adaptador de infraestructura conoceria reglas de negocio.
    """
    from app.domain.value_objects.dataset import WriteResult

    campos = set(WriteResult.__dataclass_fields__)

    assert campos == {"content_hash", "bytes_written", "bar_count", "physical_location"}
    assert not campos & {"provider", "version", "created_at", "dataset_id", "lineage"}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("content_hash", "   "),
        ("physical_location", ""),
        ("bytes_written", -1),
        ("bar_count", -1),
    ],
    ids=lambda v: str(v),
)
def test_write_result_rejects_unauditable_writes(campo: str, valor: object) -> None:
    """Una escritura sin huella no es auditable y sin ubicacion no es localizable."""
    from app.core.types import ContentHash
    from app.domain.value_objects.dataset import WriteResult

    base: dict[str, object] = {
        "content_hash": ContentHash("abc123"),
        "bytes_written": 100,
        "bar_count": 10,
        "physical_location": "/ruta/x.parquet",
    }
    base[campo] = valor

    with pytest.raises(InvariantViolation):
        WriteResult(**base)  # type: ignore[arg-type]
