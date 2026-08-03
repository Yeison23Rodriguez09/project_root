"""Catalogo de instrumentos leido de `configs/symbols/`. Implementa `InstrumentCatalogPort`.

Vive en `app/config` y no en `storage` porque este paquete es, por contrato, el
unico autorizado a abrir un fichero de configuracion (ADR-0005), y una
especificacion de instrumento ES configuracion: el `point` de EURUSD, el
`lot_step` del broker y el modelo de costes son parametros de negocio, y la
regla 4.5 de CLAUDE.md prohibe que esten en el codigo.

La consecuencia arquitectonica importa: ningun motor puede importar este modulo
-`config` es nivel 4 y los motores son nivel 3-. Un motor recibe `Instrument` ya
construido a traves de `InstrumentCatalogPort`, que `container` inyecta. Si un
motor pudiera leer el fichero, el resultado dejaria de depender solo de (datos,
configuracion, semilla) y pasaria a depender del estado del disco.

Carga ansiosa. El catalogo entero se lee al construirse, no bajo demanda. Un
simbolo mal escrito en una especificacion debe fallar al arrancar, no en la
barra 4000 de un backtest nocturno.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.core.exceptions import ConfigNotFound, ConfigValidationError
from app.core.types import Symbol
from app.domain.value_objects.instrument import CostModel, Instrument

#: Campos que toda especificacion debe declarar. No hay valores por defecto para
#: ninguno: un `value_per_point_per_lot` asumido produce un dimensionamiento
#: silenciosamente equivocado, y ese error no se nota hasta que la posicion es
#: diez veces mayor de lo previsto.
REQUIRED_FIELDS: tuple[str, ...] = (
    "point",
    "digits",
    "contract_size",
    "value_per_point_per_lot",
    "min_lot",
    "max_lot",
    "lot_step",
    "margin_per_lot",
)

#: Campos del modelo de costes. Estos SI admiten defecto cero, porque un coste
#: nulo es una hipotesis explicita y conservadora en el sentido correcto: hace el
#: backtest MAS optimista, de modo que un resultado que no sobrevive ni siquiera
#: sin costes no merece mas analisis.
COST_FIELDS: tuple[str, ...] = (
    "commission_per_lot",
    "spread_points",
    "slippage_points",
    "slippage_atr_multiple",
    # Cuarto componente desde ADR-0010, y el unico con signo: un carry favorable
    # se representa en negativo. Aparece aqui y no solo en `CostModel` porque
    # esta tupla es la que gobierna la construccion; un campo declarado en el
    # TOML de un simbolo y ausente de esta lista se ignoraria en silencio, que
    # es peor que no admitirlo.
    "financing_per_lot_per_day",
)


def _build_instrument(symbol: str, spec: Mapping[str, Any], source: Path) -> Instrument:
    """Convierte una especificacion TOML en un `Instrument` validado.

    Raises:
        ConfigValidationError: si falta un campo obligatorio o el objeto de
            dominio rechaza los valores. La validacion real la hace `Instrument`
            en su `__post_init__`; aqui solo se comprueba la presencia y se
            envuelve el fallo con la ruta del fichero, porque "min_lot invalido"
            sin decir en cual de los veinte simbolos no ayuda a nadie.
    """
    missing = [name for name in REQUIRED_FIELDS if name not in spec]
    if missing:
        raise ConfigValidationError(
            "Especificacion de instrumento incompleta",
            symbol=symbol,
            path=str(source),
            missing=missing,
        )
    costs = CostModel(**{name: float(spec.get(name, 0.0)) for name in COST_FIELDS})
    return Instrument(
        symbol=Symbol(symbol),
        point=float(spec["point"]),
        digits=int(spec["digits"]),
        contract_size=float(spec["contract_size"]),
        value_per_point_per_lot=float(spec["value_per_point_per_lot"]),
        min_lot=float(spec["min_lot"]),
        max_lot=float(spec["max_lot"]),
        lot_step=float(spec["lot_step"]),
        margin_per_lot=float(spec["margin_per_lot"]),
        costs=costs,
        quote_currency=str(spec.get("quote_currency", "USD")),
    )


class TomlInstrumentCatalog:
    """Instrumentos leidos de `configs/symbols/*.toml`.

    Implementa `app.shared.ports.InstrumentCatalogPort`.

    Un fichero por simbolo, y el nombre del fichero ES el simbolo. Se prefiere a
    un unico `symbols.toml` porque las especificaciones las mantiene gente
    distinta en momentos distintos, y un fichero compartido genera conflictos de
    fusion donde no hay conflicto real.
    """

    __slots__ = ("_directory", "_instruments")

    def __init__(self, directory: Path | str) -> None:
        self._directory = Path(directory)
        self._instruments: dict[str, Instrument] = self._load_all()

    def _load_all(self) -> dict[str, Instrument]:
        """Lee el directorio completo en orden alfabetico estable."""
        if not self._directory.is_dir():
            return {}
        loaded: dict[str, Instrument] = {}
        for path in sorted(self._directory.glob("*.toml")):
            symbol = path.stem
            try:
                spec = tomllib.loads(path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:
                raise ConfigValidationError(
                    "Especificacion de instrumento ilegible",
                    symbol=symbol,
                    path=str(path),
                    detail=str(exc),
                ) from exc
            loaded[symbol] = _build_instrument(symbol, spec.get("instrument", spec), path)
        return loaded

    # -- InstrumentCatalogPort -----------------------------------------------

    def get(self, symbol: Symbol) -> Instrument:
        """Especificacion de un instrumento.

        Raises:
            ConfigNotFound: si no hay fichero para ese simbolo. El error lista
                los disponibles: el fallo casi siempre es una errata o un simbolo
                con sufijo de broker (`EURUSD.pro`), y ver la lista lo resuelve
                sin abrir el directorio.
        """
        instrument = self._instruments.get(str(symbol))
        if instrument is None:
            raise ConfigNotFound(
                "No hay especificacion para el instrumento",
                symbol=str(symbol),
                directory=str(self._directory),
                available=self.available_symbols(),
            )
        return instrument

    # -- consulta ------------------------------------------------------------

    def available_symbols(self) -> Sequence[str]:
        """Simbolos con especificacion, en orden alfabetico estable."""
        return sorted(self._instruments)

    def __contains__(self, symbol: object) -> bool:
        return str(symbol) in self._instruments

    def __len__(self) -> int:
        return len(self._instruments)

    def describe(self) -> dict[str, Any]:
        """Resumen para `qp doctor` y `qp preflight`."""
        return {
            "directory": str(self._directory),
            "count": len(self._instruments),
            "symbols": self.available_symbols(),
        }

    def __repr__(self) -> str:
        return f"TomlInstrumentCatalog({len(self._instruments)} instrumentos)"


__all__ = ["COST_FIELDS", "REQUIRED_FIELDS", "TomlInstrumentCatalog"]
