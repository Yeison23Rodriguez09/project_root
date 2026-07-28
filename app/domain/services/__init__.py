"""Servicios de dominio: reglas que no pertenecen a una sola entidad.

Un servicio de dominio aparece cuando una regla necesita varias entidades a la
vez y forzarla dentro de una de ellas crearia una dependencia artificial.
Dimensionar una posicion necesita instrumento, cartera y perfil de riesgo:
no es responsabilidad de ninguno de los tres por separado.

Siguen siendo funciones puras. Reciben todo por argumento y no tocan
infraestructura. La diferencia con `app/application` es que aqui vive la
**regla** y alli vive la **coordinacion**.

Contenido previsto:

* `sizing.py`           volumen a partir de riesgo por operacion y distancia al stop
* `scoring.py`          puntuacion multicriterio de un candidato
* `rejection_rules.py`  criterios de descarte, cada uno con su motivo
* `promotion_policy.py` veredicto de promocion con la evidencia que lo sostiene

Fases 6-7. No se escriben antes porque su contrato depende de la forma
definitiva de las metricas de walk-forward, y adivinarla ahora produciria una
interfaz que habria que rehacer.
"""
