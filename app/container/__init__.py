"""Raiz de composicion: inyeccion de dependencias, arranque y ciclo de vida.

Composition Root, no Service Locator. La diferencia es donde se resuelve: aqui
el contenedor arma el grafo completo una sola vez y lo entrega ya construido. Un
Service Locator se pasaria a cada componente para que pidiera lo que necesita, y
entonces cada componente volveria a conocer al contenedor y ninguna dependencia
seria visible en su firma.

Reglas de este paquete:

* Es el UNICO autorizado a saber que `BrokerPort` lo implementa MT5.
* No contiene logica de negocio. No calcula. No decide. Solo conecta.
* Ejecuta `LifecyclePort` en orden topologico de dependencias: nada arranca
  antes de aquello de lo que depende, y se para en orden inverso.
* Antes de construir nada ejecuta `preflight`. Si una sola regla de gobernanza
  falla, el sistema no inicia.

Regla que hace cumplir en el resto del sistema: nada se instancia directamente.
Un motor nunca escribe `Broker(...)`; escribe `container.resolve(BrokerPort)`.
Sin eso el motor conoce una clase concreta y deja de ser sustituible en un test.

Modulos:

* `preflight.py`  validacion de arranque sobre el despliegue real
"""
