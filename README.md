# Martina Edu

Materiales de estudio por asignatura, curso y tema. La portada muestra las asignaturas y sus cursos disponibles; cada curso tiene una página con los temas y enlaces a apuntes, ejercicios, tarjetas, guiones de podcast y actividades adicionales cuando las haya. El contenido editable vive en `content/`; Quarto genera la web y Chromium imprime las mismas páginas en PDF A4. El primer tema completo, *Els fonaments del planeta Terra* (Geografía, 1.º ESO), sirve para comprobar el recorrido completo del generador.

## Pedir materiales desde Codex

Abre este repositorio como proyecto en Codex. Adjunta los apuntes o indica dónde está la guía y escribe lo que necesitas con tus propias palabras. No hace falta indicar carpetas, YAML, Quarto, diseño de impresión ni comandos. Codex deduce el curso y la asignatura de la petición y del material; si una lengua de enseñanza configurable no queda clara, la consulta. Los materiales salen en la web y en PDF A4.

Ejemplos de peticiones:

> Estos son los apuntes de Biología de 1.º de ESO sobre las células. Prepara el tema para estudiar y practicar.

> Añade esta nueva página de los apuntes al tema de Geografía que ya tenemos. Actualiza solo lo que cambie.

> Mi hija confunde rotación y translación. Añade ejercicios y tarjetas de repaso al tema existente.

> Revisa las soluciones del tema de fracciones de Matemáticas de 1.º de ESO y corrige los errores que encuentres.

Una petición de preparar un tema completo produce apuntes, ejercicios, solucionario y tarjetas interactivas/recortables; añade un guion de podcast si su política es `default`. Si solo se pide una parte, se actualiza esa parte y lo que dependa directamente de ella. Los originales con datos personales se usan como referencia privada y no se publican.

## Crear y actualizar un tema

1. Codex busca primero `content/<asignatura>/<curso>/<tema>/` y lee las fuentes disponibles. Si hay una guía desarrollada, conserva su teoría; los apuntes originales sirven para contrastar objetivos, vocabulario y lenguaje de clase. Si falta acceso a la guía, solicita una copia antes de trabajar en esa teoría.
2. Para un tema nuevo, crea el contenido estructurado y los materiales que correspondan según la configuración. Para uno existente, compara la petición con el contenido actual, conserva secciones e identificadores vigentes y cambia solo los elementos afectados, incluidas sus soluciones y tarjetas cuando corresponda.
3. Codex valida cobertura, fuentes y referencias, genera web y PDF y revisa la salida. Una corrección científica se comunica con su justificación; no se oculta dentro de una reescritura general.

Los archivos principales de cada tema son `topic.yml` (metadatos, objetivos, fuentes y vocabulario del examen), `notes.yml` (secciones), `exercises.yml` (preguntas y soluciones) y `flashcards.yml`. `vocabulary.yml` y `diagrams.yml` con SVG se usan cuando el tema los necesita. Un tema puede incluir `pasapalabra.yml` para añadir una página propia: cada grupo tiene `letter`, un posible `mode: contains` y una lista `clues` con `id`, `answer`, `clue`, `objective_ids` y `source_ids`. La web oculta cada respuesta hasta pulsar su control; el PDF deja espacio para responder y reúne las soluciones al final. Cada bloque lleva los identificadores de objetivos y fuentes que lo respaldan. Los `source_ids` de una ampliación deben apuntar a su fuente real, no a los apuntes de clase por comodidad. Las fuentes y notas de procedencia permanecen en los YAML para la revisión editorial; no aparecen como citas por apartado ni como listado final en la web o los PDF. El tema de Geografía muestra el formato completo; su contenido educativo no es una plantilla para otros temas.

### Dónde vive cada regla

- `AGENTS.md`: criterios comunes, privacidad, conservación de contenido y comprobación final.
- `config/subjects.yml`: asignaturas y lengua de enseñanza fija o configurable.
- `content/<asignatura>/AGENTS.md`: criterios pedagógicos propios de esa materia, sin repetir la configuración de idiomas.
- `.agents/skills/importar-materiales/`: lectura de adjuntos o guías, procedencia y actualización de fuentes.
- `.agents/skills/generar-materiales/`: apuntes, ejercicios, soluciones, guiones de podcast y revisión de impresión.
- `.agents/skills/generar-flashcards/`: tarjetas web e imprimibles.
- `scripts/build.py`: reglas automáticas del modelo de datos y comprobación de las salidas.

Codex selecciona las skills según la petición; el padre no tiene que nombrarlas. Las decisiones editoriales que no pueden verificarse automáticamente, como la fidelidad de una explicación o la calidad de un ejercicio, se revisan con las fuentes.

## Uso local

### Guiones de podcast

El guion es un quinto material educativo, editable y versionado en `content/<asignatura>/<curso>/<tema>/podcast/script.md`. Codex lo redacta desde la teoría validada y el vocabulario del tema, con el nivel del curso y la lengua de enseñanza de `topic.yml`. La narración debe poder entenderse sin imágenes. Ejemplos de peticiones:

> Genera todos los materiales del tema 2 de Biología de 1.º ESO, incluido el podcast.

> Genera únicamente el podcast del tema 1 de Geografía.

> Actualiza el podcast para introducir más preguntas de repaso.

> Haz el podcast más conversacional sin modificar los apuntes.

La política está en `config/subjects.yml`, bajo `materials.podcast`. El valor global inicial es `generation: on_demand`, `format: narration` y `duration_minutes: 12`. Una asignatura puede sobrescribir cualquier campo en su entrada de `subjects`; un tema puede hacerlo en `topic.yml` con el mismo bloque `materials.podcast`. La precedencia es global → asignatura → tema → petición explícita. `default` crea el guion al pedir todos los materiales; `on_demand` solo ante petición explícita; `disabled` impide generarlo incluso si se pide expresamente, hasta cambiar la configuración. La duración orienta la redacción, sin forzar relleno. La política no sobrescribe un guion existente: una revisión necesita petición expresa o un cambio relevante del contenido.

Por ejemplo, añade a `topic.yml` `materials: {podcast: {generation: default}}` para activar por defecto un tema de Matemáticas. En `config/subjects.yml`, coloca ese mismo bloque dentro de la entrada de la asignatura para cambiar todos sus temas.

La configuración inicial es `default` para Valencià, Biologia, Geografia y Lengua castellana; `on_demand` para Francès, Anglès, Matemáticas, Educació física y Música. Las asignaturas de lengua configurable toman el idioma de explicación de `teaching_language` del tema; la lengua estudiada puede ser distinta.

El formato inicial es narración con una sola voz. Un ejemplo mínimo:

```markdown
---
title: Els fonaments del planeta Terra
language: ca-valencia
format: narration
estimated_duration_minutes: 12
objective_ids: [o1]
source_ids: [guide]
---

## Introducció

La Terra gira sobre si mateixa. Això origina el dia i la nit.

Quin moviment provoca el dia i la nit?

[pause: 5]

És la rotació.
```

Cada `##` abre un capítulo; los párrafos sin marcas son voz narrada; `[pause: segundos]`, en línea propia, indica silencio y admite decimales. Los metadatos y títulos no se narran. No uses listas, enlaces, imágenes, HTML ni instrucciones técnicas en el cuerpo. `source_ids` y `objective_ids` conservan la trazabilidad editorial sin mostrarse en la página pública. Los ejemplos pedagógicos reformulan conocimientos existentes; no añaden objetivos nuevos. Hay guiones en `content/geografia/1-eso/els-fonaments-del-planeta-terra/podcast/script.md` y `content/biologia/1-eso/els-essers-vius/podcast/script.md`.

La CLI existente procesa el mismo archivo en local y en CI:

```sh
python scripts/build.py podcast policy geografia/1-eso/els-fonaments-del-planeta-terra --all-materials
python scripts/build.py podcast policy geografia/1-eso/els-fonaments-del-planeta-terra --explicit
python scripts/build.py podcast validate geografia/1-eso/els-fonaments-del-planeta-terra
python scripts/build.py podcast text geografia/1-eso/els-fonaments-del-planeta-terra
python scripts/build.py podcast inspect geografia/1-eso/els-fonaments-del-planeta-terra
python -m unittest discover -s tests
```

`text` extrae únicamente la voz. `inspect` devuelve metadatos y eventos ordenados de capítulo, voz y pausa en JSON. Para previsualizar, ejecuta el flujo web indicado abajo y después `python scripts/build.py serve`; abre `http://localhost:8000/topics/geografia/1-eso/els-fonaments-del-planeta-terra/podcast.html`. La página muestra capítulos y pausas, y ofrece PDF A4. El guion original sigue disponible como Markdown en `content/`.

Codex genera y revisa el contenido; `scripts/podcast.py` valida y procesa el texto sin IA ni servicios externos; GitHub Actions ejecuta las pruebas, valida, construye y publica la vista web mediante el mismo código. La síntesis TTS y el MP3 quedan pendientes: un motor futuro podrá consumir los eventos de `inspect` y resolver motor, voz e idioma fuera del guion.

### Con Docker

Con Docker instalado y en marcha, desde la raíz del repositorio:

```sh
docker compose up --build
```

La validación, la generación y los PDF se ejecutan antes de abrir [http://localhost:8000](http://localhost:8000). Para incorporar cambios, detén el proceso y ejecuta `docker compose up` otra vez. Los archivos generados viven en volúmenes de Docker; `docker compose down -v` los elimina. La primera construcción descarga Quarto y Chromium y puede tardar unos minutos.

### Sin Docker

Se necesitan Python 3.11+, Quarto 1.6+ y Chromium de Playwright para los PDF.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python scripts/build.py validate
python scripts/build.py prepare
quarto render generated --to html
python -m playwright install chromium
python scripts/build.py pdf
python scripts/build.py verify
```

Abre `site/index.html` o inicia `python -m http.server 8000 -d site`. Los PDF se generan en `site/pdf/`. Para las tarjetas, imprime el PDF a doble cara, girando por el borde largo y al 100 % de escala. No edites `generated/` ni `site/`: se regeneran desde `content/`.

## Validación y publicación

`validate` comprueba la lengua y el curso, los identificadores, la cobertura de los objetivos en apuntes, ejercicios y tarjetas, el vocabulario de examen, las fuentes y los diagramas referenciados, y que cada ejercicio tenga respuesta. También comprueba las pistas y respuestas del Pasapalabra y la estructura de los guiones existentes. `verify` comprueba las páginas web por tema, sus PDF A4, las imágenes, las tarjetas interactivas, el orden de anversos y reversos, la revelación de conceptos del Pasapalabra y la vista textual del podcast. Los PDF de ejercicios y del Pasapalabra incluyen un solucionario al final. Estas comprobaciones no sustituyen la revisión pedagógica y visual.

Cada cambio enviado a `main` ejecuta esas comprobaciones en GitHub Actions y publica `site/` en GitHub Pages si terminan correctamente. El repositorio y la web son públicos: no subas fotografías o escaneos con datos personales. Transcribe el contenido necesario y registra el original privado como fuente sin adjuntarlo.
