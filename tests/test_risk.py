"""Riesgo: dimensionamiento fraccional fijo y limites de exposicion.

El test que sostiene el fichero es `test_the_risk_budget_is_never_exceeded`: por
mucho que se muevan capital, stop, coste e instrumento, el dinero arriesgado
hasta el stop nunca supera el presupuesto. Es la unica propiedad por la que
existe este motor; las demas son la forma de conseguirla.

El segundo en importancia es `test_a_vanishing_stop_would_ask_for_absurd_size`,
que no comprueba que el limite existe sino que DEMUESTRA el dano que evita.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import InvariantViolation
from app.core.types import Direction, Severity, Symbol, TimestampNs
from app.core.validation import ValidationReport
from app.domain.entities.order import OrderType
from app.domain.entities.trade import Position
from app.domain.value_objects.instrument import CostModel, Instrument
from app.portfolio.limits import RiskLimits
from app.portfolio.policy import FixedFractionalRiskPolicy, SizingDecision
from app.shared.ports import RiskPolicyPort

AT = TimestampNs(1_700_000_000_000_000_000)
EQUITY = 10_000.0


def _eurusd(*, spread: float = 10.0, slippage: float = 5.0, margin: float = 1_000.0) -> Instrument:
    """EURUSD de 5 digitos, con las especificaciones que devuelve MT5."""
    return Instrument(
        symbol=Symbol("EURUSD"),
        point=0.00001,
        digits=5,
        contract_size=100_000.0,
        value_per_point_per_lot=1.0,
        min_lot=0.01,
        max_lot=100.0,
        lot_step=0.01,
        margin_per_lot=margin,
        costs=CostModel(spread_points=spread, slippage_points=slippage),
    )


def _policy(**overrides: float | int) -> FixedFractionalRiskPolicy:
    return FixedFractionalRiskPolicy(RiskLimits(**overrides))  # type: ignore[arg-type]


def _size(
    policy: FixedFractionalRiskPolicy,
    *,
    stop: float,
    equity: float = EQUITY,
    direction: int = int(Direction.LONG),
    positions: tuple[Position, ...] = (),
    instrument: Instrument | None = None,
) -> tuple[object, ValidationReport]:
    return policy.size_order(
        intent_direction=direction,
        instrument=instrument if instrument is not None else _eurusd(),
        equity=equity,
        stop_distance=stop,
        open_positions=positions,
        at_ns=AT,
    )


def _decide(
    policy: FixedFractionalRiskPolicy,
    *,
    stop: float,
    equity: float = EQUITY,
    positions: tuple[Position, ...] = (),
    instrument: Instrument | None = None,
) -> tuple[SizingDecision, ValidationReport]:
    report = ValidationReport(subject="test")
    decision = policy.decide(
        intent_direction=int(Direction.LONG),
        instrument=instrument if instrument is not None else _eurusd(),
        equity=equity,
        stop_distance=stop,
        open_positions=positions,
        report=report,
    )
    return decision, report


def _position(lots: float, symbol: str = "EURUSD") -> Position:
    return Position(
        symbol=Symbol(symbol),
        direction=Direction.LONG,
        lots=lots,
        entry_price=1.1000,
        entry_time=AT,
    )


# ---------------------------------------------------------------------------
# La propiedad que justifica el motor
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("equity", [500.0, 10_000.0, 250_000.0])
@pytest.mark.parametrize("stop_points", [60.0, 100.0, 350.0, 2_000.0])
@pytest.mark.parametrize("fraction", [0.001, 0.01, 0.05])
def test_the_risk_budget_is_never_exceeded(
    equity: float, stop_points: float, fraction: float
) -> None:
    """Barrido: el dinero en riesgo nunca supera el presupuesto.

    Se comprueba sobre el tamano FINAL -ya truncado a la rejilla- porque es el
    que se enviaria. Truncar hacia abajo solo puede dejarlo por debajo; si
    alguna vez quedara por encima, el redondeo estaria redondeando al alza.
    """
    instrument = _eurusd()
    decision, _ = _decide(
        _policy(risk_fraction=fraction),
        stop=instrument.points_to_price(stop_points),
        equity=equity,
    )

    if not decision.tradable:
        return  # No operar nunca viola un presupuesto.

    en_riesgo = decision.lots * stop_points * instrument.value_per_point_per_lot
    assert en_riesgo <= equity * fraction + 1e-9, "se arriesgo mas de lo presupuestado"


@pytest.mark.unit
def test_a_vanishing_stop_would_ask_for_absurd_size() -> None:
    """No comprueba que el limite existe: demuestra el dano que evita.

    Se calcula a mano lo que pediria el presupuesto con un stop de un punto y se
    contrasta con lo que la politica autoriza.
    """
    instrument = _eurusd()
    presupuesto = EQUITY * 0.01
    lotes_sin_limite = presupuesto / (1.0 * instrument.value_per_point_per_lot)

    assert lotes_sin_limite == 100.0, "un stop de un punto pediria 100 lotes"

    intent, report = _size(_policy(), stop=instrument.points_to_price(1.0))

    assert intent is None
    assert "STOP_TOO_TIGHT" in report.codes()


@pytest.mark.unit
def test_a_zero_stop_is_rejected_before_dividing() -> None:
    """Es el divisor del tamano: sin esta puerta el resultado seria infinito."""
    intent, report = _size(_policy(), stop=0.0)

    assert intent is None
    assert "NON_POSITIVE_STOP" in report.codes()


@pytest.mark.unit
@pytest.mark.parametrize("stop", [-0.001, -1.0])
def test_a_negative_stop_is_rejected(stop: float) -> None:
    """Un stop negativo daria lotes negativos, que pasarian todos los topes."""
    intent, report = _size(_policy(), stop=stop)

    assert intent is None
    assert "NON_POSITIVE_STOP" in report.codes()


# ---------------------------------------------------------------------------
# La aritmetica
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_size_is_the_budget_divided_by_the_stop() -> None:
    """10.000 al 1% son 100 de presupuesto; con 100 puntos a 1 por punto, 1 lote."""
    instrument = _eurusd()
    decision, report = _decide(_policy(), stop=instrument.points_to_price(100.0))

    assert decision.risk_money == pytest.approx(100.0)
    assert decision.stop_points == pytest.approx(100.0)
    assert decision.raw_lots == pytest.approx(1.0)
    assert decision.lots == pytest.approx(1.0)
    assert report.ok


@pytest.mark.unit
def test_halving_the_stop_doubles_the_size() -> None:
    instrument = _eurusd()
    ancho, _ = _decide(_policy(), stop=instrument.points_to_price(400.0))
    estrecho, _ = _decide(_policy(), stop=instrument.points_to_price(200.0))

    assert estrecho.raw_lots == pytest.approx(2.0 * ancho.raw_lots)


@pytest.mark.unit
def test_the_size_follows_current_equity_and_not_the_initial() -> None:
    """Baja solo tras las perdidas y sube tras las ganancias, sin intervencion."""
    instrument = _eurusd()
    stop = instrument.points_to_price(100.0)

    tras_perdidas, _ = _decide(_policy(), stop=stop, equity=5_000.0)
    tras_ganancias, _ = _decide(_policy(), stop=stop, equity=20_000.0)

    assert tras_perdidas.raw_lots == pytest.approx(0.5)
    assert tras_ganancias.raw_lots == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# La rejilla: nunca hacia arriba
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_grid_truncates_and_never_rounds_up() -> None:
    """Redondear al alza arriesgaria mas de lo presupuestado.

    Con 555 de capital al 1% y 100 puntos, el presupuesto pide 0.0555 lotes. La
    rejilla de 0.01 tiene que dejarlo en 0.05, no en 0.06.
    """
    instrument = _eurusd()
    decision, _ = _decide(_policy(), stop=instrument.points_to_price(100.0), equity=555.0)

    assert decision.raw_lots == pytest.approx(0.0555)
    assert decision.lots == pytest.approx(0.05)
    assert decision.lots < decision.raw_lots


@pytest.mark.unit
def test_a_budget_below_the_minimum_lot_does_not_trade() -> None:
    """Subir al lote minimo seria arriesgar mas de lo autorizado, asi que no."""
    instrument = _eurusd()
    intent, report = _size(_policy(), stop=instrument.points_to_price(100.0), equity=50.0)

    assert intent is None
    assert "BELOW_MINIMUM_LOT" in report.codes()
    assert not report.ok


# ---------------------------------------------------------------------------
# Topes: reducen, no rechazan
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_absolute_cap_shrinks_the_order_but_keeps_it() -> None:
    instrument = _eurusd(margin=0.0)
    intent, report = _size(
        _policy(max_lots_per_order=2.0, max_lots_per_symbol=20.0),
        stop=instrument.points_to_price(60.0),
        equity=1_000_000.0,
        instrument=instrument,
    )

    assert intent is not None, "un tope reduce, no bloquea"
    assert intent.lots == pytest.approx(2.0)  # type: ignore[attr-defined]
    assert "CAPPED_BY_MAX_LOTS" in report.codes()
    assert report.max_severity is Severity.WARNING


@pytest.mark.unit
def test_open_lots_in_the_same_symbol_consume_the_allowance() -> None:
    """Dos posiciones en el mismo simbolo no son dos riesgos independientes."""
    instrument = _eurusd(margin=0.0)
    limites = {"max_lots_per_order": 10.0, "max_lots_per_symbol": 10.0}

    libre, _ = _decide(
        _policy(**limites),
        stop=instrument.points_to_price(60.0),
        equity=1_000_000.0,
        instrument=instrument,
    )
    ocupado, report = _decide(
        _policy(**limites),
        stop=instrument.points_to_price(60.0),
        equity=1_000_000.0,
        positions=(_position(9.0),),
        instrument=instrument,
    )

    assert libre.lots == pytest.approx(10.0)
    assert ocupado.lots == pytest.approx(1.0)
    assert "CAPPED_BY_SYMBOL_EXPOSURE" in report.codes()


@pytest.mark.unit
def test_positions_in_other_symbols_do_not_consume_the_allowance() -> None:
    instrument = _eurusd(margin=0.0)
    limites = {"max_lots_per_order": 10.0, "max_lots_per_symbol": 10.0}

    decision, report = _decide(
        _policy(**limites),
        stop=instrument.points_to_price(60.0),
        equity=1_000_000.0,
        positions=(_position(9.0, symbol="GBPUSD"),),
        instrument=instrument,
    )

    assert decision.lots == pytest.approx(10.0)
    assert "CAPPED_BY_SYMBOL_EXPOSURE" not in report.codes()


@pytest.mark.unit
def test_margin_caps_the_size_when_it_binds() -> None:
    """Con 25% de 10.000 hay 2.500 de margen; a 5.000 por lote solo caben 0.5.

    El presupuesto de riesgo pedia 1.67 lotes, asi que aqui manda el margen.
    """
    instrument = _eurusd(margin=5_000.0)
    decision, report = _decide(
        _policy(), stop=instrument.points_to_price(60.0), instrument=instrument
    )

    assert decision.raw_lots == pytest.approx(100.0 / 60.0)
    assert decision.lots == pytest.approx(0.5)
    assert "CAPPED_BY_MARGIN" in report.codes()
    assert decision.margin_used <= EQUITY * 0.25 + 1e-9


@pytest.mark.unit
def test_margin_stays_out_of_the_way_when_it_does_not_bind() -> None:
    """A 1.000 por lote caben 2.5 y el presupuesto solo pide 1.67: no ata.

    Distinguirlo importa: sin este caso, el truncado de rejilla podria pasar por
    un recorte de margen y el informe culparia al limite equivocado.
    """
    instrument = _eurusd(margin=1_000.0)
    decision, report = _decide(
        _policy(), stop=instrument.points_to_price(60.0), instrument=instrument
    )

    assert decision.raw_lots == pytest.approx(100.0 / 60.0)
    assert decision.lots == pytest.approx(1.66)  # solo rejilla, no tope
    assert "CAPPED_BY_MARGIN" not in report.codes()
    assert decision.binding == ()


@pytest.mark.unit
def test_a_marginless_instrument_has_no_margin_ceiling() -> None:
    """Sin margen declarado el tope no aplica; inventarlo con divisor cero
    produciria una excepcion en lugar de una decision."""
    instrument = _eurusd(margin=0.0)
    decision, report = _decide(
        _policy(), stop=instrument.points_to_price(100.0), instrument=instrument
    )

    assert decision.tradable
    assert decision.margin_used == 0.0
    assert "CAPPED_BY_MARGIN" not in report.codes()


# ---------------------------------------------------------------------------
# Puertas: rechazan
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("equity", [0.0, -1.0, -50_000.0])
def test_non_positive_equity_is_rejected(equity: float) -> None:
    intent, report = _size(_policy(), stop=_eurusd().points_to_price(100.0), equity=equity)

    assert intent is None
    assert "NON_POSITIVE_EQUITY" in report.codes()


@pytest.mark.unit
def test_the_position_limit_blocks_a_new_order() -> None:
    intent, report = _size(
        _policy(max_open_positions=2),
        stop=_eurusd().points_to_price(100.0),
        positions=(_position(0.1), _position(0.1, symbol="GBPUSD")),
    )

    assert intent is None
    assert "MAX_POSITIONS_REACHED" in report.codes()


@pytest.mark.unit
@pytest.mark.parametrize("direction", [0, 2, -3])
def test_only_long_or_short_produce_an_intent(direction: int) -> None:
    intent, report = _size(_policy(), stop=_eurusd().points_to_price(100.0), direction=direction)

    assert intent is None
    assert "INVALID_DIRECTION" in report.codes()


@pytest.mark.unit
def test_the_minimum_stop_follows_the_cost_of_the_instrument() -> None:
    """Cien puntos son holgura en un instrumento barato y ruido en uno caro."""
    barato = _eurusd(spread=2.0, slippage=1.0)
    caro = _eurusd(spread=100.0, slippage=50.0)
    limites = RiskLimits()

    assert limites.min_stop_points_for(barato) == pytest.approx(12.0)
    assert limites.min_stop_points_for(caro) == pytest.approx(600.0)

    stop = barato.points_to_price(100.0)
    assert _size(_policy(), stop=stop, instrument=barato)[0] is not None
    assert _size(_policy(), stop=stop, instrument=caro)[0] is None


@pytest.mark.unit
def test_a_costless_instrument_still_has_a_floor() -> None:
    """Sin suelo absoluto, un modelo de coste sin rellenar anularia el limite."""
    sin_costes = _eurusd(spread=0.0, slippage=0.0)

    assert RiskLimits().min_stop_points_for(sin_costes) == pytest.approx(10.0)
    intent, report = _size(_policy(), stop=sin_costes.points_to_price(1.0), instrument=sin_costes)
    assert intent is None
    assert "STOP_TOO_TIGHT" in report.codes()


@pytest.mark.unit
def test_an_uncalibrated_cost_model_is_announced_and_not_silent() -> None:
    """Un `CostModel` sin rellenar debilita el limite mas importante del motor.

    Se midio contra el broker real: `configs/symbols/EURUSD.toml` declara spread
    cero mientras MT5 devolvia 73 puntos. Con cero, el stop minimo cae al suelo
    absoluto de 10 puntos; con el coste real serian 3 x 73 = 219. La degradacion
    es de un factor 20 y antes no dejaba rastro en ninguna parte.

    Sigue siendo WARNING y no ERROR: rechazar romperia todo backtest que use el
    `CostModel` por defecto. Lo que se exige es que quede constancia.
    """
    sin_calibrar = _eurusd(spread=0.0, slippage=0.0)
    calibrado = _eurusd(spread=73.0, slippage=0.0)

    _, aviso = _decide(_policy(), stop=sin_calibrar.points_to_price(50.0), instrument=sin_calibrar)
    _, limpio = _decide(_policy(), stop=calibrado.points_to_price(300.0), instrument=calibrado)

    assert "UNCALIBRATED_COST_MODEL" in aviso.codes()
    assert aviso.ok, "es un aviso, no un bloqueo"
    assert "UNCALIBRATED_COST_MODEL" not in limpio.codes()

    # La magnitud de la degradacion, medida.
    assert RiskLimits().min_stop_points_for(sin_calibrar) == pytest.approx(10.0)
    assert RiskLimits().min_stop_points_for(calibrado) == pytest.approx(219.0)


# ---------------------------------------------------------------------------
# Contrato
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_policy_satisfies_the_port() -> None:
    assert isinstance(FixedFractionalRiskPolicy(), RiskPolicyPort)


@pytest.mark.unit
@pytest.mark.parametrize("value_per_point", [0.0, -1.0])
def test_the_divisor_cannot_be_non_positive_by_construction(value_per_point: float) -> None:
    """`decide` divide entre `stop_points * value_per_point_per_lot` sin guarda.

    Puede permitirselo porque `Instrument` rechaza un valor por punto no
    positivo y la puerta del stop exige distancia positiva. Este test fija esa
    dependencia: si alguien relajara la invariante de `Instrument`, la division
    de `policy.decide` se quedaria sin proteccion y esto saltaria antes.
    """
    with pytest.raises(InvariantViolation):
        Instrument(
            symbol=Symbol("EURUSD"),
            point=0.00001,
            digits=5,
            contract_size=100_000.0,
            value_per_point_per_lot=value_per_point,
            min_lot=0.01,
            max_lot=100.0,
            lot_step=0.01,
            margin_per_lot=0.0,
            costs=CostModel(),
        )


@pytest.mark.unit
def test_the_intent_carries_what_execution_needs() -> None:
    intent, _ = _size(_policy(), stop=_eurusd().points_to_price(100.0))

    assert intent is not None
    assert intent.symbol == Symbol("EURUSD")  # type: ignore[attr-defined]
    assert intent.direction is Direction.LONG  # type: ignore[attr-defined]
    assert intent.order_type is OrderType.MARKET  # type: ignore[attr-defined]
    assert intent.decided_at == AT  # type: ignore[attr-defined]
    # En una orden a mercado el precio de referencia es el de llenado, que aun
    # no existe: el nivel absoluto lo fija ejecucion al conocerlo.
    assert intent.stop_loss is None  # type: ignore[attr-defined]


@pytest.mark.unit
def test_the_same_inputs_always_give_the_same_size() -> None:
    """Puro y determinista: sin reloj, sin azar, sin estado entre llamadas."""
    politica = _policy()
    stop = _eurusd().points_to_price(137.0)

    primero, _ = _decide(politica, stop=stop)
    for _ in range(5):
        siguiente, _ = _decide(politica, stop=stop)
        assert siguiente.to_dict() == primero.to_dict()


@pytest.mark.unit
def test_every_refusal_records_its_reason() -> None:
    """Un rechazo sin motivo obliga a repetir el trabajo para entenderlo."""
    casos = [
        {"stop": 0.0},
        {"stop": _eurusd().points_to_price(1.0)},
        {"stop": _eurusd().points_to_price(100.0), "equity": -1.0},
        {"stop": _eurusd().points_to_price(100.0), "equity": 50.0},
    ]
    for caso in casos:
        intent, report = _size(_policy(), **caso)  # type: ignore[arg-type]
        assert intent is None
        assert report.codes(), f"rechazo sin motivo con {caso}"
        assert report.max_severity is Severity.ERROR


@pytest.mark.unit
def test_the_decision_shows_whether_a_cap_or_the_budget_set_the_size() -> None:
    """Un tope que salta a menudo es politica mal calibrada; uno que no salta
    nunca es un cortafuegos sano. Sin `raw_lots` no se distinguen."""
    instrument = _eurusd(margin=0.0)
    por_presupuesto, _ = _decide(_policy(), stop=instrument.points_to_price(100.0))
    por_tope, _ = _decide(
        _policy(max_lots_per_order=0.5, max_lots_per_symbol=20.0),
        stop=instrument.points_to_price(100.0),
        instrument=instrument,
    )

    assert not por_presupuesto.reduced
    assert por_presupuesto.binding == ()
    assert por_tope.reduced
    assert "CAPPED_BY_MAX_LOTS" in por_tope.binding


# ---------------------------------------------------------------------------
# Limites mal formados
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    [
        {"risk_fraction": 0.0},
        {"risk_fraction": -0.01},
        {"risk_fraction": 1.5},
        {"absolute_min_stop_points": 0.0},
        {"max_lots_per_order": 0.0},
        {"max_open_positions": 0},
        {"max_margin_utilization": 0.0},
        {"max_margin_utilization": 1.5},
        {"max_lots_per_order": 10.0, "max_lots_per_symbol": 5.0},
    ],
    ids=lambda v: ",".join(f"{k}={x}" for k, x in v.items()) if isinstance(v, dict) else "",
)
def test_malformed_limits_are_rejected_at_construction(overrides: dict[str, float]) -> None:
    """No debe descubrirse cuando llega la primera orden, sino antes de que haya
    dinero en juego."""
    with pytest.raises(InvariantViolation):
        RiskLimits(**overrides)  # type: ignore[arg-type]


@pytest.mark.unit
def test_the_config_file_produces_valid_limits() -> None:
    """Lo declarado en `configs/risk.toml` tiene que construir de verdad."""
    import tomllib
    from pathlib import Path

    from app.portfolio.policy import limits_from_mapping

    raw = tomllib.loads(Path("configs/risk.toml").read_text(encoding="utf-8"))
    limits = limits_from_mapping(raw)

    assert limits.risk_fraction == 0.01
    assert limits.max_open_positions == 5
    assert FixedFractionalRiskPolicy(limits).limits == limits
