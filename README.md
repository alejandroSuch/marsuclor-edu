# Martina Edu

Materiales de estudio por asignatura, curso y tema. El contenido editable vive en `content/`; Quarto genera la web y Chromium imprime las mismas páginas en PDF A4. El primer tema completo, *Els fonaments del planeta Terra* (Geografía, 1.º ESO), sirve para comprobar el recorrido completo del generador.

## Pedir materiales desde Codex

Abre este repositorio como proyecto en Codex. Adjunta los apuntes o indica dónde está la guía y escribe lo que necesitas con tus propias palabras. No hace falta indicar carpetas, YAML, Quarto, diseño de impresión ni comandos. Codex deduce el curso y la asignatura de la petición y del material; si una lengua de enseñanza configurable no queda clara, la consulta. Los materiales salen en la web y en PDF A4.

Ejemplos de peticiones:

> Estos son los apuntes de Biología de 1.º de ESO sobre las células. Prepara el tema para estudiar y practicar.

> Añade esta nueva página de los apuntes al tema de Geografía que ya tenemos. Actualiza solo lo que cambie.

> Mi hija confunde rotación y translación. Añade ejercicios y tarjetas de repaso al tema existente.

> Revisa las soluciones del tema de fracciones de Matemáticas de 1.º de ESO y corrige los errores que encuentres.

Por defecto, una petición de preparar un tema completo produce apuntes, ejercicios, solucionario y tarjetas interactivas/recortables. Si solo se pide una parte, se actualiza esa parte y lo que dependa directamente de ella. Los originales con datos personales se usan como referencia privada y no se publican.

## Crear y actualizar un tema

1. Codex busca primero `content/<asignatura>/<curso>/<tema>/` y lee las fuentes disponibles. Si hay una guía desarrollada, conserva su teoría; los apuntes originales sirven para contrastar objetivos, vocabulario y lenguaje de clase. Si falta acceso a la guía, solicita una copia antes de trabajar en esa teoría.
2. Para un tema nuevo, crea el contenido estructurado y los cuatro materiales. Para uno existente, compara la petición con el contenido actual, conserva secciones e identificadores vigentes y cambia solo los elementos afectados, incluidas sus soluciones y tarjetas cuando corresponda.
3. Codex valida cobertura, fuentes y referencias, genera web y PDF y revisa la salida. Una corrección científica se comunica con su justificación; no se oculta dentro de una reescritura general.

Los archivos principales de cada tema son `topic.yml` (metadatos, objetivos, fuentes y vocabulario del examen), `notes.yml` (secciones), `exercises.yml` (preguntas y soluciones) y `flashcards.yml`. `vocabulary.yml` y `diagrams.yml` con SVG se usan cuando el tema los necesita. Cada bloque lleva los identificadores de objetivos y fuentes que lo respaldan. Los `source_ids` de una ampliación deben apuntar a su fuente real, no a los apuntes de clase por comodidad. El tema de Geografía muestra el formato completo; su contenido educativo no es una plantilla para otros temas.

### Dónde vive cada regla

- `AGENTS.md`: criterios comunes, privacidad, conservación de contenido y comprobación final.
- `config/subjects.yml`: asignaturas y lengua de enseñanza fija o configurable.
- `content/<asignatura>/AGENTS.md`: criterios pedagógicos propios de esa materia, sin repetir la configuración de idiomas.
- `.agents/skills/importar-materiales/`: lectura de adjuntos o guías, procedencia y actualización de fuentes.
- `.agents/skills/generar-materiales/`: apuntes, ejercicios, soluciones y revisión de impresión.
- `.agents/skills/generar-flashcards/`: tarjetas web e imprimibles.
- `scripts/build.py`: reglas automáticas del modelo de datos y comprobación de las salidas.

Codex selecciona las skills según la petición; el padre no tiene que nombrarlas. Las decisiones editoriales que no pueden verificarse automáticamente, como la fidelidad de una explicación o la calidad de un ejercicio, se revisan con las fuentes.

## Uso local

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

`validate` comprueba la lengua y el curso, los identificadores, la cobertura de los objetivos en apuntes, ejercicios y tarjetas, el vocabulario de examen, las fuentes y los diagramas referenciados, y que cada ejercicio tenga respuesta. `verify` comprueba las cuatro páginas web por tema, sus PDF A4, las imágenes, las tarjetas interactivas y el orden de anversos y reversos. Estas comprobaciones no sustituyen la revisión pedagógica y visual.

Cada cambio enviado a `main` ejecuta esas comprobaciones en GitHub Actions y publica `site/` en GitHub Pages si terminan correctamente. El repositorio y la web son públicos: no subas fotografías o escaneos con datos personales. Transcribe el contenido necesario y registra el original privado como fuente sin adjuntarlo.
