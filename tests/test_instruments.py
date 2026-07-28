"""Catalogo de instrumentos: carga ansiosa, validacion y aritmetica de contrato.

Se prueba contra `configs/symbols/` REAL, no contra ficheros de prueba. El
motivo: las especificaciones que van a produccion son las que deben estar bien,
y un test sobre ficheros inventados verifica los ficheros inventados. Los casos
de error si usan ficheros temporales, porque un catalogo roto no puede vivir en
el repositorio.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.instruments import REQUIRED_FIELDS, TomlInstrumentCatalog
from app.core.exceptions import ConfigNotFound, ConfigValidationError
from app.core.types import Symbol

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ROOT / "configs" / "symbols"


@pytest.fixture(scope="module")
def catalog() -> TomlInstrumentCatalog:
    return TomlInstrumentCatalog(SYMBOLS)


# ---------------------------------------------------------------------------
# 1. El catalogo real del repositorio
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_the_repository_ships_a_non_empty_catalog(catalog: TomlInstrumentCatalog) -> None:
    """Sin instrumentos no hay `InstrumentCatalogPort` que inyectar."""
    assert len(catalog) >= 2, (
        "Hacen falta al menos dos instrumentos. Con uno solo es imposible "
        "detectar que un motor asumio el `point` o el valor del punto de un "
        "simbolo concreto, que es el acoplamiento que el catalogo evita."
    )


@pytest.mark.contract
@pytest.mark.parametrize("symbol", sorted(p.stem for p in SYMBOLS.glob("*.toml")))
def test_every_shipped_specification_builds_a_valid_instrument(
    catalog: TomlInstrumentCatalog, symbol: str
) -> None:
    """Cada fichero produce un `Instrument` que pasa sus propias invariantes."""
    instrument = catalog.get(Symbol(symbol))
    assert str(instrument.symbol) == symbol
    assert instrument.point > 0
    assert instrument.value_per_point_per_lot > 0
    assert 0 < instrument.min_lot <= instrument.max_lot


@pytest.mark.contract
@pytest.mark.parametrize("symbol", sorted(p.stem for p in SYMBOLS.glob("*.toml")))
def test_shipped_instruments_differ_in_the_magnitudes_that_matter(
    catalog: TomlInstrumentCatalog, symbol: str
) -> None:
    """Ningun instrumento repite la combinacion (point, digits) de otro.

    Es lo que da valor a tener dos: si compartieran escala, una constante
    escondida en un motor pasaria desapercibida al cambiar de simbolo.
    """
    others = [s for s in catalog.available_symbols() if s != symbol]
    mine = catalog.get(Symbol(symbol))
    for other in others:
        theirs = catalog.get(Symbol(other))
        assert (mine.point, mine.digits) != (theirs.point, theirs.digits), (
            f"{symbol} y {other} comparten escala de cotizacion"
        )


@pytest.mark.unit
def test_symbols_are_listed_in_stable_order(catalog: TomlInstrumentCatalog) -> None:
    assert catalog.available_symbols() == sorted(catalog.available_symbols())


@pytest.mark.unit
def test_membership_and_size(catalog: TomlInstrumentCatalog) -> None:
    assert "EURUSD" in catalog
    assert "NOEXISTE" not in catalog
    assert len(catalog) == len(catalog.available_symbols())


# ---------------------------------------------------------------------------
# 2. Aritmetica de contrato
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_points_and_money_conversions_are_consistent(
    catalog: TomlInstrumentCatalog,
) -> None:
    """100 puntos por 0.5 lotes valen 100 x valor_del_punto x 0.5."""
    instrument = catalog.get(Symbol("EURUSD"))
    price_move = instrument.points_to_price(100)
    money = instrument.money_from_price_move(price_move, 0.5)
    assert money == pytest.approx(100 * instrument.value_per_point_per_lot * 0.5)
    assert instrument.price_to_points(price_move) == pytest.approx(100)


@pytest.mark.unit
def test_lots_are_truncated_never_rounded_up(catalog: TomlInstrumentCatalog) -> None:
    """Redondear al alza puede superar un limite de riesgo ya calculado.

    Un tamano ligeramente menor nunca rompe un limite; uno mayor si.
    """
    instrument = catalog.get(Symbol("EURUSD"))
    assert instrument.round_lots(0.037) == pytest.approx(0.03)
    assert instrument.round_lots(0.0399) == pytest.approx(0.03)


@pytest.mark.unit
def test_a_size_below_the_minimum_becomes_zero(catalog: TomlInstrumentCatalog) -> None:
    """Cero obliga al llamante a tratar "no se puede operar con este riesgo".

    La alternativa -subir al minimo- arriesgaria mas de lo que la politica de
    riesgo autorizo, en silencio.
    """
    instrument = catalog.get(Symbol("EURUSD"))
    assert instrument.round_lots(0.004) == 0.0
    assert instrument.round_lots(0.0) == 0.0


@pytest.mark.unit
def test_lots_are_capped_at_the_broker_maximum(catalog: TomlInstrumentCatalog) -> None:
    instrument = catalog.get(Symbol("EURUSD"))
    assert instrument.round_lots(10_000.0) == instrument.max_lot


# ---------------------------------------------------------------------------
# 3. Errores
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_symbol_lists_what_is_available(catalog: TomlInstrumentCatalog) -> None:
    """El fallo casi siempre es una errata o un sufijo de broker (`EURUSD.pro`)."""
    with pytest.raises(ConfigNotFound) as raised:
        catalog.get(Symbol("GBPJPY"))
    assert "EURUSD" in str(raised.value)


@pytest.mark.unit
@pytest.mark.parametrize("omitted", REQUIRED_FIELDS)
def test_an_incomplete_specification_fails_at_startup(
    tmp_path: Path, omitted: str
) -> None:
    """Carga ansiosa: un campo ausente rompe al arrancar, no en la barra 4000.

    Ninguno admite valor por defecto. Un `value_per_point_per_lot` asumido
    produce un dimensionamiento equivocado que no se nota hasta que la posicion
    es diez veces mayor de lo previsto.
    """
    complete = {
        "point": 0.00001,
        "digits": 5,
        "contract_size": 100000.0,
        "value_per_point_per_lot": 1.0,
        "min_lot": 0.01,
        "max_lot": 100.0,
        "lot_step": 0.01,
        "margin_per_lot": 3333.33,
    }
    body = "\n".join(f"{k} = {v}" for k, v in complete.items() if k != omitted)
    (tmp_path / "TESTSYM.toml").write_text(f"[instrument]\n{body}\n", encoding="utf-8")

    with pytest.raises(ConfigValidationError) as raised:
        TomlInstrumentCatalog(tmp_path)
    assert omitted in str(raised.value)


@pytest.mark.unit
def test_an_unreadable_specification_names_the_file(tmp_path: Path) -> None:
    """"min_lot invalido" sin decir en cual de veinte simbolos no ayuda a nadie."""
    (tmp_path / "ROTO.toml").write_text("[[[ no es toml", encoding="utf-8")
    with pytest.raises(ConfigValidationError) as raised:
        TomlInstrumentCatalog(tmp_path)
    assert "ROTO" in str(raised.value)


@pytest.mark.unit
def test_domain_invariants_are_enforced_through_the_catalog(tmp_path: Path) -> None:
    """La validacion real la hace `Instrument`; el catalogo no la duplica."""
    from app.core.exceptions import InvariantViolation

    (tmp_path / "BAD.toml").write_text(
        "[instrument]\npoint = 0.0\ndigits = 5\ncontract_size = 1.0\n"
        "value_per_point_per_lot = 1.0\nmin_lot = 0.01\nmax_lot = 1.0\n"
        "lot_step = 0.01\nmargin_per_lot = 1.0\n",
        encoding="utf-8",
    )
    with pytest.raises(InvariantViolation):
        TomlInstrumentCatalog(tmp_path)


@pytest.mark.unit
def test_an_absent_directory_yields_an_empty_catalog(tmp_path: Path) -> None:
    """Arrancar sin catalogo es legitimo; `qp doctor` lo reporta como aviso."""
    empty = TomlInstrumentCatalog(tmp_path / "no-existe")
    assert len(empty) == 0
    assert empty.available_symbols() == []


@pytest.mark.unit
def test_costs_default_to_zero_which_is_the_optimistic_hypothesis(
    tmp_path: Path,
) -> None:
    """Un coste omitido hace el backtest MAS optimista, no menos.

    Es la direccion correcta del error: un resultado que no sobrevive ni
    siquiera sin costes no merece mas analisis.
    """
    (tmp_path / "NOCOST.toml").write_text(
        "[instrument]\npoint = 0.01\ndigits = 2\ncontract_size = 1.0\n"
        "value_per_point_per_lot = 1.0\nmin_lot = 0.01\nmax_lot = 1.0\n"
        "lot_step = 0.01\nmargin_per_lot = 1.0\n",
        encoding="utf-8",
    )
    instrument = TomlInstrumentCatalog(tmp_path).get(Symbol("NOCOST"))
    assert instrument.costs.commission_per_lot == 0.0
    assert instrument.costs.spread_points == 0.0


__all__: list[str] = []
