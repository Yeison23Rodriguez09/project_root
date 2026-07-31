"""Adaptador de `MarketDataPort` sobre el terminal de MetaTrader 5.

Vive en `broker` y no en `research.data` porque `configs/architecture.toml`
prohibe `MetaTrader5` en el paquete `research`: un motor que pudiera hablar con
un terminal dejaria de ser reproducible sin que nada lo delatara. El adaptador de
fichero y este implementan el MISMO puerto desde paquetes distintos, que es el
cuadro hexagonal correcto.

Responsabilidades, y ni una mas (ADR-0011):

    conectar -> verificar terminal -> verificar login -> listar simbolos
             -> descargar OHLCV -> convertir a Bars -> entregar

NO guarda nada. NO valida mas alla de lo que `Bars` exige. NO normaliza, no
deduplica y no decide que rango pedir: eso lo orquesta el servicio de
almacenamiento.

El modulo `MetaTrader5` se INYECTA en lugar de importarse en cabecera. Dos
motivos: el paquete solo existe en Windows y con el terminal instalado, de modo
que un import de cabecera impediria hasta leer este contrato en Linux; y un
terminal inyectable es lo que permite probar el adaptador con un doble, que es
obligatorio porque `configs/runtime.toml` exige `require_synthetic_data_only` en
modo `ci`: el pipeline no puede hablar nunca con un broker real.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

from app.core.exceptions import DataSourceError
from app.core.types import NS_PER_SECOND, Symbol, Timeframe, TimestampNs
from app.domain.entities.bars import Bars

#: Barras a pedir cuando no se acota el rango. MT5 devuelve como mucho lo que el
#: terminal tenga descargado, asi que esto es un techo y no una promesa: pedir la
#: "maxima profundidad" es pedir mucho y quedarse con lo que haya.
MAX_BARS_WITHOUT_RANGE = 200_000

#: Columnas del array estructurado que devuelve `copy_rates_*`.
RATE_TIME = "time"
RATE_VOLUME = "tick_volume"


@dataclass(frozen=True, slots=True)
class TerminalStatus:
    """Estado observado del terminal, para diagnostico y para `qp download`.

    Se devuelve como dato en lugar de imprimirse: quien decide como mostrarlo es
    la interfaz, y quien decide si es suficiente para operar es el servicio.
    """

    connected: bool
    login: int | None
    server: str
    trade_allowed: bool
    terminal_path: str
    symbols_total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "login": self.login,
            "server": self.server,
            "trade_allowed": self.trade_allowed,
            "terminal_path": self.terminal_path,
            "symbols_total": self.symbols_total,
        }


class MT5MarketDataAdapter:
    """Historicos leidos del terminal de MetaTrader 5.

    No recibe credenciales y no las pedira nunca: se conecta al terminal que YA
    tiene sesion iniciada. La autenticacion es del operador, no del software, y
    un adaptador que aceptara usuario y contrasena convertiria cada fichero de
    configuracion y cada log en un sitio donde pueden filtrarse.
    """

    def __init__(self, terminal: Any | None = None) -> None:
        self._terminal = terminal
        self._connected = False

    # -- ciclo de conexion ---------------------------------------------------

    @property
    def terminal(self) -> Any:
        """Modulo `MetaTrader5`, importado de forma perezosa si no se inyecto."""
        if self._terminal is None:
            try:
                import MetaTrader5
            except ImportError as exc:
                raise DataSourceError(
                    "MetaTrader5 no esta disponible: instala el extra 'mt5' en Windows",
                    cause=str(exc),
                ) from exc
            self._terminal = MetaTrader5
        return self._terminal

    def connect(self) -> TerminalStatus:
        """Abre la conexion con el terminal y comprueba que es utilizable.

        Comprueba TRES cosas distintas, y separarlas importa porque el
        diagnostico es distinto en cada caso: que el terminal responda, que haya
        una sesion iniciada, y que el terminal se considere conectado a su
        servidor. Un terminal abierto sin cuenta responde a `initialize` y no
        sirve para nada.

        Raises:
            DataSourceError: el terminal no responde, no hay sesion iniciada o
                no esta conectado al servidor del broker.
        """
        if not self.terminal.initialize():
            code, detail = self._last_error()
            raise DataSourceError(
                "No se pudo inicializar el terminal de MetaTrader 5",
                mt5_code=code,
                mt5_detail=detail,
            )
        self._connected = True

        status = self.status()
        if status.login is None:
            self.disconnect()
            raise DataSourceError(
                "El terminal no tiene sesion iniciada: inicia sesion en MetaTrader 5",
                terminal_path=status.terminal_path,
            )
        if not status.connected:
            self.disconnect()
            raise DataSourceError(
                "El terminal no esta conectado a su servidor",
                server=status.server,
                login=status.login,
            )
        return status

    def disconnect(self) -> None:
        """Cierra la conexion. Idempotente: parar dos veces no es un error."""
        if self._connected:
            self.terminal.shutdown()
            self._connected = False

    def __enter__(self) -> MT5MarketDataAdapter:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.disconnect()

    def status(self) -> TerminalStatus:
        """Fotografia del terminal y de la cuenta, sin lanzar."""
        terminal_info = self.terminal.terminal_info()
        account_info = self.terminal.account_info()
        symbols_total = self.terminal.symbols_total()
        return TerminalStatus(
            connected=bool(getattr(terminal_info, "connected", False)),
            login=int(account_info.login) if account_info is not None else None,
            server=str(getattr(account_info, "server", "")) if account_info else "",
            trade_allowed=bool(getattr(terminal_info, "trade_allowed", False)),
            terminal_path=str(getattr(terminal_info, "path", "")),
            symbols_total=int(symbols_total or 0),
        )

    # -- MarketDataPort ------------------------------------------------------

    def available_symbols(self) -> Sequence[Symbol]:
        """Simbolos publicados por el broker, en orden alfabetico.

        El orden se fija por la misma razon que en el adaptador de fichero: el
        terminal los devuelve en el orden de su propia base de datos y cualquier
        artefacto derivado dejaria de ser reproducible.
        """
        raw = self.terminal.symbols_get()
        if raw is None:
            return ()
        return tuple(sorted(Symbol(str(item.name)) for item in raw))

    def load(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        *,
        start_ns: TimestampNs | None = None,
        end_ns: TimestampNs | None = None,
    ) -> Bars:
        """Descarga el rango pedido y lo entrega como `Bars` validado.

        Sin rango se piden las ultimas `MAX_BARS_WITHOUT_RANGE` barras
        disponibles, que es lo mas parecido a "maxima profundidad" que el
        terminal ofrece: MT5 solo entrega lo que tiene descargado.

        Las invariantes las hace cumplir `Bars`. Si el terminal devuelve una
        serie desordenada o fuera de rejilla, esto FALLA en lugar de repararla:
        un historico corrupto que parece sano produce un backtest que corre y
        miente.

        Raises:
            DataSourceError: el simbolo no existe, el terminal no devolvio datos
                o el marco temporal no tiene equivalente en MT5.
        """
        native_timeframe = self._native_timeframe(timeframe)
        rates = self._copy_rates(symbol, native_timeframe, start_ns, end_ns)

        if rates is None or len(rates) == 0:
            code, detail = self._last_error()
            raise DataSourceError(
                "El terminal no devolvio barras para el par pedido",
                symbol=str(symbol),
                timeframe=str(timeframe),
                mt5_code=code,
                mt5_detail=detail,
            )

        # MT5 publica el instante de APERTURA en segundos. La conversion a
        # nanosegundos es exacta y no introduce redondeo.
        #
        # ADVERTENCIA que R2B debe verificar contra un terminal real: ese
        # instante viene expresado en la zona horaria del SERVIDOR del broker, no
        # necesariamente en UTC. Aqui no se aplica ninguna correccion porque
        # inventarla sin medirla desplazaria toda la serie, y un desplazamiento
        # uniforme no rompe ninguna invariante de `Bars`: pasaria inadvertido y
        # contaminaria cada backtest posterior. Se documenta y se mide.
        timestamp = np.asarray(rates[RATE_TIME], dtype=np.int64) * NS_PER_SECOND

        return Bars.from_arrays(
            symbol=str(symbol),
            timeframe=timeframe,
            timestamp=timestamp,
            open=np.asarray(rates["open"], dtype=np.float64),
            high=np.asarray(rates["high"], dtype=np.float64),
            low=np.asarray(rates["low"], dtype=np.float64),
            close=np.asarray(rates["close"], dtype=np.float64),
            volume=np.asarray(rates[RATE_VOLUME], dtype=np.float64),
        )

    # -- interno -------------------------------------------------------------

    def _native_timeframe(self, timeframe: Timeframe) -> int:
        """Traduce el marco temporal del dominio al entero de MT5.

        Se resuelve por NOMBRE y no con una tabla propia: `Timeframe.M15` busca
        `TIMEFRAME_M15`. Una tabla duplicada aqui tendria que mantenerse en
        paralelo a la del dominio y discreparia en el primer marco que se anada.
        """
        attribute = f"TIMEFRAME_{timeframe.name}"
        native = getattr(self.terminal, attribute, None)
        if native is None:
            raise DataSourceError(
                "MetaTrader 5 no publica un equivalente de este marco temporal",
                timeframe=str(timeframe),
                expected_attribute=attribute,
            )
        return int(native)

    def _copy_rates(
        self,
        symbol: Symbol,
        native_timeframe: int,
        start_ns: TimestampNs | None,
        end_ns: TimestampNs | None,
    ) -> Any:
        if start_ns is None and end_ns is None:
            return self.terminal.copy_rates_from_pos(
                str(symbol), native_timeframe, 0, MAX_BARS_WITHOUT_RANGE
            )
        return self.terminal.copy_rates_range(
            str(symbol),
            native_timeframe,
            self._as_datetime(start_ns, default=0),
            self._as_datetime(end_ns, default=None),
        )

    @staticmethod
    def _as_datetime(value: TimestampNs | None, *, default: int | None) -> datetime:
        """Convierte nanosegundos a `datetime` UTC, que es lo que MT5 espera.

        `default=None` significa "hasta el final del historico disponible"; se
        traduce a un instante muy lejano en lugar de a `now()`, porque llamar al
        reloj aqui haria que dos descargas del mismo rango no fueran iguales.
        """
        if value is not None:
            return datetime.fromtimestamp(int(value) / NS_PER_SECOND, tz=UTC)
        if default is None:
            return datetime(2100, 1, 1, tzinfo=UTC)
        return datetime.fromtimestamp(default, tz=UTC)

    def _last_error(self) -> tuple[int, str]:
        try:
            code, detail = self.terminal.last_error()
        except (AttributeError, TypeError, ValueError):  # pragma: no cover
            return (0, "")
        return int(code), str(detail)


__all__ = ["MAX_BARS_WITHOUT_RANGE", "MT5MarketDataAdapter", "TerminalStatus"]
