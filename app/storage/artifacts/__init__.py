"""Repositorio de artefactos con procedencia obligatoria.

Un artefacto es cualquier salida persistida de una corrida: metricas, informes,
curvas de equity, inventarios de plugins. Todos se escriben con la misma
garantia -atomica, con claves ordenadas y confinada a la raiz- porque un
artefacto a medias o con orden de claves variable no se puede comparar contra el
de la corrida anterior, y comparar es para lo que existen.

* `store.py`  `FileArtifactStore`: adaptador de `ArtifactStorePort` sobre disco
"""
