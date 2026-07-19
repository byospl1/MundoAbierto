# 🧠 Cerebro de Lecturas

Pipeline automático: **Kindle → IA híbrida → vault "Lecturas" de Obsidian**.

Motor híbrido: **Gemini** (`gemini-flash-latest`, capa gratuita) como motor
principal, y el **modelo local de Apple Intelligence** (FoundationModels,
binario `ia_cerebro`) como respaldo automático cuando Google agota la cuota
(HTTP 429) o falta la API key. Los libros procesados con el modelo local se
marcan (`"motor": "local"` en `estado.json`) y Gemini los regenera con más
calidad en cuanto vuelve a haber cuota. `--local` fuerza el modo 100% local.

## Cómo funciona

1. **Conecta el Kindle por USB.** Un agente de macOS (`StartOnMount`) detecta el
   montaje, copia `My Clippings.txt` a `archivo/` y lanza el procesamiento.
   Verás una notificación. No hay que hacer nada más.
2. **El pipeline** (`procesar.py`) parsea y deduplica los highlights, y para cada
   libro con novedades pide a Gemini: resumen, ideas clave, conceptos con
   definición y agrupación temática de los highlights.
### Trabajos universitarios (PDF)

Deja tus **PDF o DOCX** en **`~/Documents/Trabajos Universidad/`**. En cada
ejecución (diaria, semanal o al conectar el Kindle) el pipeline detecta los
documentos nuevos o modificados, extrae su texto (PDF con `pdf_texto` —PDFKit
nativo, binario Swift; DOCX con la biblioteca estándar de Python, sin
dependencias), y Gemini analiza: tesis, materia, ideas clave, conceptos y
autores citados. Se escribe una nota en `Trabajos/`. Los PDF escaneados sin
capa de texto se saltan con aviso.

Lo importante: **tus trabajos entran al mismo grafo de conceptos que tus
lecturas**. Si un concepto (p. ej. "Trauma") aparece a la vez en un libro que
lees y en un trabajo que escribes, su nota de concepto pone ambos en diálogo
—qué aporta cada uno, dónde tu escritura tiene un punto ciego que tus lecturas
llenan— y la síntesis semanal incluye una sección **"Lo que lees ↔ lo que
escribes"**.

### En el vault `Obsidian/Lecturas` se escribe:
   - `Libros/` — una nota por libro
   - `Conceptos/` — una nota por concepto. Si el concepto aparece en 2+
     libros, Gemini escribe una **síntesis profunda**: qué dice cada libro,
     dónde friccionan, las citas textuales confrontadas y una pregunta
     abierta. Es el corazón del "cerebro" en el graph view.
   - `Trabajos/` — una nota por trabajo universitario (tesis, ideas, conceptos)
   - `Convergencias/` — síntesis semanal profunda: **hilos** que atraviesan
     lecturas y trabajos (con citas confrontadas), tensiones reales entre
     tesis, el puente "lo que lees ↔ lo que escribes", preguntas abiertas y
     la "tesis de tu momento lector"
   - `🧠 Inicio.md` — dashboard general (destaca los conceptos que unen lo que
     lees con lo que escribes)
4. **Cada domingo a las 10:00** el agente semanal regenera la síntesis;
   **cada día a las 9:30** un agente ligero reintenta lo pendiente (p. ej.
   mejoras que quedaron esperando cuota de Gemini) y sale al instante si no
   hay nada que hacer.

## Requisitos

- API key gratuita de Gemini: https://aistudio.google.com/apikey
  → pégala en `.env` como `GEMINI_API_KEY=...`
- El Kindle Scribe (y todos los 2024+) usa MTP: la captura se hace con
  `libmtp` (instalado via Homebrew). El agente corre cada 2 minutos, detecta
  el Kindle en el bus USB y captura UNA vez por conexión. Nota: cierra el
  "USB File Manager" de Amazon (Send to Kindle) momentáneamente porque
  monopoliza la interfaz MTP; se relanza solo al volver a abrir Send to Kindle.
- Kindles antiguos que montan como disco USB también funcionan (StartOnMount).

## Comandos útiles

```bash
python3 ~/Documents/kindle-cerebro/procesar.py             # procesar novedades
python3 ~/Documents/kindle-cerebro/procesar.py --sintesis  # forzar síntesis
tail -f ~/Documents/kindle-cerebro/logs/cerebro.log        # ver actividad
```

## Robustez interna

- **Títulos estables**: el título limpio que Gemini asigna la primera vez se
  conserva en re-análisis (evita notas duplicadas por renombres). Si un
  título cambia de todos modos, la nota vieja se borra (campo `nota` en
  `estado.json`).
- **Poda de huérfanas**: al final de cada corrida se eliminan las notas de
  `Libros/`, `Trabajos/` y `Conceptos/` que ya no corresponden al estado.
  Solo se tocan archivos con el frontmatter del sistema; las notas creadas
  por el usuario en esas carpetas se respetan.
- **Cortacircuito de cuota**: si la cuota diaria de Gemini se agota a mitad
  de corrida, el resto de llamadas ni se intenta (antes cada una gastaba
  ~2 min en reintentos) y se usa el respaldo local o se pospone la mejora.

## Permisos de macOS (TCC)

Los agentes de launchd no pueden tocar `~/Documents` ni iCloud Drive con
binarios del sistema (macOS los deniega en silencio). Por eso los tres
agentes se lanzan a través de `~/Library/CerebroLecturas/cerebro_agente`
(fuente: `agente.swift`), un envoltorio con identidad TCC propia a la que
macOS sí concede acceso (diálogo de permiso una sola vez).
**Si se recompila `cerebro_agente`, macOS vuelve a pedir el permiso.**
Sus logs de launchd van a `~/Library/CerebroLecturas/logs/`.

## Archivos

- `procesar.py` — pipeline principal (sin dependencias, solo Python de sistema)
- `capturar.sh` — se dispara al montar un volumen; archiva los clippings
- `launchagents/` — los dos agentes instalados en `~/Library/LaunchAgents`
- `estado.json` — memoria del sistema (qué se procesó ya); borrarlo = reprocesar todo
- `archivo/` — copias históricas de My Clippings.txt
- `logs/` — registros

## Desinstalar

```bash
launchctl bootout gui/$(id -u)/com.hugo.cerebro-lecturas.captura
launchctl bootout gui/$(id -u)/com.hugo.cerebro-lecturas.semanal
rm ~/Library/LaunchAgents/com.hugo.cerebro-lecturas.*.plist
```
