"""Adaptador de `MarketDataPort` sobre historicos en Parquet.

Cuatro responsabilidades y ni una mas:

    leer -> parsear -> construir Bars -> entregar Bars

Lo que este adaptador NO hace, y la lista importa tanto como lo que hace:

    no rellena huecos        no interpola       no elimina outliers
    no repara datos          no ordena filas    no corrige timestamps

Si el historico es invalido, el adaptador FALLA. Las invariantes las hace
cumplir `Bars`, y una fuente que entrega filas desordenadas esta rota: ordenarlas
en silencio convertiria un fichero corrupto en uno que parece sano, y el error
reaparecería mas tarde como un resultado inexplicable en lugar de como una
excepcion con nombre. El puerto ya lo declara: "si no puede garantizar las
invariantes, debe fallar en lugar de entregar datos dudosos".

La calidad estadistica -cuantos huecos hay, de que tamano, si hay precios
congelados- no es asunto de este fichero. Pertenece a los validadores de
investigacion, que INFORMAN en lugar de lanzar. Aqui solo se distingue entre
"esto es una serie" y "esto no lo es".

Vive en `research.data` porque es donde el contrato lo situa: el docstring del
paquete declara "ingesta, normalizacion y validacion de series". El adaptador de
MT5 NO podra vivir aqui: `architecture.toml` prohibe `MetaTrader5` en `research`,
y por eso ira a `broker`. Dos adaptadores del mismo puerto en paquetes distintos
es el cuadro hexagonal correcto, y la matriz ya lo anticipaba.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from app.core.exceptions import DataIntegrityError, DataSourceError
from app.core.types import Symbol, Timeframe, TimestampNs
from app.domain.entities.bars import Bars
from app.research.data.layout import DatasetLayout

#: Columnas que todo fichero debe traer. `volume` no entra: hay proveedores que
#: no lo publican, y `Bars` admite su ausencia con ceros. Un volumen inventado
#: seria peor que uno ausente, asi que no se rellena con nada mas.
REQUIRED_COLUMNS: tuple[str, ...] = ("timestamp", "open", "high", "low", "close")

#: Columna opcional, tratada aparte por lo anterior.
OPTIONAL_COLUMNS: tuple[str, ...] = ("volume",)

class ParquetMarketData:
    """Historicos en disco, segun la disposicion que se le inyecta.

    NO decide donde estan los ficheros: se lo pregunta al `DatasetLayout`, el
    mismo objeto que consulta `ParquetMarketDataWriter`. Antes derivaba la ruta
    por su cuenta, y con la llegada del escritor eso habria significado dos
    implementaciones de la misma convencion divergiendo en el primer cambio: el
    sintoma seria un historico escrito que nadie encuentra al leer (ADR-0011).
    """

    def __init__(self, layout: DatasetLayout) -> None:
        self._layout = layout

    # -- MarketDataPort -----------------------------------------------------

    def available_symbols(self) -> Sequence[Symbol]:
        """Simbolos con al menos un historico, en orden alfabetico.

        El inventario lo resuelve el layout: saber QUE hay es conocimiento de la
        disposicion, y cambiar a particionado por fecha cambiaria como se
        enumera sin que el lector deba enterarse.
        """
        return self._layout.symbols()

    def load(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        *,
        start_ns: TimestampNs | None = None,
        end_ns: TimestampNs | None = None,
    ) -> Bars:
        """Serie validada para el rango pedido.

        El recorte por rango es una SELECCION, no una reparacion: se entregan
        las barras que caen dentro y no se sintetiza ninguna que falte. Un rango
        sin datos devuelve una serie vacia, que es un hecho representable y
        distinto de un error.

        Raises:
            DataSourceError: no existe el fichero del par pedido.
            DataIntegrityError: el fichero existe pero no es una serie -faltan
                columnas, longitudes distintas, tipos no numericos-.
            InvariantViolation: la serie viola el contrato de `Bars` -desorden,
                OHLC incoherente, fuera de rejilla-. Se deja propagar: es el
                dominio rechazando datos que no deberian existir, y envolverla
                aqui solo enterraria el diagnostico.
        """
        path = self._path_for(symbol, timeframe)
        columns = self._read_columns(path, symbol, timeframe)

        timestamp = self._column(columns, "timestamp", path, dtype=np.int64)
        selected = self._range_mask(timestamp, start_ns, end_ns)

        return Bars.from_arrays(
            symbol=str(symbol),
            timeframe=timeframe,
            timestamp=timestamp[selected],
            open=self._column(columns, "open", path)[selected],
            high=self._column(columns, "high", path)[selected],
            low=self._column(columns, "low", path)[selected],
            close=self._column(columns, "close", path)[selected],
            volume=(
                self._column(columns, "volume", path)[selected]
                if "volume" in columns
                else None
            ),
        )

    # -- interno ------------------------------------------------------------

    def _path_for(self, symbol: Symbol, timeframe: Timeframe) -> Path:
        path = self._layout.path_for(symbol, timeframe)
        if not path.is_file():
            raise DataSourceError(
                "No hay historico para el par pedido",
                symbol=str(symbol),
                timeframe=str(timeframe),
                path=str(path),
            )
        return path

    @staticmethod
    def _read_columns(path: Path, symbol: Symbol, timeframe: Timeframe) -> dict[str, Any]:
        """Lee el fichero y devuelve sus columnas, sin interpretarlas.

        `pyarrow` se importa aqui y no arriba a proposito: el modulo debe poder
        importarse -y su contrato leerse- en un entorno que no lo tenga. Un
        `ImportError` al cargar el paquete impediria hasta listar simbolos.
        """
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise DataSourceError(
                "Leer historicos en Parquet requiere pyarrow",
                path=str(path),
                cause=str(exc),
            ) from exc

        try:
            table = pq.read_table(path)
        except OSError as exc:
            raise DataSourceError(
                "El historico no se pudo leer",
                symbol=str(symbol),
                timeframe=str(timeframe),
                path=str(path),
                cause=str(exc),
            ) from exc

        missing = [name for name in REQUIRED_COLUMNS if name not in table.column_names]
        if missing:
            raise DataIntegrityError(
                "Al historico le faltan columnas obligatorias",
                path=str(path),
                missing=missing,
                found=sorted(table.column_names),
            )
        return {
            name: table.column(name)
            for name in (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS)
            if name in table.column_names
        }

    @staticmethod
    def _column(
        columns: dict[str, Any], name: str, path: Path, *, dtype: Any = np.float64
    ) -> np.ndarray:
        """Convierte una columna a array numpy del tipo esperado.

        Un tipo no convertible es un fallo de INTEGRIDAD y no de formato: el
        fichero se leyo bien y su contenido no es una serie de precios.
        """
        try:
            return np.asarray(columns[name].to_numpy(zero_copy_only=False), dtype=dtype)
        except (ValueError, TypeError) as exc:
            raise DataIntegrityError(
                "Una columna del historico no es numerica",
                path=str(path),
                column=name,
                expected=str(np.dtype(dtype)),
                cause=str(exc),
            ) from exc

    @staticmethod
    def _range_mask(
        timestamp: np.ndarray, start_ns: TimestampNs | None, end_ns: TimestampNs | None
    ) -> np.ndarray:
        """Mascara del rango pedido, ambos extremos incluidos.

        Cerrado por los dos lados porque `timestamp[i]` es la APERTURA de la
        barra: pedir hasta `end_ns` y excluirla dejaria fuera la barra que abre
        exactamente en ese instante, que es la que el llamante espera recibir al
        pedir un rango que termina ahi.
        """
        mask = np.ones(timestamp.shape, dtype=bool)
        if start_ns is not None:
            mask &= timestamp >= int(start_ns)
        if end_ns is not None:
            mask &= timestamp <= int(end_ns)
        return mask


__all__ = ["OPTIONAL_COLUMNS", "REQUIRED_COLUMNS", "ParquetMarketData"]
