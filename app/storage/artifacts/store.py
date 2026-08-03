"""Repositorio de artefactos en disco. Implementa `ArtifactStorePort`.

Escribe JSON con `orjson` bajo una raiz configurada. Tres decisiones que no son
de comodidad:

**Escritura atomica.** Se escribe a un fichero temporal en el mismo directorio y
se renombra. `os.replace` es atomico dentro de un volumen, asi que un artefacto
o esta completo o no esta. Sin esto, una corrida interrumpida a mitad de escribir
deja un JSON truncado que el siguiente lector interpreta como corrupto, y el
diagnostico apunta al productor en vez de a la interrupcion.

**Rutas confinadas.** Toda ruta relativa se resuelve y se comprueba contra la
raiz. Un `../../etc/passwd` que llegara desde configuracion o desde el nombre de
una estrategia escribiria fuera del area de artefactos; se rechaza con un error
de dominio en lugar de confiar en que nadie lo intente.

**Claves ordenadas.** `OPT_SORT_KEYS` hace que el mismo contenido produzca el
mismo fichero byte a byte. Es lo que permite comparar dos corridas con `diff` y
lo que hace que el hash de un artefacto sea estable, que es P1 aplicado a la
persistencia.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import orjson

from app.core.exceptions import DataIntegrityError, StorageError

#: Opciones de serializacion. `SORT_KEYS` da salida estable byte a byte;
#: `SERIALIZE_NUMPY` evita que cada llamante escriba su propio conversor para los
#: escalares de numpy, que es de donde salen casi todos los numeros del sistema.
_JSON_OPTIONS = orjson.OPT_SORT_KEYS | orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_INDENT_2

#: Sufijo del fichero temporal previo al renombrado atomico.
_PARTIAL_SUFFIX = ".partial"


class FileArtifactStore:
    """Artefactos JSON bajo una raiz, con escritura atomica.

    Implementa `app.shared.ports.ArtifactStorePort`.

    La raiz se crea al construir. Es deliberado: si se creara perezosamente en la
    primera escritura, un permiso mal configurado no se descubriria hasta el
    final de una corrida larga, que es cuando ya no se puede hacer nada.
    """

    __slots__ = ("_root",)

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    # -- escritura -----------------------------------------------------------

    def write_json(self, relative_path: str, payload: Mapping[str, Any]) -> str:
        """Escribe un artefacto y devuelve su ruta absoluta.

        Raises:
            StorageError: si la ruta se sale de la raiz o el destino no es
                escribible.
            DataIntegrityError: si la carga contiene algo que no se puede
                serializar. Es un error de integridad y no de almacenamiento: el
                disco esta bien; lo que esta mal es lo que se le pidio guardar.
        """
        return self._write_bytes(relative_path, self._encode(relative_path, payload))

    def write_table(self, relative_path: str, rows: Sequence[Mapping[str, Any]]) -> str:
        """Escribe una secuencia de filas como JSON Lines.

        Un objeto por linea y no un array unico: una tabla de operaciones puede
        tener millones de filas y JSON Lines se lee en flujo, mientras que un
        array obliga a cargarlo entero para ver la primera. Ademas, un fichero
        truncado sigue siendo legible hasta la ultima linea completa.
        """
        encoded = b"".join(
            orjson.dumps(dict(row), option=orjson.OPT_SORT_KEYS | orjson.OPT_SERIALIZE_NUMPY)
            + b"\n"
            for row in rows
        )
        return self._write_bytes(relative_path, encoded)

    # -- lectura -------------------------------------------------------------

    def read_json(self, relative_path: str) -> Mapping[str, Any]:
        """Lee un artefacto previamente escrito.

        Raises:
            StorageError: si no existe.
            DataIntegrityError: si el contenido no es JSON valido, lo que
                significa que alguien lo edito a mano o que una escritura no
                atomica lo dejo a medias.
        """
        target = self._resolve(relative_path)
        if not target.is_file():
            raise StorageError("El artefacto no existe", path=relative_path, root=str(self._root))
        try:
            loaded = orjson.loads(target.read_bytes())
        except orjson.JSONDecodeError as exc:
            raise DataIntegrityError(
                "Artefacto ilegible: no es JSON valido",
                path=relative_path,
                detail=str(exc),
            ) from exc
        if not isinstance(loaded, dict):
            raise DataIntegrityError(
                "La raiz de un artefacto debe ser un objeto",
                path=relative_path,
                found=type(loaded).__name__,
            )
        return loaded

    def exists(self, relative_path: str) -> bool:
        return self._resolve(relative_path).is_file()

    def list_artifacts(self, prefix: str = "") -> tuple[str, ...]:
        """Rutas relativas de los artefactos existentes, en orden estable.

        Ordenado alfabeticamente a proposito: `Path.rglob` no garantiza orden y
        dos inventarios de la misma carpeta podrian salir distintos, lo que
        rompe la comparacion entre corridas.
        """
        base = self._resolve(prefix) if prefix else self._root
        if not base.is_dir():
            return ()
        return tuple(
            sorted(
                path.relative_to(self._root).as_posix()
                for path in base.rglob("*")
                if path.is_file() and not path.name.endswith(_PARTIAL_SUFFIX)
            )
        )

    # -- interno -------------------------------------------------------------

    def _encode(self, relative_path: str, payload: Mapping[str, Any]) -> bytes:
        try:
            return orjson.dumps(dict(payload), option=_JSON_OPTIONS)
        except TypeError as exc:
            raise DataIntegrityError(
                "Artefacto no serializable",
                path=relative_path,
                detail=str(exc),
            ) from exc

    def _write_bytes(self, relative_path: str, data: bytes) -> str:
        target = self._resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + _PARTIAL_SUFFIX)
        try:
            partial.write_bytes(data)
            # `Path.replace` es `os.replace`: atomico dentro de un volumen. El
            # artefacto o esta completo o no esta; nunca a medias.
            partial.replace(target)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise StorageError(
                "No se pudo escribir el artefacto",
                path=relative_path,
                detail=str(exc),
            ) from exc
        return str(target)

    def _resolve(self, relative_path: str) -> Path:
        """Ruta absoluta dentro de la raiz, o error si intenta salirse.

        La comprobacion es sobre la ruta YA resuelta, no sobre la cadena. Filtrar
        `..` textualmente deja pasar enlaces simbolicos y rutas absolutas, que son
        justo los dos casos que interesa cerrar.
        """
        candidate = (self._root / relative_path).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise StorageError(
                "Ruta de artefacto fuera de la raiz del repositorio",
                path=relative_path,
                resolved=str(candidate),
                root=str(self._root),
            )
        return candidate

    def __repr__(self) -> str:
        return f"FileArtifactStore({self._root})"


__all__ = ["FileArtifactStore"]
