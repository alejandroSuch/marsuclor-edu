---
name: importar-materiales
description: Incorporar apuntes, PDF, fotos o una guía existente a un tema de Martina Edu, también al actualizarlo, con privacidad y procedencia.
---

Cuando una familia adjunte material o señale un documento, localízalo y léelo antes de escribir contenido. Si hay una guía ya desarrollada, úsala como base de la teoría; utiliza apuntes originales para contrastar objetivos, vocabulario y terminología. Si no puedes acceder a la guía, pide una copia: no la reconstruyas desde otra fuente.

Busca primero el tema en `content/`. Si existe, compara fuentes y contenido por secciones y conserva lo que no haya cambiado. Si es nuevo, prepara `topic.yml` y transcribe a los archivos del modelo existente. Registra fuentes identificables en `topic.yml` y enlaza cada bloque mediante `source_ids`; separa claramente las ampliaciones y las dudas. Una fuente ilegible queda pendiente, sin suposiciones.

Al leer PDF, fotos y apuntes manuscritos, haz también un inventario de los esquemas, mapas, árboles, ciclos y tablas que expliquen un objetivo. Comprueba visualmente sus etiquetas y relaciones; si una parte no se lee, déjala pendiente. Anota qué visual procede de clase y qué elementos serían una reelaboración didáctica. No copies el escaneo al repositorio público: cuando el esquema sea útil, traslada sus conocimientos a un diagrama de diseño propio con la misma terminología y `source_ids` fieles, o a una tabla si esa forma explica mejor el contenido. Si el original contradice la teoría validada, registra la duda y la propuesta de corrección antes de dibujarlo.

Revisa datos personales antes de incorporar archivos a este repositorio público. Conserva los originales privados fuera del repositorio cuando no sea apropiado publicarlos. Usa `generar-materiales` y `generar-flashcards` si la petición incluye materiales completos; no publiques ni confirmes como válido un tema a medio construir.
