"""Nivel 1: entidades y reglas puras del negocio cuantitativo.

Restricciones que definen esta capa:

* solo puede importar de `app.core`;
* no toca disco, red, reloj del sistema ni broker;
* no contiene aleatoriedad que no llegue inyectada;
* todos sus objetos son inmutables y validan sus invariantes al construirse.

Consecuencia practica: cualquier objeto de dominio que exista es un objeto
correcto. Los motores no repiten comprobaciones defensivas.

Organizacion:

* `entities/`      identidad propia que sobrevive al cambio de atributos
* `value_objects/` la identidad **es** el contenido
* `services/`      reglas que no pertenecen a una sola entidad (fases 6-7)
"""
