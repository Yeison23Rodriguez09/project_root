"""Bus de eventos en proceso. Mecanismo puro, sin I/O.

Ningun modulo conoce al siguiente: emite un hecho y no sabe si alguien escucha.
`research` publica `FeaturesCalculated` sin saber que existen `analytics` ni
`monitoring`.

En capa `core` porque encaminar un evento no toca disco ni red. Si estuviera en
infraestructura, un motor de nivel 3 no podria emitir sin violar la matriz y
acabariamos pasando callbacks a mano, perdiendo justo el desacoplamiento que el
bus existe para dar. Los suscriptores que si hacen I/O viven en `monitoring` y se
enganchan desde `container`.

El bus NO depende de `domain`: si conociera `Trade` u `Order`, cada evento nuevo
del dominio obligaria a tocar el bus. Transporta carga generica; la interpreta el
suscriptor.

Modulos:

* `event.py`     `Event`, `EventMeta`, validacion de carga
* `bus.py`       `EventBus`, suscripciones, middlewares, politica de error
* `recorder.py`  grabacion y reproduccion de secuencias
"""
