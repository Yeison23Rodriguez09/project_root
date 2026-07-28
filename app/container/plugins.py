"""Descubrimiento de manifiestos de plugin. Lee, no importa.

Separado de `app.core.registry.loader` a proposito, y la diferencia es la que
sostiene el contrato de `configs/plugins.toml`:

* este modulo **lee el manifiesto** de cada plugin y no ejecuta nada suyo;
* el loader **importa el codigo**, que es cuando los decoradores se registran.

El orden importa. Un plugin que reclama un nombre ya tomado, o que declara una
API que este nucleo no acepta, debe rechazarse ANTES de importarse: si se
importara primero, su `__init__` ya habria corrido, y el rechazo posterior seria
un rechazo de algo que ya se ejecuto.

Vive en `container` porque la raiz de composicion es quien decide que entra en
el sistema. `interfaces` puede consultarla -`qp plugins`- sin conocer ni el
sistema de ficheros ni el mecanismo de importacion.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Nombre del manifiesto dentro del directorio de cada plugin. Se lee de
#: `configs/plugins.toml`; este es el valor por defecto si el contrato falta.
DEFAULT_MANIFEST_FILENAME = "plugin.toml"


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Lo que un plugin declara de si mismo, mas de donde se leyo.

    Attributes:
        directory: Raiz del plugin. Se guarda porque el diagnostico util no es
            "falta `owner`" sino "falta `owner` en `plugins/indicator_ema`".
        data: Contenido del manifiesto, tal cual. Sin normalizar: validarlo es
            trabajo de `Preflight`, que aplica el contrato, y hacerlo aqui
            duplicaria la regla en dos sitios que acabarian discrepando.
        error: Motivo por el que el manifiesto no pudo leerse, si ocurrio. Un
            plugin ilegible se INVENTARIA igualmente, con su error: omitirlo
            haria que `qp plugins list` mostrara un catalogo mas corto sin
            decir que algo se cayo por el camino.
    """

    directory: Path
    data: Mapping[str, Any]
    error: str | None = None

    @property
    def name(self) -> str:
        declared = self.data.get("name")
        return str(declared) if declared else self.directory.name

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "directory": str(self.directory),
            "ok": self.ok,
            "error": self.error,
            "manifest": dict(self.data),
        }


def plugin_contract(root: Path) -> dict[str, Any]:
    """Contenido de `configs/plugins.toml`, o vacio si no existe."""
    path = root / "configs" / "plugins.toml"
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def external_directories(root: Path) -> tuple[Path, ...]:
    """Directorios donde se permite buscar plugins, segun el contrato."""
    declared = plugin_contract(root).get("discovery", {}).get("external_directories", ())
    return tuple(root / str(relative) for relative in declared)


def internal_packages(root: Path) -> tuple[str, ...]:
    """Paquetes internos que aportan bloques al catalogo, segun el contrato."""
    declared = plugin_contract(root).get("discovery", {}).get("internal_packages", ())
    return tuple(str(name) for name in declared)


def discover_manifests(root: Path) -> tuple[PluginManifest, ...]:
    """Manifiestos de todos los plugins externos instalados.

    Orden alfabetico estable por ruta. Un orden variable haria que el fallo por
    nombre repetido -que denuncia al SEGUNDO en reclamarlo- senalase a un plugin
    distinto en cada ejecucion, y con el la culpa cambiaria de sitio sola.
    """
    filename = str(
        plugin_contract(root).get("meta", {}).get("manifest_filename")
        or DEFAULT_MANIFEST_FILENAME
    )
    found: list[PluginManifest] = []
    for base in external_directories(root):
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            found.append(_read_manifest(child, filename))
    return tuple(found)


def manifest_payloads(manifests: Sequence[PluginManifest]) -> tuple[Mapping[str, Any], ...]:
    """Solo los manifiestos legibles, en la forma que espera `Preflight`.

    Los ilegibles se excluyen porque ya se denunciaron al leerlos; pasarlos
    produciria un segundo hallazgo -"no declara `name`"- que es consecuencia del
    primero y desplaza la atencion de la causa real.
    """
    return tuple(m.data for m in manifests if m.ok)


def _read_manifest(directory: Path, filename: str) -> PluginManifest:
    path = directory / filename
    if not path.is_file():
        return PluginManifest(
            directory=directory,
            data={},
            error=f"falta {filename}",
        )
    try:
        return PluginManifest(
            directory=directory,
            data=tomllib.loads(path.read_text(encoding="utf-8")),
        )
    except tomllib.TOMLDecodeError as exc:
        return PluginManifest(directory=directory, data={}, error=f"TOML invalido: {exc}")


__all__ = [
    "DEFAULT_MANIFEST_FILENAME",
    "PluginManifest",
    "discover_manifests",
    "external_directories",
    "internal_packages",
    "manifest_payloads",
    "plugin_contract",
]
