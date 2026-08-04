"""Descubrimiento de componentes. El catalogo no sabe como se cargan.

Separacion deliberada:

* `Registry` es el **catalogo**: guarda entradas y las devuelve.
* `Loader` es el **descubrimiento**: encuentra modulos y los importa.

Ninguno conoce al otro. El unico vinculo es que un decorador se ejecuta durante
la importacion. Gracias a eso se puede anadir un directorio de plugins externos

    plugins/
        indicator_ema/
        indicator_rsi/
        strategy_breakout/

sin tocar una linea del catalogo, y se puede construir un catalogo desde una
fuente que no sea un modulo de Python sin arrastrar la maquinaria de importacion.

Determinismo: los modulos se importan en orden alfabetico estable. Con un
`Registry` que rechaza duplicados, un orden variable haria que el fallo por
nombre repetido apareciera unas veces si y otras no, que es la peor forma de
tener un bug.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from app.core.registry.exceptions import RegistryError


class PluginLoadError(RegistryError):
    """Un modulo de plugin fallo al importarse.

    No se degrada a aviso. Un plugin que no carga significa que faltan bloques
    del espacio de busqueda, y una corrida de discovery sobre un espacio
    incompleto produce conclusiones que parecen validas y no lo son.
    """

    code = "PLUGIN_LOAD_ERROR"


@dataclass(frozen=True, slots=True)
class LoadReport:
    """Constancia de que se cargo y que no.

    Se persiste junto a cada corrida. Sin ella no se puede saber que
    componentes existian el dia de la busqueda, y una corrida cuyo catalogo no
    es reconstruible no es reproducible por mucho que se guarde la semilla.
    """

    imported: tuple[str, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()
    roots: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "roots": list(self.roots),
            "imported": list(self.imported),
            "skipped": [{"module": m, "reason": r} for m, r in self.skipped],
            "n_imported": len(self.imported),
        }

    def __repr__(self) -> str:
        return f"LoadReport(imported={len(self.imported)}, skipped={len(self.skipped)})"


@dataclass(slots=True)
class Loader:
    """Importa modulos para que sus componentes se den de alta.

    Attributes:
        strict: Si es `True` (por defecto) un fallo de importacion aborta. Solo
            deberia bajarse a `False` en una sesion exploratoria, nunca en una
            corrida cuyo resultado vaya a compararse con otra.
        skip_private: Ignora modulos cuyo nombre empieza por `_`.
    """

    strict: bool = True
    skip_private: bool = True
    _imported: list[str] = field(default_factory=list, init=False)
    _skipped: list[tuple[str, str]] = field(default_factory=list, init=False)

    # -- descubrimiento interno --------------------------------------------

    def load_package(self, dotted: str, *, recursive: bool = True) -> LoadReport:
        """Importa todos los submodulos de un paquete ya instalado.

        Es la via normal: `loader.load_package("app.research.features")` hace
        que todos los indicadores se registren. El paquete raiz se importa
        primero para que su `__init__` pueda preparar lo que necesite.
        """
        root = self._import(dotted)
        if root is None:
            return self._report((dotted,))

        search_paths = getattr(root, "__path__", None)
        if search_paths is None:
            return self._report((dotted,))

        walk = pkgutil.walk_packages if recursive else pkgutil.iter_modules
        names = sorted(
            name
            for _finder, name, _ispkg in walk(search_paths, prefix=f"{dotted}.")
            if not (self.skip_private and self._is_private(name))
        )
        for name in names:
            self._import(name)
        return self._report((dotted,))

    def load_packages(self, dotted_names: Iterable[str]) -> LoadReport:
        """Carga varios paquetes en el orden dado."""
        roots = tuple(dotted_names)
        for dotted in roots:
            self.load_package(dotted)
        return self._report(roots)

    # -- descubrimiento externo --------------------------------------------

    def load_plugin_directory(self, directory: Path | str) -> LoadReport:
        """Importa cada subdirectorio-paquete de una carpeta de plugins.

        La carpeta se anade a `sys.path` de forma temporalmente permanente: se
        deja puesta porque un plugin puede importar a otro. Se comprueba que no
        estuviera ya para no ensuciar la ruta en llamadas repetidas.

        Un plugin es un directorio con `__init__.py`. Los sueltos `.py` se
        ignoran a proposito: obligar a que cada plugin sea un paquete deja
        sitio para sus metadatos y sus pruebas desde el primer dia.
        """
        base = Path(directory).resolve()
        if not base.is_dir():
            if self.strict:
                raise PluginLoadError("El directorio de plugins no existe", directory=str(base))
            self._skipped.append((str(base), "directorio inexistente"))
            return self._report((str(base),))

        if str(base) not in sys.path:
            sys.path.insert(0, str(base))

        for child in sorted(base.iterdir()):
            if not child.is_dir() or not (child / "__init__.py").exists():
                continue
            if self.skip_private and child.name.startswith("_"):
                continue
            self._import(child.name)
        return self._report((str(base),))

    # -- interno ------------------------------------------------------------

    def _import(self, dotted: str) -> ModuleType | None:
        if dotted in sys.modules:
            self._imported.append(dotted)
            return sys.modules[dotted]
        try:
            module = importlib.import_module(dotted)
        except Exception as exc:
            if self.strict:
                raise PluginLoadError(
                    f"No se pudo importar {dotted!r}",
                    module=dotted,
                    cause=f"{type(exc).__name__}: {exc}",
                ) from exc
            self._skipped.append((dotted, f"{type(exc).__name__}: {exc}"))
            return None
        self._imported.append(dotted)
        return module

    @staticmethod
    def _is_private(dotted: str) -> bool:
        return any(part.startswith("_") for part in dotted.split("."))

    def _report(self, roots: Sequence[str]) -> LoadReport:
        return LoadReport(
            imported=tuple(dict.fromkeys(self._imported)),
            skipped=tuple(self._skipped),
            roots=tuple(roots),
        )


def load_default_components(*, strict: bool = True) -> LoadReport:
    """Carga los catalogos internos de la plataforma.

    Punto unico de entrada para que ningun runner tenga que recordar la lista de
    paquetes que hay que importar. Si un motor olvidara uno, discovery buscaria
    sobre un espacio incompleto sin dar ninguna senal de que falta algo.
    """
    return Loader(strict=strict).load_packages(
        (
            "app.research.features",
            "app.research.signals",
            "app.research.strategies",
        )
    )


__all__ = ["LoadReport", "Loader", "PluginLoadError", "load_default_components"]
