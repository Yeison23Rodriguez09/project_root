"""`MT5MarketDataAdapter` verificado sin terminal, con un doble del modulo.

Toda la bateria corre en cualquier maquina y en CI. Es un requisito del
contrato, no una comodidad: `configs/runtime.toml` exige
`require_synthetic_data_only` en modo `ci`, de modo que el pipeline no puede
hablar con un broker real.

Lo que se prueba aqui es el CONTRATO del adaptador: que verifique lo que dice
verificar, que traduzca sin inventar y que falle con el diagnostico correcto. La
descarga contra un terminal vivo pertenece a R2B, que es otra cosa: alli se mide
lo que este doble no puede saber -por ejemplo la zona horaria del servidor-.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.broker.mt5_source import MAX_BARS_WITHOUT_RANGE, MT5MarketDataAdapter
from app.core.exceptions import DataSourceError, InvariantViolation
from app.core.types import NS_PER_SECOND, Symbol, Timeframe, TimestampNs
from app.shared.ports import MarketDataPort
from tests.fakes.mt5 import FakeMT5, rates_from_seconds

M15_S = Timeframe.M15.nanoseconds // NS_PER_SECOND


def _terminal(**overrides: object) -> FakeMT5:
    """Terminal simulado sano, con las desviaciones que pida el test."""
    fake = FakeMT5(rates=rates_from_seconds(1_700_000_000, M15_S, 10))
    for name, value in overrides.items():
        setattr(fake, name, value)
    return fake


# ---------------------------------------------------------------------------
# Contrato del puerto
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_adapter_satisfies_the_port() -> None:
    """Cumplimiento estructural: encaja con el `Protocol` sin heredarlo.

    Es lo que permite intercambiarlo con el adaptador de fichero sin que ningun
    consumidor cambie una linea.
    """
    assert isinstance(MT5MarketDataAdapter(terminal=_terminal()), MarketDataPort)


@pytest.mark.unit
def test_the_adapter_accepts_no_credentials() -> None:
    """No recibe usuario ni contrasena, y no debe llegar a recibirlos.

    La autenticacion es del operador: se conecta al terminal que YA tiene sesion.
    Un adaptador con credenciales convertiria cada fichero de configuracion y
    cada log en un sitio donde pueden filtrarse. Se fija como asercion para que
    anadirlas sea una decision visible y no un parametro mas.
    """
    import inspect

    parameters = set(inspect.signature(MT5MarketDataAdapter.__init__).parameters)

    assert parameters == {"self", "terminal"}
    assert not parameters & {"login", "password", "server", "credentials", "token"}


# ---------------------------------------------------------------------------
# Conexion: tres comprobaciones con diagnosticos distintos
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_connect_reports_terminal_and_account() -> None:
    adapter = MT5MarketDataAdapter(terminal=_terminal())

    status = adapter.connect()

    assert status.connected
    assert status.login == 999_000_111
    assert status.server == "FakeBroker-Demo"
    assert status.symbols_total == 3
    assert "connected" in status.to_dict()


@pytest.mark.unit
def test_a_terminal_that_does_not_start_is_reported_as_such() -> None:
    fake = _terminal(initialize_ok=False, error=(-10005, "IPC timeout"))
    adapter = MT5MarketDataAdapter(terminal=fake)

    with pytest.raises(DataSourceError) as error:
        adapter.connect()

    assert "inicializar" in str(error.value)
    assert error.value.context["mt5_code"] == -10005


@pytest.mark.unit
def test_a_terminal_without_a_session_is_distinguished_from_one_that_is_down() -> None:
    """Un terminal abierto sin cuenta responde a `initialize` y no sirve.

    Confundirlo con "el terminal no arranca" manda a reinstalar cuando lo que
    hace falta es iniciar sesion.
    """
    adapter = MT5MarketDataAdapter(terminal=_terminal(account_info_value=None))

    with pytest.raises(DataSourceError) as error:
        adapter.connect()

    assert "sesion" in str(error.value)


@pytest.mark.unit
def test_a_session_not_connected_to_its_server_is_rejected() -> None:
    from tests.fakes.mt5 import FakeTerminalInfo

    adapter = MT5MarketDataAdapter(
        terminal=_terminal(terminal_info_value=FakeTerminalInfo(connected=False))
    )

    with pytest.raises(DataSourceError) as error:
        adapter.connect()

    assert "servidor" in str(error.value)


@pytest.mark.unit
def test_a_failed_connection_does_not_leave_the_terminal_open() -> None:
    """Si la verificacion falla despues de `initialize`, hay que cerrar.

    Dejar la conexion abierta tras rechazarla acumularia sesiones muertas en el
    terminal a cada intento.
    """
    fake = _terminal(account_info_value=None)
    adapter = MT5MarketDataAdapter(terminal=fake)

    with pytest.raises(DataSourceError):
        adapter.connect()

    assert fake.called("shutdown")
    assert not fake.initialized


@pytest.mark.unit
def test_disconnect_is_idempotent() -> None:
    """Parar dos veces no es un error, y parar sin haber arrancado tampoco."""
    fake = _terminal()
    adapter = MT5MarketDataAdapter(terminal=fake)

    adapter.disconnect()
    assert not fake.called("shutdown")

    adapter.connect()
    adapter.disconnect()
    adapter.disconnect()
    assert sum(1 for call, _ in fake.calls if call == "shutdown") == 1


@pytest.mark.unit
def test_the_adapter_works_as_a_context_manager() -> None:
    fake = _terminal()

    with MT5MarketDataAdapter(terminal=fake) as adapter:
        assert adapter.status().connected

    assert fake.called("shutdown")


# ---------------------------------------------------------------------------
# Descarga: traduce, no inventa
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_connects_on_its_own() -> None:
    """Sin esto, `load` sobre un terminal sin inicializar llegaba a `copy_rates`
    y devolvia "no hay barras", cuando el problema era que no habia terminal. Un
    diagnostico equivocado manda a buscar el error donde no esta.
    """
    fake = _terminal()
    adapter = MT5MarketDataAdapter(terminal=fake)

    adapter.load(Symbol("EURUSD"), Timeframe.M15)

    assert fake.called("initialize")


@pytest.mark.unit
def test_the_symbol_is_selected_before_asking_for_data() -> None:
    """MT5 no entrega historico de un simbolo fuera de Market Watch.

    Devuelve vacio con "Invalid params", que sugiere un error de llamada cuando
    lo que falta es la seleccion. Un broker publica miles de instrumentos y solo
    unos pocos vienen seleccionados.
    """
    fake = _terminal()
    adapter = MT5MarketDataAdapter(terminal=fake)

    adapter.load(Symbol("EURUSD"), Timeframe.M15)

    assert fake.arguments_of("symbol_select") == ("EURUSD", True)
    assert fake.calls.index(("symbol_select", ("EURUSD", True))) < next(
        i for i, (call, _) in enumerate(fake.calls) if call.startswith("copy_rates")
    )


@pytest.mark.unit
def test_a_symbol_the_broker_rejects_is_reported_clearly() -> None:
    adapter = MT5MarketDataAdapter(terminal=_terminal(symbol_select_ok=False))

    with pytest.raises(DataSourceError) as error:
        adapter.load(Symbol("NOEXISTE"), Timeframe.M15)

    assert "simbolo" in str(error.value)


@pytest.mark.unit
def test_seconds_become_nanoseconds_without_rounding() -> None:
    """MT5 publica la apertura en segundos; el dominio la exige en nanosegundos."""
    fake = _terminal(rates=rates_from_seconds(1_700_000_000, M15_S, 5))
    adapter = MT5MarketDataAdapter(terminal=fake)

    bars = adapter.load(Symbol("EURUSD"), Timeframe.M15)

    assert len(bars) == 5
    assert int(bars.timestamp[0]) == 1_700_000_000 * NS_PER_SECOND
    assert int(bars.timestamp[1]) - int(bars.timestamp[0]) == Timeframe.M15.nanoseconds


@pytest.mark.unit
def test_tick_volume_is_the_volume_that_reaches_the_domain() -> None:
    """En divisas el volumen real es cero y el util es el de ticks."""
    fake = _terminal(rates=rates_from_seconds(1_700_000_000, M15_S, 4))
    adapter = MT5MarketDataAdapter(terminal=fake)

    bars = adapter.load(Symbol("EURUSD"), Timeframe.M15)

    assert np.asarray(bars.volume).tolist() == [100.0, 101.0, 102.0, 103.0]


@pytest.mark.unit
def test_a_range_is_asked_as_a_range() -> None:
    fake = _terminal()
    adapter = MT5MarketDataAdapter(terminal=fake)

    adapter.load(
        Symbol("EURUSD"),
        Timeframe.M15,
        start_ns=TimestampNs(1_700_000_000 * NS_PER_SECOND),
        end_ns=TimestampNs(1_700_100_000 * NS_PER_SECOND),
    )

    symbol, timeframe, start, end = fake.arguments_of("copy_rates_range")
    assert symbol == "EURUSD"
    assert timeframe == 15
    assert start.year == 2023
    assert end > start


@pytest.mark.unit
def test_without_a_range_the_deepest_available_history_is_requested() -> None:
    fake = _terminal()
    adapter = MT5MarketDataAdapter(terminal=fake)

    adapter.load(Symbol("EURUSD"), Timeframe.M15)

    _symbol, _tf, position, count = fake.arguments_of("copy_rates_from_pos")
    assert position == 0
    assert count == MAX_BARS_WITHOUT_RANGE


@pytest.mark.unit
def test_the_end_of_range_never_comes_from_the_clock() -> None:
    """Pedir "hasta hoy" con `now()` haria que dos descargas del mismo rango no
    fueran iguales, y el linaje dejaria de ser reproducible.
    """
    fake = _terminal()
    adapter = MT5MarketDataAdapter(terminal=fake)

    adapter.load(Symbol("EURUSD"), Timeframe.M15, start_ns=TimestampNs(1_700_000_000 * NS_PER_SECOND))
    first_end = fake.arguments_of("copy_rates_range")[3]

    fake.calls.clear()
    adapter.load(Symbol("EURUSD"), Timeframe.M15, start_ns=TimestampNs(1_700_000_000 * NS_PER_SECOND))
    second_end = fake.arguments_of("copy_rates_range")[3]

    assert first_end == second_end


# ---------------------------------------------------------------------------
# Fallos y datos invalidos
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("empty", [None, np.array([], dtype=object)], ids=["none", "vacio"])
def test_no_bars_is_reported_with_the_broker_error(empty: object) -> None:
    fake = _terminal(rates=empty, error=(-2, "Terminal: Invalid params"))
    adapter = MT5MarketDataAdapter(terminal=fake)

    with pytest.raises(DataSourceError) as error:
        adapter.load(Symbol("EURUSD"), Timeframe.M15)

    assert error.value.context["mt5_code"] == -2
    assert "Invalid params" in error.value.context["mt5_detail"]


@pytest.mark.unit
def test_a_timeframe_mt5_does_not_publish_is_rejected() -> None:
    """La traduccion se resuelve por nombre: `Timeframe.M15` busca `TIMEFRAME_M15`.

    Si una version del terminal dejara de publicar una constante, el fallo debe
    decir cual falta y no morir con un `AttributeError` sin contexto.
    """

    class TerminalWithoutTimeframes(FakeMT5):
        def __getattr__(self, name: str) -> int:
            raise AttributeError(name)

    fake = TerminalWithoutTimeframes(rates=rates_from_seconds(1_700_000_000, M15_S, 3))
    adapter = MT5MarketDataAdapter(terminal=fake)

    with pytest.raises(DataSourceError) as error:
        adapter.load(Symbol("EURUSD"), Timeframe.M15)

    assert "marco temporal" in str(error.value)
    assert error.value.context["expected_attribute"] == "TIMEFRAME_M15"


@pytest.mark.unit
def test_a_corrupt_series_from_the_terminal_is_rejected_not_repaired() -> None:
    """El terminal tambien puede entregar basura, y no se arregla en silencio."""
    rates = rates_from_seconds(1_700_000_000, M15_S, 8)
    rates["time"][[3, 4]] = rates["time"][[4, 3]]
    adapter = MT5MarketDataAdapter(terminal=_terminal(rates=rates))

    with pytest.raises(InvariantViolation):
        adapter.load(Symbol("EURUSD"), Timeframe.M15)


@pytest.mark.unit
def test_off_grid_bars_from_the_terminal_are_rejected() -> None:
    rates = rates_from_seconds(1_700_000_000, M15_S, 8)
    rates["time"][4:] += M15_S // 3
    adapter = MT5MarketDataAdapter(terminal=_terminal(rates=rates))

    with pytest.raises(InvariantViolation):
        adapter.load(Symbol("EURUSD"), Timeframe.M15)


# ---------------------------------------------------------------------------
# Inventario
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_available_symbols_is_sorted() -> None:
    """El terminal los devuelve en el orden de su propia base de datos.

    Cualquier artefacto derivado de esa lista dejaria de ser reproducible entre
    maquinas si se conservara.
    """
    adapter = MT5MarketDataAdapter(terminal=_terminal())

    assert [str(s) for s in adapter.available_symbols()] == ["EURUSD", "GBPUSD", "XAUUSD"]


@pytest.mark.unit
def test_listing_symbols_also_connects_first() -> None:
    fake = _terminal()

    MT5MarketDataAdapter(terminal=fake).available_symbols()

    assert fake.called("initialize")


@pytest.mark.unit
def test_a_terminal_without_symbols_yields_an_empty_inventory() -> None:
    fake = _terminal()
    fake.symbols = ()
    adapter = MT5MarketDataAdapter(terminal=fake)

    assert adapter.available_symbols() == ()
