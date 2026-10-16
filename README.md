# Martina Edu

Materials d'estudi per assignatura, curs i tema. El primer tema és una **versió provisional** de geografia de 1r d'ESO: encara no s'ha localitzat el PDF original. Cada ampliació indica la font.

## Ús local

### Amb Docker

Cal tindre Docker instal·lat i en marxa. Des de l'arrel del repositori:

```sh
docker compose up --build
```

La validació, la generació Quarto i els PDF s'executen abans d'obrir el servidor en [http://localhost:8000](http://localhost:8000). Per incorporar canvis al contingut, para el procés i executa `docker compose up` de nou. Els fitxers generats viuen en volums de Docker; `docker compose down -v` els elimina. La primera construcció descarrega Quarto i Chromium i pot tardar uns minuts.

### Sense Docker

Cal Python 3.11+, Quarto 1.6+ i, per als PDF, Chromium de Playwright.

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

Obri `site/index.html` o inicia `python -m http.server 8000 -d site`. A cada pàgina hi ha un botó per imprimir en A4. Els PDF es generen a `site/pdf/` des de la mateixa pàgina HTML. Per crear un altre tema, copia l'estructura de `content/geografia/1-eso/els-fonaments-del-planeta-terra/` i actualitza els fitxers YAML/Markdown. `python scripts/build.py validate` detecta camps, objectius i fonts que falten.

## Estructura

- `config/subjects.yml`: idiomes per assignatura. Els configurables es concreten per tema.
- `content/<assignatura>/<curs>/<tema>/`: font del material, independent del disseny.
- `scripts/build.py`: validació, preparació per a Quarto i impressió PDF.
- `styles/`: presentació web, A4 i targetes interactives.
- `generated/` i `site/`: eixides generades, ignorades per Git.
- `.agents/skills/`: tres fluxos de treball per als agents.

El curs es dedueix de la ruta i del fitxer del tema: `1-eso` / `1r d'ESO`, `4-eso` / `4t d'ESO`, etc. El generador no fixa un nivell global. El camp `teaching_language` es comprova contra la configuració de l'assignatura. Per a francès, anglès i educació física, es tria explícitament en cada tema.

## Publicació

Cada `push` a `main` valida, renderitza amb Quarto, genera els PDF i desplega `site/` a GitHub Pages. En una nova pàgina de GitHub, selecciona **Settings → Pages → Build and deployment → GitHub Actions** si encara no s'ha activat automàticament.

No puges fotografies o escanejos amb dades personals al repositori públic. Revisa i transcriu el material primer; registra el fitxer privat com a origen sense adjuntar-lo.
