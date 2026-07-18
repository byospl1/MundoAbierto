# 🐉 La Torre del Erudito — Mazmorras del Saber

Tu vault de Obsidian convertido en un juego de mazmorras personal. Cada libro es una mazmorra, cada concepto un monstruo, y tu progreso de aprendizaje se refleja en el juego automáticamente cada semana.

## Cómo funciona

```
Obsidian (Mac) ──plugin obsidian-git──▶ GitHub (carpeta vault/)
                                            │ push
                                            ▼
                              GitHub Action: build-world.mjs
                              (Gemini genera preguntas nuevas)
                                            │ commit world.json
                                            ▼
                              GitHub Pages publica el juego
                                            │
                                            ▼
                    index.html (iPhone) ── progreso en Firebase
```

- **Mazmorras** = tus libros (`vault/Libros/*.md`). Salas = temas; el **jefe** aparece al dominar todas las salas.
- **Grimorio** = flashcards de tus conceptos (`vault/Conceptos/*.md`) con repaso espaciado (Leitner).
- **Dragón de las Convergencias** = jefe semanal generado desde `vault/Convergencias/` (se desbloquea con 2 jefes vencidos).
- **Torre** = tu base: decórala con el oro que ganas estudiando. Retos diarios y racha 🔥.
- El progreso se guarda en tu Firebase existente (`paginadepsicologia-6b6f8`, colección `mazmorra_arcana`, un doc por email) y en el dispositivo como respaldo.

## Puesta en marcha (una sola vez)

### 1. Repositorio
Crea un repo (p. ej. `torre-erudito`) y sube TODO este proyecto: `index.html`, `world.json`, `manifest.webmanifest`, `sw.js`, `icon-*.png`, `vault/`, `scripts/`, `.github/`.

### 2. GitHub Pages
Settings → Pages → Source: **Deploy from a branch** → `main` / root. En 1–2 min el juego queda en `https://TU-USUARIO.github.io/torre-erudito/`.

### 3. API key de Gemini (preguntas automáticas)
Consigue una key gratis en https://aistudio.google.com/apikey y guárdala en el repo:
Settings → Secrets and variables → Actions → **New repository secret** → nombre `GEMINI_API_KEY`.
Sin la key el generador usa un modo de respaldo (preguntas más simples desde tus "Ideas clave").

### 4. Actualización manual del vault (flujo elegido)
Cada semana que quieras actualizar el mundo:
1. Comprime tu vault de Obsidian en un .zip.
2. Súbelo al chat de Claude ("actualiza el vault con este zip") — Claude reemplaza `vault/` y regenera `world.json`; descarga el proyecto y vuelve a subirlo al repo, **o** copia tú mismo las notas nuevas dentro de `vault/` del repo (arrastrándolas en github.com → Add file → Upload files).
3. Al hacer commit, la GitHub Action regenera `world.json` sola (con Gemini) y Pages republica en ~2 min.

(Alternativa automática: el plugin obsidian-git, si algún día quieres cero pasos.)

### 5. Firebase — solo verifica reglas
Ya usa tu proyecto existente. En Firestore → Reglas añade:
```
match /mazmorra_arcana/{doc} {
  allow read, write: if true;
}
```

### 6. iPhone
Abre la URL en Safari → Compartir → **Añadir a pantalla de inicio**. Funciona como app y guarda caché offline.

## Flujo semanal (automático)

1. Lees y tomas notas en Obsidian → obsidian-git hace push solo.
2. La Action detecta cambios en `vault/`, regenera `world.json` con Gemini (solo los libros nuevos/modificados — hay caché) y hace commit.
3. Pages republica. Al abrir el juego verás: *"⚔ ¡Nuevos portales! El mundo se actualizó"*.
4. También corre cada lunes por si acaso, y puedes lanzarla a mano (Actions → Run workflow).

## 🔄 Automatización semanal (elige una)

Todo el vault (libros + `vault/Uni/`) se convierte en preguntas con IA y se sube solo. Hay dos caminos que hacen lo mismo cada **domingo**; usa el que prefieras.

### A) Automático en la nube — sin tu Mac (recomendado)
La GitHub Action `build-world.yml` corre **cada domingo** (y en cada push del vault). Regenera `world.json` desde lo que haya en `vault/` y republica. Solo necesita el secret `GEMINI_API_KEY`. Si usas obsidian-git, tus notas ya llegan solas al repo.

### B) Un clic en tu Mac — para los trabajos de la universidad
Los archivos de la uni que viven solo en tu Mac los sube este ejecutable:

1. **Prepara** (una vez): copia `.env.example` como `.env` y pon tu `GEMINI_API_KEY`. Si tus trabajos están en otra carpeta, define `UNI_SOURCE` (por defecto `~/Documents/Universidad`).
2. **Doble clic en `Actualizar.command`.** El script copia tus `.md` de la universidad a `vault/Uni/`, genera las preguntas con Gemini (sin repetir nada), respalda el mundo anterior en `backups/`, y hace commit + push → GitHub Pages republica. *(Primera vez, si macOS lo bloquea: clic derecho → Abrir.)*

**Hacerlo automático cada domingo** (opción B sin tocar nada): edita la ruta dentro de `com.hugo.torre-erudito.plist`, cópialo a `~/Library/LaunchAgents/` y cárgalo con `launchctl load` (instrucciones dentro del archivo). Tu Mac lo correrá solo los domingos a las 18:00.

> La carpeta de la universidad solo la ve la opción B (tu Mac). La opción A ve lo que ya esté en el repo. Puedes usar las dos.

## Notas para la uni

**¿En qué momento se integran?** Cuando quieras, en cualquier subida semanal. Crea la carpeta `vault/Uni/` y mete ahí tus notas de la universidad con el mismo formato que los libros (`## Resumen`, `## Ideas clave`, `## Conceptos`). El generador ya lee `vault/Uni/` automáticamente (además de `vault/Libros/`): cada documento nuevo se vuelve una mazmorra. **No se repiten preguntas ni conceptos** entre libros — el generador deduplica por texto, así que al añadir material nuevo no verás preguntas repetidas.

## Mecánicas de estudio (adherencia)

- **Maná 🔮**: sembrar consume maná; lo recargas *estudiando* (cartas +2, salas +8, repaso). Para hacer crecer la granja hay que aprender.
- **Meta diaria 🎯** (anillo en el HUD): 10 acciones de estudio al día → +120 🪙 +50 XP.
- **Repaso inteligente**: el grimorio prioriza tus cartas más falladas y ofrece una *misión de refuerzo*.
- **Ruta de niveles**: cada nivel da oro y premios (retroactivo). **Cofre de racha** creciente + 🛡 escudo que salva tu racha un día.
- **Jefes con 2 fases** y **mazmorras que se archivan** al dominarlas (sus preguntas vuelven solo como recordatorio en el repaso diario).

## Mecánicas de granja

Árboles frutales, edificios (corral, establo, invernadero, granero, molino, pozo, **biblioteca sagrada** donde se acomodan tus libros leídos, altar), **trabajador** contratable, **forraje → estiércol → abono**, **depredadores** (cuervos/zorro/plaga) que repeles con defensas o respondiendo, **barco mercante** cada 2 días, clima, eventos, cofres, mascotas legendarias, crafteo y logros.

## Notas para la uni (formato)

## Ajustes rápidos (dentro de index.html)

- Recompensas: `XP_SALA`, `ORO_JEFE`, `XP_DRAGON`, etc.
- Retos diarios: `QUEST_POOL`. Tienda: `DECOS`, `POCIONES`, precios de atuendo.
- Intervalos de repaso espaciado: `SRS_DIAS = [0,1,2,4,7,15]` (días por caja).
