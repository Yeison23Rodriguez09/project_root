"""El motor de medida: que numero sale de que hecho, y que se niega a inventar.

Se prueban PROPIEDADES, no ejemplos. Comprobar que `expectancy` vale 20 sobre
una lista fabricada verifica una division; lo que importa es que la metrica
signifique lo que su nombre dice en los casos donde es facil que no lo
signifique -una cuenta arruinada, una serie sin variacion, una curva monotona,
cero operaciones- porque ahi es donde un motor de metricas produce numeros
plausibles y falsos.

La frontera que este fichero tambien fija: analytics NO influye en lo que mide.
Recibe operaciones y curva, y no puede tocarlas.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.analytics import performance_metrics
from app.core.exceptions import InvariantViolation
from app.core.types import Direction, Symbol, Timeframe, TimestampNs
from app.domain.entities.trade import ExitReason, Trade
from app.domain.value_objects.metrics import MIN_TRADES_FOR_INFERENCE, PerformanceMetrics

NS_PER_BAR = 15 * 60 * 1_000_000_000


def _trade(
    *,
    gross_pnl: float,
    commission: float = 0.0,
    spread_cost: float = 0.0,
    financing_cost: float = 0.0,
    bars_held: int = 1,
    index: int = 0,
) -> Trade:
    """Operacion cerrada minima, con el resultado bruto y los costes al mando."""
    entry = TimestampNs(index * NS_PER_BAR)
    return Trade(
        symbol=Symbol("EURUSD"),
        direction=Direction.LONG,
        lots=1.0,
        entry_time=entry,
        entry_price=1.1000,
        exit_time=TimestampNs(int(entry) + bars_held * NS_PER_BAR),
        exit_price=1.1010,
        exit_reason=ExitReason.SIGNAL_EXIT,
        gross_pnl=gross_pnl,
        commission=commission,
        spread_cost=spread_cost,
        financing_cost=financing_cost,
        bars_held=bars_held,
    )


def _equity(*values: float) -> np.ndarray:
    return np.asarray(values, dtype=np.float64)


# ---------------------------------------------------------------------------
# 1. Cero operaciones es un resultado, no un caso especial
# ---------------------------------------------------------------------------


def test_no_trades_produces_the_domain_empty_result() -> None:
    """Sin operaciones se devuelve el vacio que el dominio ya define.

    Reconstruirlo aqui abriria la puerta a que las dos versiones discrepen, y
    la discrepancia solo se veria al comparar rankings.
    """
    metrics = performance_metrics(
        trades=[],
        equity=_equity(100.0, 100.0, 100.0),
        timeframe=Timeframe.M15,
        initial_equity=100.0,
    )

    assert metrics == PerformanceMetrics.empty(Timeframe.M15, bars=3)
    assert metrics.is_degenerate


def test_an_empty_run_is_still_attached_to_its_timeframe() -> None:
    """El vacio tambien pertenece a un marco temporal.

    Un resultado nulo de M15 y uno de H1 no son el mismo resultado: si el vacio
    perdiera el timeframe, discovery podria compararlos.
    """
    metrics = performance_metrics(
        trades=[], equity=_equity(), timeframe=Timeframe.H1, initial_equity=100.0
    )

    assert metrics.timeframe is Timeframe.H1
    assert metrics.bars == 0


# ---------------------------------------------------------------------------
# 2. Bruto, neto y costes: la relacion que revela una estrategia fragil
# ---------------------------------------------------------------------------


def test_gross_and_net_differ_exactly_by_the_costs() -> None:
    """`net_profit = gross_profit + gross_loss - costes`. Sin holguras.

    Es la identidad que sostiene `cost_ratio`. Si no se cumpliera, la metrica
    que revela a una estrategia que no sobrevive a los costes estaria midiendo
    otra cosa.
    """
    trades = [
        _trade(gross_pnl=100.0, commission=5.0),
        _trade(gross_pnl=-40.0, commission=5.0, index=1),
        _trade(gross_pnl=60.0, spread_cost=2.0, financing_cost=3.0, index=2),
    ]
    metrics = performance_metrics(
        trades=trades,
        equity=_equity(1_095.0, 1_050.0, 1_105.0),
        timeframe=Timeframe.M15,
        initial_equity=1_000.0,
    )

    assert metrics.gross_profit == pytest.approx(160.0)
    assert metrics.gross_loss == pytest.approx(-40.0)
    assert metrics.net_profit == pytest.approx(160.0 - 40.0 - 15.0)
    assert metrics.cost_ratio == pytest.approx(15.0 / 160.0)


def test_the_split_between_profit_and_loss_is_made_on_the_gross() -> None:
    """Una operacion con bruto positivo y neto negativo cuenta como ganancia bruta.

    No es una incoherencia con `win_rate`: son dos preguntas distintas. "Cuanto
    produjo la idea antes de pagar por ejecutarla" y "cuantas veces gane dinero
    de verdad" tienen respuestas distintas, y confundirlas esconde justo a la
    estrategia cuyo margen se come la ejecucion.
    """
    trades = [_trade(gross_pnl=10.0, commission=25.0)]
    metrics = performance_metrics(
        trades=trades,
        equity=_equity(985.0),
        timeframe=Timeframe.M15,
        initial_equity=1_000.0,
    )

    assert metrics.gross_profit == pytest.approx(10.0)
    assert metrics.gross_loss == pytest.approx(0.0)
    assert metrics.net_profit == pytest.approx(-15.0)
    assert metrics.win_rate == 0.0


def test_win_rate_counts_on_the_net() -> None:
    """Ganar es terminar con mas dinero, no con mejor precio."""
    trades = [
        _trade(gross_pnl=10.0, commission=1.0),
        _trade(gross_pnl=10.0, commission=20.0, index=1),
    ]
    metrics = performance_metrics(
        trades=trades,
        equity=_equity(1_009.0, 999.0),
        timeframe=Timeframe.M15,
        initial_equity=1_000.0,
    )

    assert metrics.win_rate == pytest.approx(0.5)


def test_profit_factor_and_cost_ratio_are_none_when_undefined() -> None:
    """Sin perdidas no hay `profit_factor`; sin bruto positivo no hay `cost_ratio`.

    `None` y no un relleno: un cero fingido no se distingue de un cociente
    legitimamente nulo, y un infinito contamina los artefactos JSON y encabeza
    cualquier ranking.
    """
    only_wins = performance_metrics(
        trades=[_trade(gross_pnl=10.0)],
        equity=_equity(1_010.0),
        timeframe=Timeframe.M15,
        initial_equity=1_000.0,
    )
    only_losses = performance_metrics(
        trades=[_trade(gross_pnl=-10.0)],
        equity=_equity(990.0),
        timeframe=Timeframe.M15,
        initial_equity=1_000.0,
    )

    assert only_wins.profit_factor is None
    assert only_losses.cost_ratio is None
    assert only_losses.profit_factor == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 3. Drawdown: la caida aparece cuando ocurre, no cuando se cierra
# ---------------------------------------------------------------------------


def test_the_first_bar_can_produce_a_drawdown() -> None:
    """El capital inicial entra en la curva.

    Sin el, la primera barra no tiene contra que medirse y una estrategia que
    pierde un 20% en su primer movimiento apareceria plana.
    """
    metrics = performance_metrics(
        trades=[_trade(gross_pnl=-200.0)],
        equity=_equity(800.0),
        timeframe=Timeframe.M15,
        initial_equity=1_000.0,
    )

    assert metrics.max_drawdown == pytest.approx(200.0)
    assert metrics.max_drawdown_pct == pytest.approx(0.2)


def test_drawdown_is_measured_against_the_previous_peak() -> None:
    """La caida se mide desde el maximo previo, no desde el inicio."""
    metrics = performance_metrics(
        trades=[_trade(gross_pnl=100.0), _trade(gross_pnl=-60.0, index=1)],
        equity=_equity(1_200.0, 900.0, 1_040.0),
        timeframe=Timeframe.M15,
        initial_equity=1_000.0,
    )

    assert metrics.max_drawdown == pytest.approx(300.0)
    assert metrics.max_drawdown_pct == pytest.approx(0.25)


def test_a_wiped_out_account_saturates_the_fraction_but_not_the_amount() -> None:
    """Perder mas del 100% del pico satura la fraccion; la perdida real no.

    `PerformanceMetrics` define la fraccion en [0,1] porque no hay grados de
    "mas que perdido todo". La magnitud absoluta si se conserva.
    """
    metrics = performance_metrics(
        trades=[_trade(gross_pnl=-1_500.0)],
        equity=_equity(-500.0),
        timeframe=Timeframe.M15,
        initial_equity=1_000.0,
    )

    assert metrics.max_drawdown_pct == pytest.approx(1.0)
    assert metrics.max_drawdown == pytest.approx(1_500.0)
    assert metrics.cagr == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# 4. Ratios: lo que se niega a devolver
# ---------------------------------------------------------------------------


def test_a_flat_curve_has_no_sharpe_instead_of_an_infinite_one() -> None:
    """Dispersion nula devuelve cero, nunca infinito.

    Una serie sin variacion no tiene ratio, y un infinito encabezaria cualquier
    ranking del zoo por la peor de las razones: no haberse movido.
    """
    metrics = performance_metrics(
        trades=[_trade(gross_pnl=0.0)],
        equity=_equity(1_000.0, 1_000.0, 1_000.0),
        timeframe=Timeframe.M15,
        initial_equity=1_000.0,
    )

    assert metrics.sharpe == 0.0
    assert metrics.sortino == 0.0
    assert metrics.calmar == 0.0


def test_sortino_never_punishes_less_than_sharpe_on_a_losing_series() -> None:
    """Sortino ignora la dispersion al alza; sobre perdidas puras coinciden en signo."""
    metrics = performance_metrics(
        trades=[_trade(gross_pnl=-10.0, index=i) for i in range(4)],
        equity=_equity(990.0, 980.0, 970.0, 960.0),
        timeframe=Timeframe.M15,
        initial_equity=1_000.0,
    )

    assert metrics.sharpe < 0.0
    assert metrics.sortino < 0.0


def test_calmar_is_the_annual_rate_over_the_fractional_drawdown() -> None:
    """`calmar = cagr / max_drawdown_pct`, sobre la FRACCION.

    Se fija el cociente exacto y no solo su signo. Un Calmar calculado sobre el
    drawdown en moneda -o devuelto sin dividir- da un numero de aspecto
    razonable que no es comparable entre instrumentos ni entre tamanos de
    cuenta, que es precisamente el error que ADR-0010 nombra. Una mutacion que
    devolvia `cagr` sin dividir sobrevivia a los tests anteriores.
    """
    metrics = performance_metrics(
        trades=[_trade(gross_pnl=100.0), _trade(gross_pnl=-60.0, index=1)],
        equity=_equity(1_200.0, 900.0, 1_040.0),
        timeframe=Timeframe.D1,
        initial_equity=1_000.0,
    )

    assert metrics.max_drawdown_pct == pytest.approx(0.25)
    assert metrics.calmar == pytest.approx(metrics.cagr / metrics.max_drawdown_pct, rel=1e-12)
    assert metrics.calmar != pytest.approx(metrics.cagr)


def test_sortino_ignores_the_dispersion_of_the_gains() -> None:
    """La semidesviacion solo mira hacia abajo, y eso la separa del Sharpe.

    Con ganancias muy dispersas y perdidas pequenas, Sortino tiene que salir
    MAYOR que Sharpe: aquella penaliza toda la variabilidad y esta solo la
    adversa. Se contrasta ademas contra la definicion calculada aparte, porque
    una mutacion que promediaba TODAS las desviaciones -no solo las negativas-
    sobrevivia comprobando unicamente el signo.
    """
    initial = 10_000.0
    steps = [300.0, -10.0, 500.0, -12.0, 20.0, -8.0, 700.0, -15.0]
    equity = _equity(*np.cumsum(steps).tolist()) + initial

    metrics = performance_metrics(
        trades=[_trade(gross_pnl=steps[i], index=i) for i in range(len(steps))],
        equity=equity,
        timeframe=Timeframe.M15,
        initial_equity=initial,
    )

    curve = [initial, *equity.tolist()]
    returns = [(curve[i + 1] - curve[i]) / curve[i] for i in range(len(curve) - 1)]
    mean = sum(returns) / len(returns)
    downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in returns) / len(returns))
    expected = mean / downside * math.sqrt(Timeframe.M15.bars_per_year)

    assert metrics.sortino == pytest.approx(expected, rel=1e-12)
    assert metrics.sortino > metrics.sharpe


def test_a_monotonic_curve_reports_no_calmar() -> None:
    """Sin caida el cociente no esta definido y se devuelve cero.

    Es la unica salida disponible -el campo no admite `None`- y se prefiere a un
    infinito. El caso solo aparece en curvas monotonas, que `is_degenerate` ya
    marca por el numero de operaciones.
    """
    metrics = performance_metrics(
        trades=[_trade(gross_pnl=10.0, index=i) for i in range(3)],
        equity=_equity(1_010.0, 1_020.0, 1_030.0),
        timeframe=Timeframe.M15,
        initial_equity=1_000.0,
    )

    assert metrics.max_drawdown_pct == 0.0
    assert metrics.calmar == 0.0
    assert metrics.cagr > 0.0


# ---------------------------------------------------------------------------
# 5. Anualizacion: el timeframe cambia el numero, y por eso va en el objeto
# ---------------------------------------------------------------------------


def test_the_same_series_annualizes_differently_by_timeframe() -> None:
    """Dos marcos temporales no producen el mismo Sharpe sobre la misma serie.

    Es exactamente el motivo de que `timeframe` sea campo obligatorio del
    resultado: sin el, discovery ordenaria en la misma lista numeros que no son
    comparables.
    """
    trades = [_trade(gross_pnl=10.0, index=i) for i in range(3)]
    equity = _equity(1_010.0, 1_005.0, 1_030.0)

    fast = performance_metrics(
        trades=trades, equity=equity, timeframe=Timeframe.M15, initial_equity=1_000.0
    )
    slow = performance_metrics(
        trades=trades, equity=equity, timeframe=Timeframe.H1, initial_equity=1_000.0
    )

    assert fast.sharpe != slow.sharpe
    assert fast.turnover > slow.turnover


def test_a_longer_period_compounds_into_a_smaller_annual_rate() -> None:
    """La misma ganancia total repartida en mas tiempo anualiza mas bajo.

    Se comprueba sobre un tramo de duracion realista y no sobre tres barras: por
    debajo de cierta longitud las dos cifras saturan en el techo y la
    comparacion dejaria de medir la anualizacion.
    """
    bars = 260
    equity = _equity(*[1_000.0 + 0.5 * i for i in range(1, bars + 1)])
    trades = [_trade(gross_pnl=1.0, index=i) for i in range(30)]

    daily = performance_metrics(
        trades=trades, equity=equity, timeframe=Timeframe.D1, initial_equity=1_000.0
    )
    hourly = performance_metrics(
        trades=trades, equity=equity, timeframe=Timeframe.H1, initial_equity=1_000.0
    )

    assert daily.cagr < hourly.cagr
    assert daily.cagr == pytest.approx(0.13, abs=0.02)


def test_a_sample_too_short_to_annualise_saturates_instead_of_overflowing() -> None:
    """Anualizar tres barras de M15 no puede reventar ni devolver infinito.

    Elevar el retorno total a `1/anos` con anos del orden de 1e-4 produce un
    exponente de miles: la primera version de este modulo lanzaba
    `OverflowError` con un backtest corto, que es la entrada que discovery
    genera a millares. Se satura en un techo finito, absurdo a proposito, para
    que la cifra siga siendo ordenable y serializable y para que verla signifique
    "muestra demasiado corta".
    """
    metrics = performance_metrics(
        trades=[_trade(gross_pnl=10.0, index=i) for i in range(3)],
        equity=_equity(1_010.0, 1_020.0, 1_030.0),
        timeframe=Timeframe.M15,
        initial_equity=1_000.0,
    )

    assert math.isfinite(metrics.cagr)
    assert math.isfinite(metrics.calmar)
    assert metrics.cagr > 0.0
    assert metrics.is_degenerate


def test_turnover_counts_operations_per_year() -> None:
    """Un ano exacto de barras devuelve tantas operaciones como hubo."""
    bars = int(Timeframe.D1.bars_per_year)
    trades = [_trade(gross_pnl=1.0, index=i) for i in range(12)]
    metrics = performance_metrics(
        trades=trades,
        equity=_equity(*[1_000.0 + i for i in range(1, bars + 1)]),
        timeframe=Timeframe.D1,
        initial_equity=1_000.0,
    )

    assert metrics.turnover == pytest.approx(12.0, rel=1e-9)


# ---------------------------------------------------------------------------
# 6. Exposicion: lo que se niega a truncar
# ---------------------------------------------------------------------------


def test_exposure_is_the_fraction_of_bars_with_an_open_position() -> None:
    metrics = performance_metrics(
        trades=[_trade(gross_pnl=5.0, bars_held=2), _trade(gross_pnl=5.0, bars_held=3, index=2)],
        equity=_equity(*[1_000.0 + i for i in range(1, 11)]),
        timeframe=Timeframe.M15,
        initial_equity=1_000.0,
    )

    assert metrics.exposure == pytest.approx(0.5)
    assert metrics.avg_bars_held == pytest.approx(2.5)


def test_more_held_bars_than_evaluated_bars_is_rejected() -> None:
    """No se recorta a 1.0: se rechaza.

    Un `exposure` saturado en silencio esconde el unico caso que puede
    producirlo -trades ajenos a esta curva, o solapados contados como
    secuenciales- y ese caso invalida ademas `avg_bars_held` y `turnover`.

    Se comprueba el MENSAJE y no solo el tipo. Sin el check del motor, el
    dominio acabaria rechazando el objeto igualmente -`exposure` saldria fuera
    de [0,1]- y el test pasaria por la razon equivocada, diagnosticando "metrica
    invalida" donde el problema es que las operaciones no pertenecen a esta
    curva. La mutacion que desactiva el control lo demostro.
    """
    with pytest.raises(InvariantViolation, match="ocupan mas barras"):
        performance_metrics(
            trades=[_trade(gross_pnl=1.0, bars_held=50)],
            equity=_equity(1_001.0, 1_002.0),
            timeframe=Timeframe.M15,
            initial_equity=1_000.0,
        )


# ---------------------------------------------------------------------------
# 7. Entradas que no admiten medida
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("initial", [0.0, -10.0])
def test_a_non_positive_initial_capital_is_rejected(initial: float) -> None:
    """Sin capital de partida no hay fraccion, ni retorno, ni CAGR que valgan."""
    with pytest.raises(InvariantViolation):
        performance_metrics(
            trades=[_trade(gross_pnl=1.0)],
            equity=_equity(1.0),
            timeframe=Timeframe.M15,
            initial_equity=initial,
        )


def test_a_two_dimensional_equity_is_rejected() -> None:
    """La curva es una serie. Una matriz significa que alguien compuso mal."""
    with pytest.raises(InvariantViolation):
        performance_metrics(
            trades=[_trade(gross_pnl=1.0)],
            equity=np.zeros((2, 2), dtype=np.float64) + 1_000.0,
            timeframe=Timeframe.M15,
            initial_equity=1_000.0,
        )


# ---------------------------------------------------------------------------
# 8. Pureza y determinismo
# ---------------------------------------------------------------------------


def test_the_inputs_are_never_modified() -> None:
    """Analytics no influye en lo que mide, y eso empieza por no tocarlo."""
    equity = _equity(1_010.0, 990.0, 1_030.0)
    original = equity.copy()
    trades = [_trade(gross_pnl=30.0)]

    performance_metrics(
        trades=trades, equity=equity, timeframe=Timeframe.M15, initial_equity=1_000.0
    )

    assert np.array_equal(equity, original)
    assert len(trades) == 1


def test_two_identical_calls_produce_identical_results() -> None:
    """Misma entrada, mismo resultado. Bit a bit, no aproximadamente (P1)."""
    trades = [_trade(gross_pnl=10.0 * (-1) ** i, index=i) for i in range(8)]
    equity = _equity(*[1_000.0 + 10.0 * (-1) ** i for i in range(8)])

    first = performance_metrics(
        trades=trades, equity=equity, timeframe=Timeframe.M15, initial_equity=1_000.0
    )
    second = performance_metrics(
        trades=trades, equity=equity, timeframe=Timeframe.M15, initial_equity=1_000.0
    )

    assert first.to_dict() == second.to_dict()


def test_sharpe_matches_its_textbook_definition() -> None:
    """El numero se contrasta contra la definicion, calculada aparte.

    Verificar el motor con el propio motor no prueba nada. Aqui la referencia se
    calcula con aritmetica de Python sobre la definicion de libro -media entre
    desviacion tipica MUESTRAL, por el factor de anualizacion- de modo que un
    cambio de `ddof` o del factor rompe el test.
    """
    initial = 10_000.0
    steps = [37.0, -19.0, 44.0, -8.0, 12.0, -25.0, 31.0, 5.0]
    equity = _equity(*np.cumsum(steps).tolist()) + initial
    trades = [_trade(gross_pnl=steps[i], index=i) for i in range(len(steps))]

    metrics = performance_metrics(
        trades=trades, equity=equity, timeframe=Timeframe.M15, initial_equity=initial
    )

    curve = [initial, *equity.tolist()]
    returns = [(curve[i + 1] - curve[i]) / curve[i] for i in range(len(curve) - 1)]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    expected = mean / math.sqrt(variance) * math.sqrt(Timeframe.M15.bars_per_year)

    assert metrics.sharpe == pytest.approx(expected, rel=1e-12)


def test_ratios_do_not_depend_on_the_size_of_the_account() -> None:
    """Multiplicar capital y resultados por una constante no altera un ratio.

    Es la propiedad que hace comparables dos corridas dimensionadas distinto, y
    la que sostiene que el zoo pueda ordenar estrategias de cuentas distintas.
    Una perdida de invarianza aqui significaria que algun ratio se calculo sobre
    moneda en lugar de sobre fraccion -el error que ADR-0010 nombra para Calmar-.
    """
    initial = 10_000.0
    steps = [37.0, -19.0, 44.0, -80.0, 12.0, -25.0, 31.0, 5.0]
    equity = _equity(*np.cumsum(steps).tolist()) + initial
    factor = 7.0

    base = performance_metrics(
        trades=[_trade(gross_pnl=steps[i], index=i) for i in range(len(steps))],
        equity=equity,
        timeframe=Timeframe.M15,
        initial_equity=initial,
    )
    scaled = performance_metrics(
        trades=[_trade(gross_pnl=steps[i] * factor, index=i) for i in range(len(steps))],
        equity=equity * factor,
        timeframe=Timeframe.M15,
        initial_equity=initial * factor,
    )

    for field in ("sharpe", "sortino", "max_drawdown_pct", "cagr", "calmar", "win_rate"):
        assert getattr(scaled, field) == pytest.approx(getattr(base, field), rel=1e-12), field
    assert scaled.max_drawdown == pytest.approx(base.max_drawdown * factor)


@pytest.mark.parametrize(
    ("name", "gross", "equity", "initial"),
    [
        ("una sola barra", 1.0, (10_001.0,), 10_000.0),
        ("ruina exacta", -10_000.0, (0.0,), 10_000.0),
        ("cuenta en negativo", -20_000.0, (-10_000.0,), 10_000.0),
        ("capital minusculo", 1e-9, (2e-9,), 1e-9),
        ("capital enorme", 1e12, (2e12,), 1e12),
        ("curva plana", 0.0, (10_000.0, 10_000.0, 10_000.0), 10_000.0),
    ],
)
def test_no_degenerate_input_produces_a_non_finite_number(
    name: str, gross: float, equity: tuple[float, ...], initial: float
) -> None:
    """Ningun caso limite devuelve `inf` ni `NaN`, ni revienta.

    Un no finito en el resultado contamina el artefacto JSON, el hash que lo
    sella y cualquier ranking que lo ordene, y reaparece kilometros mas abajo sin
    origen identificable. Los seis casos son los que producen division por cero,
    logaritmo de cero, exponente enorme o perdida total.
    """
    metrics = performance_metrics(
        trades=[_trade(gross_pnl=gross)],
        equity=_equity(*equity),
        timeframe=Timeframe.M15,
        initial_equity=initial,
    )

    numbers = {k: v for k, v in metrics.to_dict().items() if isinstance(v, float)}
    assert all(math.isfinite(v) for v in numbers.values()), f"{name}: {numbers}"


def test_the_result_is_a_valid_domain_object() -> None:
    """Toda salida pasa por las invariantes del dominio, sin atajos.

    Es lo que hace que "si existe, es correcto" siga siendo una propiedad del
    tipo tambien cuando el productor es este motor.
    """
    metrics = performance_metrics(
        trades=[_trade(gross_pnl=5.0, commission=1.0, index=i) for i in range(40)],
        equity=_equity(*[1_000.0 + 4.0 * i for i in range(1, 41)]),
        timeframe=Timeframe.M15,
        initial_equity=1_000.0,
    )

    assert isinstance(metrics, PerformanceMetrics)
    assert metrics.n_trades >= MIN_TRADES_FOR_INFERENCE
    assert not metrics.is_degenerate
    assert 0.0 <= metrics.win_rate <= 1.0
    assert 0.0 <= metrics.exposure <= 1.0
    assert 0.0 <= metrics.max_drawdown_pct <= 1.0
