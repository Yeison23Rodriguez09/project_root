"""Repositorio de artefactos: atomicidad, confinamiento y estabilidad de bytes.

Las tres propiedades que se comprueban aqui no son de comodidad. La atomicidad
evita que una corrida interrumpida deje un JSON truncado que el siguiente lector
diagnostica como corrupcion del productor. El confinamiento impide que un nombre
que venga de configuracion escriba fuera del area de artefactos. Y la estabilidad
de bytes es P1 aplicado a la persistencia: sin ella, dos corridas identicas
producen ficheros que `diff` marca como distintos.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.exceptions import DataIntegrityError, StorageError
from app.storage.artifacts.store import FileArtifactStore


@pytest.fixture
def store(tmp_path: Path) -> FileArtifactStore:
    return FileArtifactStore(tmp_path / "artifacts")


# ---------------------------------------------------------------------------
# 1. Escritura y lectura
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_root_is_created_on_construction(tmp_path: Path) -> None:
    """Un permiso mal configurado se descubre al arrancar, no al terminar.

    Si la raiz se creara en la primera escritura, el fallo aparecerian al final
    de una corrida larga, que es cuando ya no se puede hacer nada con el.
    """
    target = tmp_path / "nueva" / "raiz"
    assert not target.exists()
    FileArtifactStore(target)
    assert target.is_dir()


@pytest.mark.unit
def test_write_and_read_roundtrip(store: FileArtifactStore) -> None:
    payload = {"sharpe": 1.23, "n_trades": 41, "degenerate": False, "tag": None}
    store.write_json("run/metrics.json", payload)
    assert dict(store.read_json("run/metrics.json")) == payload


@pytest.mark.unit
def test_nested_paths_are_created(store: FileArtifactStore) -> None:
    written = store.write_json("a/b/c/deep.json", {"ok": True})
    assert Path(written).is_file()
    assert store.exists("a/b/c/deep.json")


@pytest.mark.unit
def test_output_is_byte_stable_regardless_of_key_order(store: FileArtifactStore) -> None:
    """Mismo contenido, mismos bytes. Es lo que hace comparables dos corridas."""
    store.write_json("a.json", {"zeta": 1, "alpha": 2, "mid": 3})
    store.write_json("b.json", {"mid": 3, "alpha": 2, "zeta": 1})
    assert (store.root / "a.json").read_bytes() == (store.root / "b.json").read_bytes()


@pytest.mark.unit
def test_no_partial_file_survives_a_successful_write(store: FileArtifactStore) -> None:
    """El renombrado atomico no deja rastro del temporal."""
    store.write_json("run/metrics.json", {"a": 1})
    leftovers = [p.name for p in store.root.rglob("*.partial")]
    assert not leftovers, f"quedaron ficheros a medias: {leftovers}"


@pytest.mark.unit
def test_overwrite_replaces_the_whole_artifact(store: FileArtifactStore) -> None:
    """Sin mezcla con el contenido anterior."""
    store.write_json("m.json", {"viejo": 1, "comun": 1})
    store.write_json("m.json", {"comun": 2})
    assert dict(store.read_json("m.json")) == {"comun": 2}


# ---------------------------------------------------------------------------
# 2. Tablas
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_table_is_json_lines_so_it_streams(store: FileArtifactStore) -> None:
    """Un objeto por linea: una tabla de millones de filas se lee en flujo.

    Un array unico obligaria a cargarla entera para ver la primera fila, y un
    fichero truncado seria ilegible por completo en vez de legible hasta la
    ultima linea completa.
    """
    rows = [{"id": 1, "pnl": 1.5}, {"id": 2, "pnl": -0.5}, {"id": 3, "pnl": 0.0}]
    store.write_table("run/trades.jsonl", rows)

    lines = (store.root / "run" / "trades.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert [json.loads(line) for line in lines] == rows


@pytest.mark.unit
def test_empty_table_writes_an_empty_file_not_nothing(store: FileArtifactStore) -> None:
    """Cero operaciones es un resultado, y debe distinguirse de "no se escribio"."""
    store.write_table("run/trades.jsonl", [])
    assert store.exists("run/trades.jsonl")
    assert (store.root / "run" / "trades.jsonl").read_bytes() == b""


# ---------------------------------------------------------------------------
# 3. Confinamiento
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "escape",
    ["../fuera.json", "a/../../fuera.json", "sub/../../../fuera.json"],
)
def test_paths_cannot_escape_the_root(store: FileArtifactStore, escape: str) -> None:
    """La comprobacion es sobre la ruta resuelta, no sobre la cadena.

    Filtrar `..` textualmente dejaria pasar enlaces simbolicos y rutas
    absolutas, que son los dos casos que de verdad interesa cerrar.
    """
    with pytest.raises(StorageError):
        store.write_json(escape, {"malicioso": True})


@pytest.mark.unit
def test_absolute_paths_are_refused(store: FileArtifactStore, tmp_path: Path) -> None:
    with pytest.raises(StorageError):
        store.write_json(str(tmp_path / "absoluto.json"), {})


# ---------------------------------------------------------------------------
# 4. Errores con el tipo correcto
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unserializable_payload_is_an_integrity_error(store: FileArtifactStore) -> None:
    """El disco esta bien; lo que esta mal es lo que se le pidio guardar.

    Confundirlo con `StorageError` haria que un artefacto corrupto se
    investigara como un problema de infraestructura, que es donde no esta.
    """
    with pytest.raises(DataIntegrityError):
        store.write_json("bad.json", {"vivo": object()})


@pytest.mark.unit
def test_reading_a_missing_artifact_is_a_storage_error(store: FileArtifactStore) -> None:
    with pytest.raises(StorageError):
        store.read_json("no/existe.json")


@pytest.mark.unit
def test_corrupt_json_is_an_integrity_error(store: FileArtifactStore) -> None:
    """Alguien lo edito a mano, o una escritura no atomica lo dejo a medias."""
    (store.root / "roto.json").write_text("{no es json", encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        store.read_json("roto.json")


@pytest.mark.unit
def test_a_json_array_at_the_root_is_refused(store: FileArtifactStore) -> None:
    """Un artefacto es un objeto: necesita claves para llevar su procedencia."""
    (store.root / "array.json").write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        store.read_json("array.json")


# ---------------------------------------------------------------------------
# 5. Inventario
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_listing_is_alphabetically_stable(store: FileArtifactStore) -> None:
    """`rglob` no garantiza orden; dos inventarios deben salir iguales."""
    for name in ("z/last.json", "a/first.json", "m/mid.json"):
        store.write_json(name, {"n": name})
    assert store.list_artifacts() == ("a/first.json", "m/mid.json", "z/last.json")


@pytest.mark.unit
def test_listing_can_be_scoped_to_a_prefix(store: FileArtifactStore) -> None:
    store.write_json("run1/a.json", {})
    store.write_json("run2/b.json", {})
    assert store.list_artifacts("run1") == ("run1/a.json",)


@pytest.mark.unit
def test_listing_an_absent_prefix_is_empty_not_an_error(store: FileArtifactStore) -> None:
    """Preguntar por una corrida que no existe es legitimo."""
    assert store.list_artifacts("nunca") == ()


__all__: list[str] = []
