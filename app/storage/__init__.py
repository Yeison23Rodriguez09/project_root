"""Zoo canonico, zoo espejo y repositorio de artefactos.

Capa infraestructura (nivel 4). Todo lo que este paquete escribe lleva
procedencia obligatoria: un artefacto sin `RunFingerprint` no es reconstruible y
por tanto no vale nada seis meses despues, que es exactamente cuando se consulta.

Ningun motor importa este paquete. Escriben a traves de `ArtifactStorePort` y
demas puertos declarados en `shared`, y `container` inyecta el adaptador. Esa
inversion es lo que permite que el mismo runner persista en disco en produccion
y en memoria en un test, sin ramas condicionales dentro del motor.

Subpaquetes:

* `artifacts/`  repositorio de artefactos con procedencia obligatoria
* `zoo/`        catalogo canonico de estrategias con su linaje (fase 8)
"""
