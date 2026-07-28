"""Plugin de referencia. Paquete real, no un doble.

Existe para que el camino de carga externo -descubrimiento del manifiesto e
importacion por `Loader.load_plugin_directory`- se ejercite contra un plugin
autentico. Un mock del loader probaria el mock.

No registra nada en el catalogo a proposito: registrar exigiria un `Registry` de
features, que es Fase 4. Lo que se verifica aqui es el mecanismo de carga, no el
contenido cargado.
"""

from __future__ import annotations

LOADED = True

__all__ = ["LOADED"]
