"""`DatasetLayout`: la convencion que impide que lector y escritor divergan.

Su valor no esta en resolver una ruta -eso es una linea- sino en que la resuelva
UNA sola pieza. Antes de existir, el lector derivaba la suya; con el escritor
habrian sido dos implementaciones de la misma convencion, y la primera
discrepancia habria producido un historico escrito que nadie encuentra al leer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.types import Symbol, Timeframe
from app.research.data.layout import SUFFIX, DatasetLayout


@pytest.mark.unit
def test_a_pair_resolves_to_one_artifact(tmp_path: Path) -> None:
    layout = DatasetLayout(root=tmp_path)

    path = layout.path_for(Symbol("EURUSD"), Timeframe.M15)

    assert path == tmp_path / "EURUSD" / f"M15{SUFFIX}"


@pytest.mark.unit
def test_the_path_does_not_depend_on_the_artifact_existing(tmp_path: Path) -> None:
    """Resolver es distinto de encontrar: el escritor necesita la ruta ANTES."""
    layout = DatasetLayout(root=tmp_path)

    assert not layout.path_for(Symbol("NUEVO"), Timeframe.H1).exists()
    assert layout.path_for(Symbol("NUEVO"), Timeframe.H1).name == f"H1{SUFFIX}"


@pytest.mark.unit
def test_different_pairs_never_collide(tmp_path: Path) -> None:
    layout = DatasetLayout(root=tmp_path)
    rutas = {
        layout.path_for(Symbol(s), tf)
        for s in ("EURUSD", "XAUUSD")
        for tf in (Timeframe.M15, Timeframe.H1)
    }

    assert len(rutas) == 4


@pytest.mark.unit
def test_symbols_are_sorted_and_empty_directories_ignored(tmp_path: Path) -> None:
    """El orden se fija en lugar de heredar el del sistema de ficheros.

    En Linux `iterdir` devuelve el orden del directorio, que depende de como se
    creo. Sin fijarlo, un inventario producido en dos maquinas daria dos listas y
    cualquier artefacto derivado dejaria de ser comparable.
    """
    layout = DatasetLayout(root=tmp_path)
    for symbol in ("XAUUSD", "EURUSD", "GBPUSD"):
        target = layout.path_for(Symbol(symbol), Timeframe.M15)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
    (tmp_path / "SIN_DATOS").mkdir()

    assert [str(s) for s in layout.symbols()] == ["EURUSD", "GBPUSD", "XAUUSD"]


@pytest.mark.unit
def test_an_absent_root_is_an_empty_inventory_not_an_error(tmp_path: Path) -> None:
    """Es el estado de un clon recien hecho: `data/` esta vacio a proposito."""
    assert DatasetLayout(root=tmp_path / "no_existe").symbols() == ()


@pytest.mark.unit
def test_timeframes_of_a_symbol_are_listed_sorted(tmp_path: Path) -> None:
    layout = DatasetLayout(root=tmp_path)
    for timeframe in (Timeframe.H1, Timeframe.M15, Timeframe.D1):
        target = layout.path_for(Symbol("EURUSD"), timeframe)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")

    assert list(layout.timeframes_for(Symbol("EURUSD"))) == ["D1", "H1", "M15"]
    assert layout.timeframes_for(Symbol("NOEXISTE")) == ()


@pytest.mark.unit
def test_the_layout_is_immutable(tmp_path: Path) -> None:
    """Cambiar la disposicion a mitad de una corrida partiria el repositorio."""
    layout = DatasetLayout(root=tmp_path)

    with pytest.raises(AttributeError):
        layout.root = tmp_path / "otra"  # type: ignore[misc]
