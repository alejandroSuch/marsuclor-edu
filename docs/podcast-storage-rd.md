# I+D del almacenamiento de podcasts (18 de octubre de 2026)

## Estado medido y límites

El guion, receta TTS y huella viven en Git; R2 Standard conserva parejas inmutables `<sha256>.mp3`/`.json`. `sync` consulta R2 antes de sintetizar, valida metadatos y checksum completo, y verifica HEAD/Range del Worker antes de publicar Pages. El Worker transmite el cuerpo, permite GET/HEAD, descarga, ETag y rangos. GitHub Actions serializa publicaciones. El hash incluye texto narrado tras correcciones, pausas, modelo y checksums, versión Piper, parámetros de síntesis/codificación y versión del pipeline; excluye títulos, rutas y fechas. Las pruebas no revelaron un defecto que justifique cambiar el esquema.

En este entorno no hay credenciales R2 ni manifiesto de la última publicación: **no se midió el bucket real**. Además, `gh secret list` y `gh variable list` no muestran configuración Cloudflare en el repositorio. La [ejecución del 18 de octubre](https://github.com/alejandroSuch/marsuclor-edu/actions/runs/39117239984) falló por falta de credenciales bootstrap; la web pública consultada ese día muestra transcripciones sin reproductor y `podcast-manifest.json` responde 404. Esto indica que la publicación de audio aún no está operativa, pero **no prueba que el bucket esté vacío**. Sí hay un MP3 local de Geografía de 674,56 s y 10.793.839 bytes, más una muestra de 40,49 s y 648.716 bytes, ambos a unos 128 kbps. El almacenamiento original se implantó en el PR [#3](https://github.com/alejandroSuch/marsuclor-edu/pull/3); no existe un issue de almacenamiento anterior que actualizar.

| Servicio | Cuota o límite documentado | Coste posterior / observación |
| --- | --- | --- |
| R2 Standard | 10 GB-mes, 1 millón de operaciones A y 10 millones B al mes | 0,015 USD/GB-mes; 4,50 USD/millón A; 0,36 USD/millón B; salida gratuita |
| R2 Infrequent Access | Sin cuota gratuita | 0,01 USD/GB-mes; A/B más caras; recuperación 0,01 USD/GB; mínimo 30 días |
| Objetos R2 | Sin límite documentado de objetos/bucket; clave 1.024 bytes; metadatos 8.192 bytes | Objeto hasta 5 TiB, subida simple hasta 5 GiB |
| Workers Free | 100.000 solicitudes/día; 10 ms CPU/invocación; 128 MB; 50 subpeticiones externas/invocación | Error 1027 posible al agotar cuota; cada lectura R2 tiene su propia cuota |
| Actions | Runners estándar gratuitos sin límite de minutos en repos públicos; job hasta 6 h | Free: 20 jobs simultáneos, caché de repo 10 GB y artefactos 500 MB |

Fuentes: [precios R2](https://developers.cloudflare.com/r2/pricing/), [límites R2](https://developers.cloudflare.com/r2/platform/limits/), [límites Workers](https://developers.cloudflare.com/workers/platform/limits/), [límites Actions](https://docs.github.com/en/actions/reference/limits), [runners públicos](https://docs.github.com/en/actions/reference/runners/github-hosted-runners). `ListObjects` es operación A; `HeadObject` y `GetObject`, B. El inventario semanal lista páginas de hasta 1.000 objetos y lee cada JSON pequeño, sin descargar MP3. Para 2.000 versiones, aproximadamente cuatro A y 2.000 B por ejecución, más reintentos. R2 factura GB-mes según promedios diarios y redondeos: el porcentaje de carga útil de la CLI es un indicador aproximado, no una factura.

## Crecimiento estimado

Supuesto: una versión de 12 minutos, bitrate constante, sin metadatos. MB decimales. Multiplica por el número medio de versiones conservadas; suma JSON y posibles formatos futuros aparte.

| Podcasts/versiones | 64 kbps | 96 kbps | 128 kbps |
| ---: | ---: | ---: | ---: |
| 25 | 144 MB | 216 MB | 288 MB |
| 100 | 576 MB | 864 MB | 1.152 MB |
| 250 | 1.440 MB | 2.160 MB | 2.880 MB |
| 500 | 2.880 MB | 4.320 MB | 5.760 MB |
| 1.000 | 5.760 MB | 8.640 MB | 11.520 MB |

Por ejemplo, 500 episodios con dos versiones medias a 128 kbps ocuparían 11,52 GB antes de metadatos. Se recodificó **solo una copia temporal** de la muestra local: 64 kbps produjo 324.379 bytes; 96 kbps, 486.548; 128 kbps original, 648.716. Es una comparación de tamaño, no de calidad auditiva, porque la fuente ya era MP3 con pérdida. No se recomienda cambiar el bitrate sin generar muestras desde audio sin comprimir y escuchar en móvil y auriculares. Los MP3 existentes permanecen intactos.

## Mejoras evaluadas

| Problema y solución | Beneficio | Complejidad / riesgo | Decisión |
| --- | --- | --- | --- |
| R2 sin inventario → CLI paginada con estadísticas, versiones y retención | Capacidad y anomalías visibles | Baja / lecturas A+B | Implementada |
| Manifiesto solo temporal → copia verificada en Pages | Distinguir la versión desplegada | Baja / URLs ya públicas | Implementada |
| Sin aviso de capacidad → workflow semanal separado | Alertas sin bloquear Pages | Baja / umbral aproximado | Implementada |
| Caché Worker inactiva → `cache.enabled` | Menos invocaciones y lecturas R2 | Baja / medir en producción | Implementada |
| Bitrate 128 kbps → prueba auditiva 64/96/128 desde fuente sin pérdida | Ahorro potencial | Media / degradación audible | Pospuesta |
| Token CI de escritura también para informe → credencial de solo lectura | Menor impacto de exposición | Baja / alta inicial | Propuesta |
| Falta backup independiente → exportación/copia externa | Recuperar borrados y corrupción | Media / coste y operación | Propuesta |
| Tráfico y facturación sin alertas exactas → GraphQL Analytics | Avisos de peticiones y operaciones | Media / nuevo token y ruido | Pospuesta |
| Lifecycle de borrado | Ahorro | Alta / pérdida de versiones | Descartada |

[Workers Caching](https://developers.cloudflare.com/workers/cache/configuration/) funciona en `workers.dev` con activación explícita. Cloudflare elimina `Range` antes de invocar un Worker cacheable, almacena el `200` completo y devuelve `206` desde la caché; un `206` generado por el Worker no se almacena. El manejo actual de rangos sigue como respaldo para invocaciones directas. Vitest y Wrangler dry-run validan código/configuración, pero no demuestran un `HIT` real; medir `Cf-Cache-Status` tras desplegar.

Una primera petición de rango con caché fría puede leer el MP3 completo para llenar la caché, en vez de solo el fragmento. Para los episodios actuales (~11 MB) cabe holgadamente bajo el límite documentado de caché de respuesta del plan Free (512 MB); la salida R2 no se factura por bytes. Medir latencia y tasa de aciertos antes de atribuir ahorro de operaciones. [Límites de respuesta Workers](https://developers.cloudflare.com/workers/platform/limits/).

## Operación automatizada y diagnóstico

```sh
.venv-tts/bin/python scripts/build.py storage stats --backend r2 --manifest https://alejandrosuch.github.io/marsuclor-edu/podcast-manifest.json
.venv-tts/bin/python scripts/build.py storage list --backend r2 --manifest https://alejandrosuch.github.io/marsuclor-edu/podcast-manifest.json --format json
.venv-tts/bin/python scripts/build.py storage versions geografia/1-eso/els-fonaments-del-planeta-terra --backend r2 --manifest https://alejandrosuch.github.io/marsuclor-edu/podcast-manifest.json
.venv-tts/bin/python scripts/build.py storage retention --backend r2 --manifest https://alejandrosuch.github.io/marsuclor-edu/podcast-manifest.json --format json
.venv-tts/bin/python scripts/build.py storage stats --backend local
```

`--manifest` acepta archivo o URL HTTPS. Sin él, `published` queda desconocido y `retention` se niega a clasificar versiones. `unreferenced` solo significa «ausente del manifiesto actual»: puede seguir enlazado desde versiones antiguas de Pages o sitios externos. Se indican pares incompletos, JSON inválidos, tamaños discordantes, objetos inesperados y referencias publicadas ausentes. No se releen todos los MP3 para el inventario: `tts verify <tema>` realiza la comprobación criptográfica completa de la versión actual. Un 403 o error de red interrumpe el informe; nunca se interpreta como bucket vacío. `--format json` sirve para automatizaciones.

`storage.yml` corre semanalmente o a petición, con el mismo código y los secretos S3 previstos para la publicación. Solo lee y usa `contents: read` en GitHub. Falla a partir del 70 % de 10 GB de payload listado, o ante anomalías; distingue niveles 85 % y 95 %. Las notificaciones de fallos dependen de la configuración de GitHub. El workflow no bloquea `pages.yml`. Estas alertas **no** miden GB-mes facturados, sobrecarga de metadatos interna, peticiones Worker ni consumo por otras aplicaciones de la cuenta. R2 expone [métricas GraphQL](https://developers.cloudflare.com/r2/platform/metrics-analytics/) de almacenamiento, operaciones y ancho de banda; Workers ofrece [métricas](https://developers.cloudflare.com/workers/observability/metrics-and-analytics/) y [logs gratuitos de retención limitada](https://developers.cloudflare.com/workers/observability/logs/workers-logs/). El token S3 no da acceso a Analytics; la automatización de esas métricas queda pendiente de justificación y credencial propia.

En el estado observado, las credenciales aún **no están configuradas**: el workflow semanal fallará al intentar R2 hasta completar el setup único de `docs/podcast-cloudflare.md`. El workflow de Pages también lo necesita. Es un bloqueo operativo existente, no un coste ni una acción manual recurrente.

## Recuperación

Pérdida de caché local o Actions: `tts sync --all --download-models` reconstruye el manifiesto desde guiones y R2. Pages: el workflow regenera HTML/PDF y manifiesto. Worker: bootstrap/Wrangler versionados lo redespliegan; `/health` y `tts sync` prueban disponibilidad y rutas. Pérdida de configuración temporal: el código está versionado, pero habrá que reemitir secretos perdidos. Borrado R2: un hash no restaura bytes; solo una copia validada permite recuperar. Corrupción: checksum y par JSON/MP3 detectan algunas anomalías, pero la copia única en R2 sigue siendo un punto de fallo. La redundancia de R2 no constituye backup independiente y no impide borrados. [Durabilidad](https://developers.cloudflare.com/r2/reference/durability/), [borrado irreversible](https://developers.cloudflare.com/r2/buckets/delete-buckets/). [Bucket locks](https://developers.cloudflare.com/r2/buckets/bucket-locks/) pueden proteger objetos, pero requieren decidir retención y permisos. [Lifecycle](https://developers.cloudflare.com/r2/buckets/object-lifecycles/) puede borrarlos automáticamente y no se activó. Una copia externa, ubicación, cifrado y coste requieren una decisión aparte.

No se probó una reconstrucción real de cuenta/bucket ni la caché en producción. Validarlas en una cuenta aislada y sobre el Worker desplegado antes de dar esas garantías por demostradas. El [fallo de CI del 18 de octubre](https://github.com/alejandroSuch/marsuclor-edu/actions/runs/39160265708) también reveló que Chromium se instalaba después de ejecutar una prueba que lo usa; el PR mueve esa instalación antes de los tests, sin añadir una segunda descarga.
