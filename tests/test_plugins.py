"""Contrato de plugins: descubrimiento, validacion y carga real.

Existe porque el bloque B1 dejo `tests/fixtures/plugins/` sin ningun consumidor.
Ocho ficheros de plugin autenticos y ni un test que los cargara: exactamente el
"declarado sin consumidor" que ADR-0006 obliga a recortar, cometido dentro del
bloque que vino a eliminarlo.

Lo que se prueba aqui es el MECANISMO, con plugins reales. `plugin_valid` es un
paquete de Python de verdad con un `plugin.toml` de ocho campos, y el `Loader`
que lo importa es el mismo que usara la plataforma. No hay mock del loader
-probaria el mock- ni manifiestos construidos en memoria: se leen de disco,
porque leer de disco es la mitad de lo que puede salir mal.

Los cuatro fixtures cubren los cuatro finales posibles de una carga:

    plugin_valid            conforme; se descubre y se importa
    plugin_incomplete       le faltan campos obligatorios
    plugin_future_api       declara una API que este nucleo no acepta
    plugin_broken_manifest  el TOML no parsea
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from app.container.plugins import (
    DEFAULT_MANIFEST_FILENAME,
    PluginManifest,
    discover_manifests,
    external_directories,
    internal_packages,
    manifest_payloads,
    plugin_contract,
)
from app.container.preflight import Preflight
from app.core.registry.loader import Loader, PluginLoadError
from app.core.types import Severity

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "plugins"


@pytest.fixture
def deployment(tmp_path: Path) -> Path:
    """Despliegue completo con los fixtures instalados como plugins reales.

    Se copia el contrato real y los plugins reales a una raiz temporal en lugar
    de apuntar a `tests/fixtures/`. El motivo: `discover_manifests` deriva el
    directorio de `configs/plugins.toml`, y sortear esa derivacion para el test
    dejaria sin probar precisamente la parte que une contrato y descubrimiento.
    """
    (tmp_path / "configs").mkdir()
    for name in ("plugins.toml", "architecture.toml", "runtime.toml", "conventions.toml"):
        shutil.copy(ROOT / "configs" / name, tmp_path / "configs" / name)
    shutil.copy(ROOT / "CONSTITUTION.md", tmp_path / "CONSTITUTION.md")
    shutil.copytree(FIXTURES, tmp_path / "plugins")
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Los fixtures son plugins autenticos
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_the_fixtures_exist_and_are_packages() -> None:
    """Un plugin es un directorio con `__init__.py`, nunca un `.py` suelto.

    La exigencia deja sitio desde el primer dia para su manifiesto, sus pruebas
    y sus datos de referencia.
    """
    directories = sorted(p for p in FIXTURES.iterdir() if p.is_dir())
    assert len(directories) == 4, "los cuatro finales de una carga deben estar cubiertos"
    for directory in directories:
        assert (directory / "__init__.py").is_file(), f"{directory.name} no es un paquete"
        assert (directory / DEFAULT_MANIFEST_FILENAME).is_file()


@pytest.mark.contract
def test_the_valid_fixture_declares_every_required_field() -> None:
    """Si el fixture conforme no lo fuera, el test de rechazo no probaria nada."""
    required = set(plugin_contract(ROOT)["manifest"]["required"])
    manifest = PluginManifest(
        directory=FIXTURES / "plugin_valid",
        data=discover_from(FIXTURES / "plugin_valid"),
    )
    assert manifest.ok
    assert required <= set(manifest.data), f"faltan {sorted(required - set(manifest.data))}"


def discover_from(directory: Path) -> dict[str, object]:
    """Manifiesto de un directorio concreto, leido de disco."""
    import tomllib

    return tomllib.loads((directory / DEFAULT_MANIFEST_FILENAME).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 2. Descubrimiento
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_discovery_finds_every_installed_plugin(deployment: Path) -> None:
    manifests = discover_manifests(deployment)
    assert [m.directory.name for m in manifests] == [
        "plugin_broken_manifest",
        "plugin_future_api",
        "plugin_incomplete",
        "plugin_valid",
    ]
    # El manifiesto ilegible no tiene `name` utilizable, asi que cae al nombre
    # del directorio. Es deliberado: un plugin roto debe poder NOMBRARSE en el
    # informe, o el operador no sabe cual de los cuatro tiene que arreglar.
    assert [m.name for m in manifests] == [
        "plugin_broken_manifest",
        "plugin_future_api",
        "plugin_incomplete",
        "plugin_valid",
    ]


@pytest.mark.unit
def test_discovery_order_is_stable(deployment: Path) -> None:
    """Un orden variable haria que el fallo por nombre repetido -que denuncia al
    SEGUNDO en reclamarlo- senalase a un plugin distinto en cada ejecucion."""
    first = [m.directory.name for m in discover_manifests(deployment)]
    second = [m.directory.name for m in discover_manifests(deployment)]
    assert first == second == sorted(first)


@pytest.mark.unit
def test_an_unreadable_manifest_is_inventoried_with_its_error(deployment: Path) -> None:
    """Un plugin ilegible se lista igualmente.

    Omitirlo haria que `qp plugins list` mostrara un catalogo mas corto sin
    decir que algo se cayo por el camino, que es la peor forma de perder un
    bloque del espacio de busqueda.
    """
    broken = next(
        m for m in discover_manifests(deployment) if m.directory.name == "plugin_broken_manifest"
    )
    assert not broken.ok
    assert broken.error is not None
    assert "TOML" in broken.error


@pytest.mark.unit
def test_unreadable_manifests_are_excluded_from_the_validation_payload(
    deployment: Path,
) -> None:
    """Ya se denunciaron al leerlos; pasarlos produciria un segundo hallazgo
    -"no declara name"- que es consecuencia del primero y desplaza la atencion."""
    manifests = discover_manifests(deployment)
    payloads = manifest_payloads(manifests)
    assert len(manifests) == 4
    assert len(payloads) == 3


@pytest.mark.unit
def test_a_missing_manifest_is_reported_not_ignored(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    shutil.copy(ROOT / "configs" / "plugins.toml", tmp_path / "configs" / "plugins.toml")
    (tmp_path / "plugins" / "sin_manifiesto").mkdir(parents=True)
    (tmp_path / "plugins" / "sin_manifiesto" / "__init__.py").write_text("", encoding="utf-8")

    (manifest,) = discover_manifests(tmp_path)
    assert not manifest.ok
    assert DEFAULT_MANIFEST_FILENAME in str(manifest.error)


@pytest.mark.unit
def test_contract_declares_where_to_look(deployment: Path) -> None:
    """El QUE, no el COMO: el contrato declara los sitios; el loader decide como."""
    assert external_directories(deployment) == (deployment / "plugins",)
    assert internal_packages(deployment) == (
        "app.research.features",
        "app.research.signals",
        "app.research.strategies",
    )


# ---------------------------------------------------------------------------
# 3. Validacion contra el contrato, SIN importar el plugin
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_incomplete_and_incompatible_plugins_are_rejected(deployment: Path) -> None:
    """La validacion ocurre ANTES de importar.

    Un plugin que reclama un nombre tomado o una API futura debe rechazarse sin
    que su `__init__` haya corrido; si se importara primero, el rechazo llegaria
    cuando el efecto ya ocurrio.
    """
    manifests = discover_manifests(deployment)
    result = Preflight(deployment, mode="research").run(
        installed_plugins=manifest_payloads(manifests)
    )
    codes = {issue.code for issue in result.report}

    assert "PREFLIGHT_PLUGIN_INCOMPLETE" in codes, "plugin_incomplete debio denunciarse"
    assert "PREFLIGHT_PLUGIN_API_TOO_OLD" in codes, "plugin_future_api debio denunciarse"

    for name in ("plugin_valid", "plugin_future_api", "plugin_incomplete"):
        assert name not in sys.modules, f"{name} se importo durante la validacion"


@pytest.mark.contract
def test_a_name_clash_is_fatal(deployment: Path) -> None:
    """Dos plugins que registran el mismo nombre no pueden convivir.

    Cual ganase dependeria del orden de carga, y con el cambiaria en silencio el
    significado de toda estrategia del zoo que referencie ese nombre.
    """
    twin = deployment / "plugins" / "plugin_twin"
    twin.mkdir()
    (twin / "__init__.py").write_text('"""Gemelo."""\n', encoding="utf-8")
    (twin / DEFAULT_MANIFEST_FILENAME).write_text(
        'name = "plugin_twin"\nversion = "1.0.0"\napi_version = "1.0"\nkind = "feature"\n'
        'capability = "Research"\nowner = "Externo"\ndescription = "Reclama fixture_alpha."\n'
        'provides = ["fixture_alpha"]\n',
        encoding="utf-8",
    )
    result = Preflight(deployment, mode="research").run(
        installed_plugins=manifest_payloads(discover_manifests(deployment))
    )
    clashes = [i for i in result.report if i.code == "PREFLIGHT_PLUGIN_NAME_CLASH"]
    assert clashes, "la colision de `provides` debio detectarse"
    assert clashes[0].severity is Severity.FATAL


@pytest.mark.unit
def test_a_conforming_plugin_alone_produces_no_findings(tmp_path: Path) -> None:
    """El reciproco: si todo se denunciara, denunciar no significaria nada."""
    (tmp_path / "configs").mkdir()
    for name in ("plugins.toml", "architecture.toml", "runtime.toml", "conventions.toml"):
        shutil.copy(ROOT / "configs" / name, tmp_path / "configs" / name)
    shutil.copy(ROOT / "CONSTITUTION.md", tmp_path / "CONSTITUTION.md")
    shutil.copytree(FIXTURES / "plugin_valid", tmp_path / "plugins" / "plugin_valid")

    result = Preflight(tmp_path, mode="research").run(
        installed_plugins=manifest_payloads(discover_manifests(tmp_path))
    )
    assert not [i for i in result.report if i.code.startswith("PREFLIGHT_PLUGIN_")]


# ---------------------------------------------------------------------------
# 4. Carga real: el Loader importa el paquete de verdad
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_loader_imports_a_real_plugin_package(deployment: Path) -> None:
    """El camino de carga externo, ejercitado de extremo a extremo.

    Se importa un paquete de Python autentico desde un directorio que no estaba
    en `sys.path`. Es la unica forma de comprobar que el mecanismo funciona: un
    doble del loader demostraria que el doble funciona.
    """
    only_valid = deployment / "solo_validos"
    only_valid.mkdir()
    shutil.copytree(FIXTURES / "plugin_valid", only_valid / "plugin_valid")

    report = Loader(strict=True).load_plugin_directory(only_valid)

    assert "plugin_valid" in report.imported
    assert not report.skipped
    assert sys.modules["plugin_valid"].LOADED is True


@pytest.mark.integration
def test_loading_the_same_directory_twice_is_idempotent(deployment: Path) -> None:
    """`sys.path` no se ensucia y el modulo no se importa dos veces."""
    only_valid = deployment / "otra_vez"
    only_valid.mkdir()
    shutil.copytree(FIXTURES / "plugin_valid", only_valid / "plugin_valid")

    Loader(strict=True).load_plugin_directory(only_valid)
    before = list(sys.path)
    Loader(strict=True).load_plugin_directory(only_valid)
    assert sys.path == before


@pytest.mark.integration
def test_an_absent_directory_is_fatal_in_strict_mode(tmp_path: Path) -> None:
    """`reject`, no `warn`: un directorio ausente significa bloques que faltan,
    y degradarlo a aviso produce corridas que parecen completas."""
    with pytest.raises(PluginLoadError):
        Loader(strict=True).load_plugin_directory(tmp_path / "no-existe")


@pytest.mark.integration
def test_a_permissive_loader_records_the_skip_instead_of_failing(tmp_path: Path) -> None:
    """Modo exploratorio: se degrada, pero deja constancia de lo que falto."""
    report = Loader(strict=False).load_plugin_directory(tmp_path / "no-existe")
    assert report.skipped
    assert not report.imported


@pytest.mark.unit
def test_the_shipped_plugins_directory_exists_and_is_empty_of_plugins() -> None:
    """`plugins/` se envia vacio y ese es el estado correcto.

    Los cinco `kind` admitidos -feature, signal, strategy, statistical_test,
    broker- pertenecen a la fase 4 o posteriores. Un plugin aqui hoy seria
    codigo sin motor que lo consuma.
    """
    shipped = ROOT / "plugins"
    assert shipped.is_dir(), "el contrato lo declara como directorio de busqueda"
    assert discover_manifests(ROOT) == (), "no debe haber plugins instalados todavia"


__all__: list[str] = []
