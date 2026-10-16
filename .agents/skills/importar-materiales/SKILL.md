---
name: importar-materiales
description: Incorporar apuntes del padre a un tema de Martina Edu con revisión de privacidad y trazabilidad.
---

1. Pide asignatura y curso solo si no se deducen del mensaje o archivo. Lee `config/subjects.yml`.
2. Revisa imágenes/PDF en privado: detecta nombres, notas, centros u otros datos personales. No copies escaneos al repositorio público.
3. Transcribe a Markdown y crea `topic.yml` con objetivos y `sources`; marca lo no legible como pendiente, sin inventarlo.
4. Identifica cada ampliación con `source_id` verificable. No presentes una ampliación como contenido del apunte.
5. Ejecuta `python scripts/build.py validate` y comunica lo que aún requiere confirmar.
