"""El contrato de `FeatureFn`, verificado sobre TODAS las features registradas.

`shared.ports.FeatureFn` promete cuatro cosas y este fichero es donde se
comprueban. Esta parametrizado sobre el catalogo, no sobre una lista escrita a
mano: una feature nueva queda cubierta por el solo hecho de registrarse, que es
la unica forma de que la cobertura no se degrade con el tiempo.

El test que sostiene el fichero es `test_a_feature_cannot_see_the_future`. Los
demas comprueban forma; ese comprueba causalidad, que es la propiedad por la que
un backtest significa algo. Un indicador que mira una barra hacia delante produce
una curva de equity magnifica y completamente falsa, y no falla en ninguna parte.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from app.core.registry.metadata import ComponentEntry
from app.core.types import Timeframe
from app.domain.entities.bars import Bars
from app.research.features import FEATURES
from app.research.features.momentum import FLAT_RSI, roc, rsi
from app.research.features.trend import ema, sma
from app.research.features.volatility import atr, stdev, true_range

STEP = Timeframe.M15.nanoseconds
PERIOD = 14

ENTRIES: list[ComponentEntry[Any]] = sorted(FEATURES, key=lambda e: e.name)
IDS: list[str] = [entry.name for entry in ENTRIES]


def _bars(count: int = 400, *, seed: int = 7) -> Bars:
    """Serie sintetica pero con forma de mercado: deriva, ruido y huecos.

    Sintetica a proposito. La causalidad tiene que cumplirse sobre CUALQUIER
    entrada, y una serie generada permite fijar la semilla y meter casos que en
    los datos reales podrian no aparecer -un tramo plano, un salto grande-.
    """
    rng = np.random.default_rng(seed)
    close = 1.10 + np.cumsum(rng.normal(0.0, 0.0004, count))
    apertura = close - rng.normal(0.0, 0.0001, count)
    margen = np.abs(rng.normal(0.0, 0.0003, count)) + 0.00005
    # `high` y `low` se construyen DESDE open y close, no alrededor del cierre:
    # `Bars` exige que la mecha envuelva al cuerpo, y una serie que no lo cumple
    # ni siquiera es un mercado posible.
    return Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=np.arange(0, count * STEP, STEP, dtype=np.int64),
        open=apertura,
        high=np.maximum(apertura, close) + margen,
        low=np.minimum(apertura, close) - margen,
        close=close,
        volume=np.full(count, 100.0),
    )


def _call(entry: ComponentEntry[Any], bars: Bars, **overrides: Any) -> Any:
    """Invoca una feature con sus parametros resueltos y validados."""
    return entry.fn(bars, **entry.resolve({"period": PERIOD, **overrides}))


# ---------------------------------------------------------------------------
# Causalidad
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
@pytest.mark.parametrize("cut", [50, 137, 300], ids=lambda c: f"corte{c}")
def test_a_feature_cannot_see_the_future(entry: ComponentEntry[Any], cut: int) -> None:
    """Truncar la serie no puede cambiar ningun valor anterior al corte.

    Es la comprobacion mas fuerte que se puede hacer sin leer el codigo: si
    `salida[i]` dependiera de una barra posterior a `i`, recortar la serie en
    `cut` cambiaria valores en `[0, cut)` y la comparacion fallaria. Ninguna
    anotacion de tipo puede expresar esto.
    """
    bars = _bars()
    completa = np.asarray(_call(entry, bars))
    truncada = np.asarray(_call(entry, bars.slice(0, cut)))

    assert truncada.size == cut
    np.testing.assert_allclose(
        truncada,
        completa[:cut],
        equal_nan=True,
        rtol=1e-12,
        atol=0.0,
        err_msg=f"{entry.name} mira al futuro: truncar en {cut} cambio valores anteriores",
    )


@pytest.mark.contract
@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_appending_a_bar_never_rewrites_history(entry: ComponentEntry[Any]) -> None:
    """La version incremental de lo mismo, que es como corre en vivo.

    En produccion llega una vela y se recalcula. Si al hacerlo cambiara algun
    valor pasado, la senal emitida ayer no seria la que el backtest evaluo.
    """
    bars = _bars(count=200)
    antes = np.asarray(_call(entry, bars.slice(0, 199)))
    despues = np.asarray(_call(entry, bars))

    np.testing.assert_allclose(despues[:199], antes, equal_nan=True, rtol=1e-12, atol=0.0)


# ---------------------------------------------------------------------------
# Forma y calentamiento
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_output_is_aligned_with_the_bars(entry: ComponentEntry[Any]) -> None:
    bars = _bars()
    salida = np.asarray(_call(entry, bars))

    assert salida.size == len(bars)
    assert salida.dtype == np.float64


@pytest.mark.contract
@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
@pytest.mark.parametrize("period", [5, 14, 50], ids=lambda p: f"periodo{p}")
def test_the_declared_warmup_matches_the_observed_one(
    entry: ComponentEntry[Any], period: int
) -> None:
    """Declarado y observado tienen que coincidir exactamente.

    `registry.protocols` dice que el calentamiento "se contrasta contra el
    observado en los tests", y por eso se exige igualdad y no holgura. Declarar
    de menos arrastra barras contaminadas al inicio de cada fold; declarar de
    mas descarta barras buenas y nadie lo nota nunca.
    """
    bars = _bars()
    salida = np.asarray(_call(entry, bars, period=period))
    declarado = entry.warmup({"period": period})

    validos = np.flatnonzero(~np.isnan(salida))
    assert validos.size > 0, f"{entry.name} no produjo ni un valor con periodo {period}"
    observado = int(validos[0])

    assert observado == declarado, (
        f"{entry.name} declara warmup={declarado} pero el primer valor "
        f"calculable esta en el indice {observado}"
    )


@pytest.mark.contract
@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_warmup_is_nan_and_never_zero(entry: ComponentEntry[Any]) -> None:
    """Un cero es un valor legitimo de un indicador.

    Rellenar el arranque con ceros no produce ningun fallo visible: produce
    senales fantasma en las primeras barras de cada fold, que es donde menos se
    miran.
    """
    bars = _bars()
    salida = np.asarray(_call(entry, bars))
    calentamiento = entry.warmup({"period": PERIOD})

    assert np.all(np.isnan(salida[:calentamiento]))
    assert not np.any(salida[:calentamiento] == 0.0)


@pytest.mark.contract
@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_a_series_shorter_than_the_warmup_gives_all_nan(entry: ComponentEntry[Any]) -> None:
    """Sin datos suficientes se devuelve NaN, no se lanza ni se rellena.

    Lo pide el arranque de cada fold: el motor tiene que poder pedir la feature
    sin comprobar antes cuantas barras hay.
    """
    salida = np.asarray(_call(entry, _bars(count=PERIOD - 1)))

    assert salida.size == PERIOD - 1
    assert np.all(np.isnan(salida))


# ---------------------------------------------------------------------------
# Pureza
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_a_feature_is_deterministic(entry: ComponentEntry[Any]) -> None:
    """Bit a bit, no aproximadamente: es la base de todo lo reproducible."""
    bars = _bars()
    primera = np.asarray(_call(entry, bars))

    for _ in range(3):
        np.testing.assert_array_equal(np.asarray(_call(entry, bars)), primera)


@pytest.mark.contract
@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_a_feature_does_not_touch_its_input(entry: ComponentEntry[Any]) -> None:
    """Si una feature mutara las barras, la siguiente veria datos alterados."""
    bars = _bars()
    copias = {
        campo: np.array(getattr(bars, campo), copy=True)
        for campo in ("open", "high", "low", "close", "volume", "timestamp")
    }

    _call(entry, bars)

    for campo, original in copias.items():
        np.testing.assert_array_equal(np.asarray(getattr(bars, campo)), original)


# ---------------------------------------------------------------------------
# Aritmetica de cada indicador
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_simple_average_is_the_average() -> None:
    bars = _bars(count=50)
    close = np.asarray(bars.close)
    salida = sma(bars, period=10)

    assert salida[9] == pytest.approx(close[:10].mean())
    assert salida[49] == pytest.approx(close[40:50].mean())


@pytest.mark.unit
def test_the_exponential_average_is_seeded_with_the_simple_one() -> None:
    """Sembrar con un unico cierre arrastraria el ruido de esa barra durante
    decenas de posiciones, justo al principio de cada fold."""
    bars = _bars(count=50)
    close = np.asarray(bars.close)
    salida = ema(bars, period=10)

    assert salida[9] == pytest.approx(close[:10].mean())
    alpha = 2.0 / 11.0
    assert salida[10] == pytest.approx(alpha * close[10] + (1 - alpha) * salida[9])


@pytest.mark.unit
def test_the_averages_track_a_constant_series_exactly() -> None:
    """Con precio constante ambas medias valen ese precio, sin deriva."""
    constante = Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=np.arange(0, 100 * STEP, STEP, dtype=np.int64),
        open=np.full(100, 1.2),
        high=np.full(100, 1.2),
        low=np.full(100, 1.2),
        close=np.full(100, 1.2),
        volume=np.full(100, 1.0),
    )

    assert sma(constante, period=20)[99] == pytest.approx(1.2)
    assert ema(constante, period=20)[99] == pytest.approx(1.2)
    assert stdev(constante, period=20)[99] == pytest.approx(0.0)
    assert atr(constante, period=14)[99] == pytest.approx(0.0)


@pytest.mark.unit
def test_the_relative_strength_stays_inside_its_range() -> None:
    valores = rsi(_bars(count=500), period=14)
    definidos = valores[~np.isnan(valores)]

    assert definidos.size > 0
    assert np.all((definidos >= 0.0) & (definidos <= 100.0))


@pytest.mark.unit
def test_a_series_that_only_rises_saturates_the_strength() -> None:
    """Sin perdidas el cociente es infinito y la formula no puede evaluarse."""
    subida = Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=np.arange(0, 60 * STEP, STEP, dtype=np.int64),
        open=np.linspace(1.0, 1.6, 60),
        high=np.linspace(1.0, 1.6, 60) + 0.001,
        low=np.linspace(1.0, 1.6, 60) - 0.001,
        close=np.linspace(1.0, 1.6, 60),
        volume=np.full(60, 1.0),
    )

    assert rsi(subida, period=14)[59] == pytest.approx(100.0)


@pytest.mark.unit
def test_a_flat_series_gives_a_neutral_strength() -> None:
    """Ni sobrecompra ni sobreventa: sin movimiento no hay informacion, y
    devolver 0 o 100 inventaria una senal."""
    plana = Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=np.arange(0, 60 * STEP, STEP, dtype=np.int64),
        open=np.full(60, 1.1),
        high=np.full(60, 1.1),
        low=np.full(60, 1.1),
        close=np.full(60, 1.1),
        volume=np.full(60, 1.0),
    )

    assert rsi(plana, period=14)[59] == pytest.approx(FLAT_RSI)


@pytest.mark.unit
def test_the_true_range_accounts_for_gaps() -> None:
    """El rango de la barra ignora los huecos, y un hueco es movimiento real
    que el stop sufre igual."""
    con_hueco = Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=np.array([0, STEP], dtype=np.int64),
        open=np.array([1.1000, 1.2000]),
        high=np.array([1.1010, 1.2010]),
        low=np.array([1.0990, 1.1990]),
        close=np.array([1.1000, 1.2000]),
        volume=np.array([1.0, 1.0]),
    )

    rango = true_range(con_hueco)

    assert np.isnan(rango[0]), "la primera barra no tiene anterior"
    # El rango propio es 0.0020, pero el salto desde el cierre previo es 0.1010.
    assert rango[1] == pytest.approx(0.1010)


@pytest.mark.unit
def test_the_average_true_range_is_never_negative() -> None:
    valores = atr(_bars(count=500), period=14)
    definidos = valores[~np.isnan(valores)]

    assert definidos.size > 0
    assert np.all(definidos >= 0.0)


@pytest.mark.unit
def test_the_rate_of_change_measures_the_relative_move() -> None:
    bars = _bars(count=100)
    close = np.asarray(bars.close)
    salida = roc(bars, period=10)

    assert salida[50] == pytest.approx(close[50] / close[40] - 1.0)


@pytest.mark.unit
def test_a_zero_price_gives_nan_and_not_infinity() -> None:
    """`Bars` no exige precios positivos, asi que el denominador puede anularse.

    Un infinito propagado contaminaria en silencio todo lo que venga despues; el
    NaN es el mismo marcador de "no calculable" que usa el calentamiento.
    """
    con_cero = Bars.from_arrays(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        timestamp=np.arange(0, 20 * STEP, STEP, dtype=np.int64),
        open=np.array([0.0] * 5 + [1.1] * 15),
        high=np.array([0.0] * 5 + [1.1] * 15),
        low=np.array([0.0] * 5 + [1.1] * 15),
        close=np.array([0.0] * 5 + [1.1] * 15),
        volume=np.full(20, 1.0),
    )

    salida = roc(con_cero, period=3)

    assert not np.any(np.isinf(salida)), "un infinito contaminaria todo lo posterior"
    assert np.all(np.isnan(salida[3:5]))


# ---------------------------------------------------------------------------
# El catalogo
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_the_catalogue_is_not_empty() -> None:
    """Sin importar los modulos de familia el catalogo quedaria vacio y
    discovery no encontraria nada, sin que ningun error lo delatara."""
    assert len(FEATURES) >= 6
    assert {"trend", "momentum", "volatility"} <= FEATURES.tags()


@pytest.mark.contract
@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_every_feature_declares_its_family_and_parameters(
    entry: ComponentEntry[Any],
) -> None:
    """Discovery compone por familia; una feature sin tag es invisible para el."""
    assert entry.tags, f"{entry.name} no declara familia"
    assert entry.params, f"{entry.name} no declara parametros explorables"
    assert entry.description, f"{entry.name} no describe que hace"
    for spec in entry.params:
        assert spec.choices, f"{entry.name}.{spec.name} no declara valores explorables"


@pytest.mark.contract
@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_an_undeclared_parameter_is_rejected(entry: ComponentEntry[Any]) -> None:
    """Es lo que permite que las features declaren su firma concreta.

    El catalogo guarda `Callable[..., FloatArray]` porque una feature con
    `**params` tragaria una errata en silencio. La validacion de nombres vive
    aqui, en `resolve`, y este test es lo que la sostiene.
    """
    from app.core.registry.exceptions import InvalidParameter

    with pytest.raises(InvalidParameter):
        entry.resolve({"periodd": 14})
