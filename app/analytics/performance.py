"""Calculo de `PerformanceMetrics` a partir de operaciones y curva de equity.

    Trade[] + equity -> PerformanceMetrics

Es la mitad que faltaba de una separacion que el dominio ya declaraba: el
docstring de `domain.value_objects.metrics` dice desde su primera linea que el
objeto se define alli "aunque el calculo viva en `analytics`". El TIPO es un
contrato entre capas -backtest lo produce, walk-forward lo agrega, discovery lo
ordena, promocion lo evalua- y por eso pertenece al dominio; el CALCULO es un
motor con entrada, salida y prohibiciones propias, y pertenece aqui
(ADR-0012, parte D).

Lo que este motor NO hace, que es la parte del contrato que fija la frontera:

    no decide          mide lo ocurrido; no influye en ninguna ejecucion
    no ejecuta         no conoce broker, ni orden, ni posicion abierta
    no juzga           "cuanta confianza merece esto" es de `validation`
    no reordena        el ranking es de discovery y de promocion

Dos reglas de honestidad heredadas y no reinterpretadas. Todas las metricas de
resultado se calculan sobre PnL NETO, porque `PerformanceMetrics` no admite una
version bruta a proposito: hace imposible presentar por descuido un resultado
sin costes. Y `gross_profit`, `gross_loss` y `profit_factor` se calculan sobre
el BRUTO, que es lo que sus nombres dicen y lo que el contrato de resultado ya
fijaba; su diferencia con el neto es precisamente `cost_ratio`, la metrica que
revela una estrategia cuyo margen desaparece al pagar el mercado.

Funciones puras: todo entra por argumento, nada toca reloj, disco ni azar, y
ninguna modifica lo que recibe.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.core.exceptions import InvariantViolation
from app.core.math import annualization_factor, drawdown_curve, safe_divide
from app.core.types import FloatArray, Timeframe
from app.domain.entities.trade import Trade
from app.domain.value_objects.metrics import PerformanceMetrics

#: Grados de libertad de la desviacion tipica de los retornos por barra.
#:
#: Uno, no cero: la serie de retornos de una corrida es una MUESTRA del proceso
#: que genera la estrategia, no la poblacion entera. Con `ddof=0` el Sharpe sale
#: sistematicamente optimista, y el sesgo crece justo donde mas dana -en las
#: series cortas, que son las que menos evidencia tienen-.
_SAMPLE_DDOF: int = 1

#: Techo finito del CAGR, en fraccion. Mil millones por uno anual.
#:
#: No es una opinion sobre el rendimiento maximo alcanzable: es el limite que
#: impide que una extrapolacion desborde. El CAGR eleva el retorno total a
#: `1/anos`, de modo que una ganancia del 10% en tres barras de M15 -menos de
#: una hora- implica un exponente de ocho mil y `float` no puede representar el
#: resultado: la primera version de este modulo lanzaba `OverflowError` con un
#: backtest corto, que es exactamente lo que discovery produce a millares.
#:
#: Se satura en lugar de propagar infinito porque un infinito contamina los
#: artefactos JSON y encabeza cualquier ranking. El valor es deliberadamente
#: absurdo -ningun resultado real lo alcanza- para que verlo en un informe
#: signifique inequivocamente "esta cifra viene de una muestra demasiado corta
#: para anualizarla", que es informacion util y no un numero disfrazado.
#:
#: Lo que protege el ranking NO es este techo, sino `is_degenerate`: un
#: resultado con menos de `MIN_TRADES_FOR_INFERENCE` operaciones no compite.
_MAX_CAGR: float = 1e9


def performance_metrics(
    *,
    trades: Sequence[Trade],
    equity: FloatArray,
    timeframe: Timeframe,
    initial_equity: float,
) -> PerformanceMetrics:
    """Metricas de una corrida, a partir de lo que produjo y de como lo produjo.

    Args:
        trades: Operaciones CERRADAS, en orden cronologico. Una posicion abierta
            no es un resultado y no participa de ninguna metrica de operacion.
        equity: Capital marcado a mercado al cierre de cada barra, tal como lo
            entrega `BacktestEnginePort`. Su longitud ES el numero de barras
            evaluadas: no se pide aparte para que no puedan discrepar.
        timeframe: Marco temporal de las barras. Sin el no hay anualizacion
            posible, y `PerformanceMetrics` lo exige por el mismo motivo.
        initial_equity: Capital antes de la primera barra.

    Raises:
        InvariantViolation: Si `initial_equity` no es positivo, si `equity` no
            es unidimensional o si las operaciones ocupan mas barras de las
            evaluadas.
    """
    curve = _Curve.of(equity, initial_equity)
    if not trades:
        # Cero operaciones es un resultado representable, no un caso especial:
        # el dominio ya define su forma y reconstruirla aqui abriria la puerta a
        # que las dos versiones discrepen.
        return PerformanceMetrics.empty(timeframe, bars=curve.bars)

    totals = _Totals.of(trades)
    _reject_impossible_exposure(totals.bars_held, curve.bars)
    years = _years(curve.bars, timeframe)
    cagr = _cagr(initial_equity, curve.final_equity, years)
    count = len(trades)

    return PerformanceMetrics(
        timeframe=timeframe,
        bars=curve.bars,
        n_trades=count,
        net_profit=totals.net_profit,
        gross_profit=totals.gross_profit,
        gross_loss=totals.gross_loss,
        profit_factor=_ratio(totals.gross_profit, abs(totals.gross_loss)),
        win_rate=totals.winners / count,
        expectancy=totals.net_profit / count,
        max_drawdown=curve.max_drawdown,
        max_drawdown_pct=curve.max_drawdown_pct,
        cagr=cagr,
        sharpe=_sharpe(curve.returns, timeframe),
        sortino=_sortino(curve.returns, timeframe),
        calmar=_calmar(cagr, curve.max_drawdown_pct),
        exposure=totals.bars_held / curve.bars if curve.bars else 0.0,
        avg_bars_held=totals.bars_held / count,
        turnover=count / years if years > 0.0 else 0.0,
        cost_ratio=_ratio(totals.costs, abs(totals.gross_profit)),
    )


@dataclass(frozen=True, slots=True)
class _Totals:
    """Agregados de una serie de operaciones, recorrida UNA sola vez.

    Existe para no repetir seis comprensiones sobre la misma lista, que ademas
    de costar seis pasadas invita a que dos de ellas apliquen criterios
    distintos -una sobre bruto y otra sobre neto- sin que se note.
    """

    net_profit: float
    gross_profit: float
    gross_loss: float
    costs: float
    winners: int
    bars_held: int

    @classmethod
    def of(cls, trades: Sequence[Trade]) -> _Totals:
        """Acumula lo que hace falta, con el criterio de cada metrica explicito.

        `winners` se cuenta sobre el resultado NETO -es lo que `Trade.is_winner`
        define- mientras que `gross_profit` y `gross_loss` se parten por el signo
        del BRUTO. No es una incoherencia: son dos preguntas distintas. La
        primera es "cuantas veces gane dinero de verdad"; la segunda, "cuanto
        produjo la idea antes de pagar por ejecutarla".
        """
        gross_profit = sum(t.gross_pnl for t in trades if t.gross_pnl > 0.0)
        gross_loss = sum(t.gross_pnl for t in trades if t.gross_pnl < 0.0)
        return cls(
            net_profit=float(sum(t.net_pnl for t in trades)),
            gross_profit=float(gross_profit),
            gross_loss=float(gross_loss),
            costs=float(sum(t.total_cost for t in trades)),
            winners=sum(1 for t in trades if t.is_winner),
            bars_held=sum(t.bars_held for t in trades),
        )


@dataclass(frozen=True, slots=True)
class _Curve:
    """Todo lo que se deduce de la curva de capital, calculado una sola vez.

    Hermano de `_Totals`: un objeto por cada una de las dos entradas del motor.
    La simetria no es estetica -evita que el cuerpo principal mezcle lo que sale
    de las operaciones con lo que sale de la curva, que es la confusion que
    produce metricas cruzadas sin que se note-.
    """

    bars: int
    final_equity: float
    returns: FloatArray
    max_drawdown: float
    max_drawdown_pct: float

    @classmethod
    def of(cls, equity: FloatArray, initial_equity: float) -> _Curve:
        """Compone la curva y deriva de ella lo que las metricas necesitan.

        El capital inicial se antepone a la serie en lugar de deducirse de
        `equity[0]`, que ya incorpora el resultado de la primera barra.
        Deducirlo desplazaria una barra toda la curva de drawdown y haria que el
        primer movimiento no contara nunca: una estrategia que pierde el 20% en
        su primera barra apareceria plana.
        """
        if initial_equity <= 0.0:
            raise InvariantViolation(
                "initial_equity debe ser positivo", initial_equity=initial_equity
            )
        values = np.asarray(equity, dtype=np.float64)
        if values.ndim != 1:
            raise InvariantViolation("equity debe ser unidimensional", ndim=values.ndim)

        curve = np.concatenate(([initial_equity], values))
        max_drawdown, max_drawdown_pct = _drawdown(curve)
        return cls(
            bars=int(values.size),
            final_equity=float(curve[-1]),
            returns=_bar_returns(curve),
            max_drawdown=max_drawdown,
            max_drawdown_pct=max_drawdown_pct,
        )


def _reject_impossible_exposure(bars_held: int, bars: int) -> None:
    """Las operaciones no pueden ocupar mas barras de las que hubo.

    Se rechaza en lugar de recortar a 1.0. Un `exposure` saturado en silencio
    esconde el unico caso que puede producirlo -operaciones solapadas contadas
    como si fueran secuenciales, o una serie de trades ajena a esta curva-, y
    ese caso invalida ademas `avg_bars_held` y `turnover`. Truncar convertiria
    un error de composicion en un numero plausible.
    """
    if bars_held > bars:
        raise InvariantViolation(
            "las operaciones ocupan mas barras de las evaluadas",
            bars_held=bars_held,
            bars=bars,
        )


def _bar_returns(curve: FloatArray) -> FloatArray:
    """Retorno simple por barra sobre la curva de capital.

    Un capital no positivo no admite retorno relativo, y ahi `safe_divide`
    rellena con cero: la cuenta ya esta arruinada y lo que ocurra despues no
    aporta informacion sobre el rendimiento. El hecho no se pierde -queda en
    `max_drawdown_pct` y en un `cagr` de -1-.
    """
    if curve.size < 2:
        return np.zeros(0, dtype=np.float64)
    return safe_divide(np.diff(curve), curve[:-1], fill=0.0)


def _drawdown(curve: FloatArray) -> tuple[float, float]:
    """Caida maxima en moneda de cuenta y en fraccion del pico previo.

    La fraccion se satura en 1.0 y la razon es de dominio, no de comodidad:
    `PerformanceMetrics` la define en [0,1] porque perder mas del 100% del pico
    significa cuenta arruinada, y una vez ahi la magnitud exacta del numero
    negativo no ordena nada -no hay grados de "mas que perdido todo"-. La
    perdida absoluta si se conserva intacta en `max_drawdown`.
    """
    peak = np.maximum.accumulate(curve)
    absolute = float(np.max(peak - curve)) if curve.size else 0.0
    fraction = float(np.max(drawdown_curve(curve))) if curve.size else 0.0
    return max(absolute, 0.0), min(max(fraction, 0.0), 1.0)


def _years(bars: int, timeframe: Timeframe) -> float:
    """Duracion del periodo evaluado, en anos negociables.

    Se deriva de `Timeframe.bars_per_year`, que asume mercado de divisas -24
    horas, 5 dias, 52 semanas- y lo declara. No se mide sobre las marcas de
    tiempo de la serie a proposito: un fin de semana o un festivo alargarian el
    calendario sin anadir una sola barra negociable, y la anualizacion resultante
    dependeria de donde empieza el tramo en lugar de cuanto mercado contiene.
    """
    return bars / timeframe.bars_per_year


def _cagr(initial_equity: float, final_equity: float, years: float) -> float:
    """Tasa de crecimiento anual compuesta, en fraccion.

    Una cuenta que termina en cero o en negativo devuelve -1.0 -perdida total-
    en lugar de un complejo o un `NaN`: elevar un numero negativo a un exponente
    fraccionario no tiene resultado real, y propagar `NaN` contaminaria todo
    ranking posterior sin decir por que.

    El calculo va en espacio logaritmico y satura en `_MAX_CAGR`. La forma
    directa -`ratio ** (1/anos)`- desborda con cualquier tramo corto, que es la
    entrada habitual de discovery, y `expm1` conserva ademas la precision
    cuando el exponente es pequeno, que es el caso de las series largas.
    """
    if years <= 0.0:
        return 0.0
    if final_equity <= 0.0:
        return -1.0
    exponent = math.log(final_equity / initial_equity) / years
    if exponent >= math.log1p(_MAX_CAGR):
        return _MAX_CAGR
    return float(math.expm1(exponent))


def _sharpe(returns: FloatArray, timeframe: Timeframe) -> float:
    """Sharpe anualizado sobre retornos por barra, con tasa libre de riesgo cero.

    Con `Rf = 0` es en rigor un ratio de INFORMACION, y el objeto de dominio ya
    lo declara asi para que el numero no se compare a la ligera con fuentes que
    si descuentan una tasa.

    Dispersion nula devuelve cero, no infinito: una serie sin variacion no tiene
    ratio, y un infinito lo encabezaria todo en cualquier ranking.
    """
    if returns.size < 2:
        return 0.0
    deviation = float(np.std(returns, ddof=_SAMPLE_DDOF))
    if deviation <= 0.0:
        return 0.0
    return float(np.mean(returns)) / deviation * annualization_factor(timeframe.bars_per_year)


def _sortino(returns: FloatArray, timeframe: Timeframe) -> float:
    """Variante que penaliza solo la desviacion a la baja.

    La semidesviacion se promedia sobre TODAS las observaciones y no solo sobre
    las negativas. Es la definicion habitual y la que mantiene comparables dos
    estrategias con distinta proporcion de barras perdedoras: promediando solo
    las negativas, una estrategia que pierde raras veces pero mucho saldria
    mejor que otra que pierde a menudo y poco, que es lo contrario de lo que la
    metrica pretende medir.
    """
    if returns.size < 2:
        return 0.0
    downside = np.minimum(returns, 0.0)
    deviation = float(np.sqrt(np.mean(np.square(downside))))
    if deviation <= 0.0:
        return 0.0
    return float(np.mean(returns)) / deviation * annualization_factor(timeframe.bars_per_year)


def _calmar(cagr: float, max_drawdown_pct: float) -> float:
    """`cagr / max_drawdown_pct`, sobre la FRACCION y nunca sobre la moneda.

    Un Calmar en unidades de cuenta no es comparable entre instrumentos ni entre
    tamanos de cuenta (ADR-0010).

    Sin caida alguna el cociente no esta definido y se devuelve 0.0. Es la
    unica salida disponible -el campo es `float` y no admite `None`, a
    diferencia de `profit_factor`- y se prefiere a un infinito porque un
    infinito encabezaria cualquier ranking del zoo. El caso solo aparece en
    curvas monotonas o planas, que `is_degenerate` ya marca por otro camino.
    """
    if max_drawdown_pct <= 0.0:
        return 0.0
    return cagr / max_drawdown_pct


def _ratio(numerator: float, denominator: float) -> float | None:
    """Cociente que devuelve `None` cuando el denominador es cero.

    `None` y no un relleno: `profit_factor` y `cost_ratio` estan declarados
    `float | None` en el dominio precisamente para no propagar infinitos a los
    artefactos JSON ni a los rankings. Un cero fingido seria peor que el
    infinito, porque no se distingue de un cociente legitimamente nulo.
    """
    if denominator <= 0.0:
        return None
    return numerator / denominator


__all__ = ["performance_metrics"]
