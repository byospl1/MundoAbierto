#!/usr/bin/env python3
"""
Cerebro de Lecturas — pipeline Kindle -> Gemini -> Obsidian.

Flujo:
  1. Parsea todos los "My Clippings.txt" archivados en archivo/.
  2. Deduplica highlights (exactos y extendidos) y detecta novedades
     comparando con estado.json.
  3. Para cada libro con highlights nuevos, Gemini 2.5 Flash genera:
     resumen, ideas clave, conceptos (con definicion) y agrupacion tematica.
  4. Escribe/actualiza notas en el vault de Obsidian:
     Libros/, Conceptos/ (interconectadas con wikilinks) y Convergencias/.
  5. La sintesis semanal de convergencias cruza TODOS los libros.

Uso:
  procesar.py                  procesa novedades (+ sintesis si hubo cambios)
  procesar.py --sintesis       fuerza la sintesis semanal de convergencias
  procesar.py --vault RUTA     escribe en otro vault (pruebas)

Sin dependencias externas: solo biblioteca estandar.
"""

import json
import logging
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, date
from pathlib import Path

# ----------------------------------------------------------- configuracion

BASE = Path(__file__).resolve().parent
ARCHIVO = BASE / "archivo"          # copias de My Clippings.txt
TRABAJOS = Path.home() / "Documents/Trabajos Universidad"   # PDFs que Hugo deja
PDF_TEXTO = BASE / "pdf_texto"      # extractor Swift (PDFKit)
ESTADO_PATH = BASE / "estado.json"
LOG_DIR = BASE / "logs"
ENV_PATH = BASE / ".env"

VAULT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Obsidian/Lecturas"

MODELO = "gemini-flash-latest"
# subir este numero fuerza reanalizar todos los libros con Gemini (mejoras
# de prompt/esquema); los libros con version vieja se regeneran solo si hay
# cuota, sin degradarlos al modelo local
VERSION_ANALISIS = 2
URL_API = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{MODELO}:generateContent")
PAUSA_ENTRE_LLAMADAS = 7   # seg; capa gratuita = 10 peticiones/minuto
REINTENTOS = 4

# ----------------------------------------------------------------- logging

LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "cerebro.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("cerebro")


def notificar(mensaje: str, titulo: str = "Cerebro de Lecturas") -> None:
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{mensaje}" with title "{titulo}"'],
            capture_output=True, timeout=10)
    except Exception:
        pass


# ------------------------------------------------------------------ Gemini

def api_key() -> str | None:
    if ENV_PATH.exists():
        for linea in ENV_PATH.read_text().splitlines():
            if linea.strip().startswith("GEMINI_API_KEY="):
                clave = linea.split("=", 1)[1].strip().strip('"').strip("'")
                if clave and "PEGA_AQUI" not in clave:
                    return clave
    log.warning("Sin GEMINI_API_KEY en %s: usare solo el modelo local", ENV_PATH)
    return None


# cortacircuito: cuando la cuota diaria se agota, el resto de la corrida no
# vuelve a intentar Gemini (cada intento fallido cuesta ~2 min de reintentos)
_SIN_CUOTA = False


def gemini(prompt: str, clave: str) -> dict:
    """Llama a Gemini pidiendo JSON; reintenta ante 429/5xx."""
    global _SIN_CUOTA
    if _SIN_CUOTA:
        raise RuntimeError("Gemini sin cuota (cortacircuito abierto en esta corrida)")
    cuerpo = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json",
        },
    }).encode()

    for intento in range(1, REINTENTOS + 1):
        try:
            peticion = urllib.request.Request(
                URL_API,
                data=cuerpo,
                headers={"Content-Type": "application/json",
                         "x-goog-api-key": clave},
            )
            with urllib.request.urlopen(peticion, timeout=180) as resp:
                datos = json.load(resp)
            texto = datos["candidates"][0]["content"]["parts"][0]["text"]
            # por si el modelo envuelve el JSON en un bloque de codigo
            texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
            return json.loads(texto)
        except urllib.error.HTTPError as e:
            detalle = e.read().decode(errors="replace")[:300]
            # cuota DIARIA agotada: reintentar es inutil, abrir cortacircuito
            if e.code == 429 and "perday" in detalle.lower().replace("_", ""):
                _SIN_CUOTA = True
                raise RuntimeError(
                    f"Gemini HTTP 429 (cuota diaria agotada): {detalle}") from e
            if e.code in (429, 500, 502, 503) and intento < REINTENTOS:
                espera = 20 * intento
                log.warning("HTTP %s (intento %s), espero %ss: %s",
                            e.code, intento, espera, detalle)
                time.sleep(espera)
                continue
            if e.code == 429:   # 429 persistente tras todos los reintentos
                _SIN_CUOTA = True
            raise RuntimeError(f"Gemini HTTP {e.code}: {detalle}") from e
        except (KeyError, json.JSONDecodeError) as e:
            if intento < REINTENTOS:
                log.warning("Respuesta no parseable (intento %s): %s", intento, e)
                time.sleep(5)
                continue
            raise RuntimeError(f"Respuesta de Gemini no parseable: {e}") from e
    raise RuntimeError("Gemini: agotados los reintentos")


# ------------------------- modelo local de Apple (respaldo sin cuota) ------

class PuenteLocal:
    """Cliente del servidor JSONL ia_cerebro (FoundationModels, macOS 26+).

    El modelo local tiene 4096 tokens de contexto TOTAL (entrada+salida):
    los prompts deben mantenerse por debajo de ~7000 caracteres.
    """
    _proc = None
    _n = 0

    @classmethod
    def pedir(cls, prompt: str) -> dict:
        if cls._proc is None or cls._proc.poll() is not None:
            binario = BASE / "ia_cerebro"
            if not binario.exists():
                raise RuntimeError("falta el binario ia_cerebro (compilar con "
                                   "swiftc -O -parse-as-library ia_cerebro.swift)")
            cls._proc = subprocess.Popen(
                [str(binario)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, text=True, bufsize=1)
            listo = cls._proc.stdout.readline()   # {"id":0,...,"texto":"listo"}
            if '"ok":true' not in listo.replace(" ", ""):
                raise RuntimeError(f"modelo local no disponible: {listo.strip()}")
            log.info("Modelo local de Apple cargado")
        cls._n += 1
        cls._proc.stdin.write(json.dumps(
            {"id": cls._n, "prompt": prompt[:7000]}, ensure_ascii=False) + "\n")
        cls._proc.stdin.flush()
        resp = json.loads(cls._proc.stdout.readline())
        if not resp.get("ok"):
            raise RuntimeError(f"modelo local: {resp.get('error')}")
        texto = resp.get("texto", "")
        m = re.search(r"\{.*\}", texto, re.S)
        return json.loads(m.group(0) if m else texto)


def es_error_de_cuota(e: Exception) -> bool:
    s = str(e).lower()
    return ("429" in s or "quota" in s or "resource_exhausted" in s
            or "sin cuota" in s)


PROMPT_LOCAL_MAPA = """Del libro "{titulo}", estos son subrayados de un lector:

{texto}

Devuelve SOLO este JSON:
{{"resumen": "un parrafo breve con lo esencial de estos subrayados",
"conceptos": ["3 a 5 conceptos centrales, de 1-3 palabras"]}}"""

PROMPT_LOCAL_REDUCE = """Un lector subrayo el libro "{titulo}" de {autor}. \
Resumenes parciales de sus subrayados:

{parciales}

Conceptos detectados: {conceptos}

Devuelve SOLO este JSON:
{{"titulo_limpio": "titulo real y legible del libro, sin guiones bajos ni basura de nombre de archivo",
"autor_limpio": "autor limpio",
"resumen": "2 parrafos sintetizando lo que el lector extrajo",
"ideas_clave": ["4 a 7 ideas clave en frases completas"],
"conceptos": [{{"nombre": "concepto", "definicion": "definicion en una frase"}}]}}"""


def procesar_libro_local(info: dict, entradas: list[dict]) -> dict:
    """Analisis por libro con el modelo local (map-reduce por su contexto corto)."""
    lotes, lote, tam = [], [], 0
    for e in entradas:
        lote.append(e)
        tam += len(e["texto"])
        if tam > 4000:
            lotes.append(lote)
            lote, tam = [], 0
    if lote:
        lotes.append(lote)

    parciales, conceptos_map = [], []
    for l in lotes:
        texto = "\n".join(f"- {e['texto'][:400]}" for e in l)
        r = PuenteLocal.pedir(PROMPT_LOCAL_MAPA.format(
            titulo=info["titulo"][:80], texto=texto))
        parciales.append(r.get("resumen", ""))
        conceptos_map += [c for c in r.get("conceptos", []) if isinstance(c, str)]

    final = PuenteLocal.pedir(PROMPT_LOCAL_REDUCE.format(
        titulo=info["titulo"][:120], autor=info["autor"][:60],
        parciales="\n".join(f"- {p}" for p in parciales)[:4000],
        conceptos=", ".join(dict.fromkeys(conceptos_map))[:600]))
    final.setdefault("temas", [])            # sin agrupacion tematica en local
    final.setdefault("citas_destacadas", [])
    return final


def sintetizar_local(libros: dict) -> dict:
    lista = "\n".join(
        f"- {d['titulo']}: conceptos {', '.join(d.get('conceptos', [])[:6])}. "
        + " ".join(d.get("ideas_clave", [])[:2])[:300]
        for d in libros.values())
    prompt = ("Un lector tiene estos libros con sus conceptos e ideas:\n\n"
              + lista[:5000] + "\n\nDevuelve SOLO este JSON:\n"
              '{"convergencias": [{"titulo": "...", "descripcion": "parrafo: '
              'en que coinciden 2 o mas libros", "libros": ["titulos"]}], '
              '"tensiones": [], "preguntas_abiertas": ["2 o 3 preguntas"], '
              '"sintesis": "parrafo: que esta aprendiendo esta persona"}')
    return PuenteLocal.pedir(prompt)


# ------------------------------------------- parseo de My Clippings.txt

RE_TIPO_HIGHLIGHT = re.compile(r"subrayado|resaltado|highlight", re.I)
RE_TIPO_NOTA = re.compile(r"\bnota\b|\bnote\b", re.I)
RE_TIPO_MARCADOR = re.compile(r"marcador|bookmark", re.I)


def parsear_clippings(texto: str) -> list[dict]:
    entradas = []
    for bloque in texto.split("=========="):
        lineas = [l.strip("﻿ \r") for l in bloque.strip().splitlines()]
        lineas = [l for l in lineas if l.strip()]
        if len(lineas) < 3:
            continue
        cabecera, meta, contenido = lineas[0], lineas[1], "\n".join(lineas[2:]).strip()
        if RE_TIPO_MARCADOR.search(meta) or not contenido:
            continue
        if RE_TIPO_NOTA.search(meta) and not RE_TIPO_HIGHLIGHT.search(meta):
            tipo = "nota"
        elif RE_TIPO_HIGHLIGHT.search(meta):
            tipo = "highlight"
        else:
            continue
        m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", cabecera)
        titulo = (m.group(1) if m else cabecera).strip()
        autor = (m.group(2) if m else "Desconocido").strip()
        entradas.append({"titulo": titulo, "autor": autor,
                         "tipo": tipo, "texto": contenido, "meta": meta})
    return entradas


def deduplicar(entradas: list[dict]) -> list[dict]:
    """Quita duplicados exactos y highlights que otro highlight extiende."""
    unicos, vistos = [], set()
    for e in entradas:
        clave = (e["titulo"], e["tipo"], e["texto"])
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(e)
    finales = []
    for e in unicos:
        if e["tipo"] == "highlight" and any(
            o is not e and o["titulo"] == e["titulo"]
            and o["tipo"] == "highlight"
            and e["texto"] in o["texto"]
            for o in unicos
        ):
            continue  # version corta de un highlight extendido
        finales.append(e)
    return finales


# ---------------------------------------------------------------- utilidades

def slug_archivo(nombre: str) -> str:
    limpio = re.sub(r'[\\/:*?"<>|#^\[\]]', "", nombre).strip()
    return re.sub(r"\s+", " ", limpio)[:120] or "Sin titulo"


def casar_fuente(ref: str, conocidos) -> str:
    """Normaliza un titulo devuelto por la IA hacia uno de los titulos reales.

    El modelo a veces repite las etiquetas del contexto (p. ej.
    'LIBRO «X» (lectura)' o 'TU TRABAJO «Y» (produccion propia)'); esta funcion
    extrae el titulo y lo casa con la lista de fuentes conocidas.
    """
    m = re.search(r"[«\"']([^»\"']+)[»\"']", ref)
    limpio = (m.group(1) if m else ref)
    limpio = re.sub(r"^\s*(LIBRO|TU TRABAJO|TRABAJO)\b[:\s]*", "", limpio,
                    flags=re.I)
    limpio = re.sub(r"\s*\((lectura|produccion propia|producción propia)\)\s*$",
                    "", limpio, flags=re.I).strip()
    for real in conocidos:
        if real == limpio or real in limpio or limpio in real:
            return real
    return limpio or ref


def enlace_fuente(titulo: str, titulos_trabajos) -> str:
    """Wikilink a una fuente. Los trabajos se cualifican con su carpeta para
    no colisionar con libros que compartan titulo (p. ej. un libro y un
    trabajo llamados igual son notas distintas en Obsidian)."""
    slug = slug_archivo(titulo)
    if titulo in titulos_trabajos:
        return f"[[Trabajos/{slug}|{titulo}]]"
    return f"[[{slug}]]"


def clave_libro(titulo: str, autor: str) -> str:
    base = unicodedata.normalize("NFKD", f"{titulo}|{autor}").lower()
    return re.sub(r"\s+", " ", base)


def hash_entrada(e: dict) -> str:
    import hashlib
    return hashlib.sha256(f"{e['tipo']}|{e['texto']}".encode()).hexdigest()[:16]


def hash_archivo(ruta: Path) -> str:
    import hashlib
    return hashlib.sha256(ruta.read_bytes()).hexdigest()[:16]


def cargar_estado() -> dict:
    if ESTADO_PATH.exists():
        return json.loads(ESTADO_PATH.read_text(encoding="utf-8"))
    return {"libros": {}, "trabajos": {}, "conceptos": {},
            "ultima_sintesis": None}


def guardar_estado(estado: dict) -> None:
    tmp = ESTADO_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(ESTADO_PATH)


def normalizar_concepto(nombre: str) -> str:
    nombre = re.sub(r"\s+", " ", nombre.strip())
    return nombre[:1].upper() + nombre[1:] if nombre else nombre


# ------------------------------------------------------- generacion de notas

PROMPT_LIBRO = """Eres un asistente de lectura experto. Analiza los siguientes \
highlights (subrayados) y notas que un lector tomo del libro "{titulo}" de \
{autor}. Responde UNICAMENTE con JSON valido, en espanol, con esta estructura:

{{
 "titulo_limpio": "titulo real y legible del libro (el titulo dado puede ser un nombre de archivo con guiones bajos, editorial, hashes o basura: limpialo)",
 "autor_limpio": "nombre del autor, limpio",
 "resumen": "2-3 parrafos sintetizando lo que el lector extrajo del libro",
 "ideas_clave": ["5 a 10 ideas clave, frases completas y accionables"],
 "conceptos": [{{"nombre": "concepto central (1-3 palabras, sustantivo)", "definicion": "definicion en una frase segun el libro", "highlights": [indices de los 2-5 highlights que mejor sustentan este concepto]}}],
 "temas": [{{"titulo": "nombre del tema", "highlights": [indices de los highlights que pertenecen a este tema]}}],
 "citas_destacadas": [indices de las 3-5 citas mas potentes]
}}

Reglas:
- Entre 4 y 10 conceptos. Usa nombres genericos y reutilizables entre libros \
(p. ej. "Habitos", "Identidad", "Atencion"), no frases largas.
- "temas" debe cubrir TODOS los indices, cada indice en exactamente un tema.
- Los indices se refieren a la numeracion de la lista de abajo.

HIGHLIGHTS Y NOTAS:
{lista}
"""


def procesar_libro(info: dict, entradas: list[dict], clave: str) -> dict:
    lista = "\n".join(
        f"[{i}] ({'NOTA' if e['tipo'] == 'nota' else 'HIGHLIGHT'}) {e['texto']}"
        for i, e in enumerate(entradas))
    # el contexto de Flash es enorme, pero acotamos por sanidad
    if len(lista) > 400_000:
        lista = lista[:400_000]
    prompt = PROMPT_LIBRO.format(titulo=info["titulo"], autor=info["autor"],
                                 lista=lista)
    return gemini(prompt, clave)


# ---------------------------------------- trabajos universitarios (PDF) ----

EXT_TRABAJO = ("*.pdf", "*.docx")   # formatos de trabajo soportados


def extraer_pdf(ruta: Path) -> str:
    """Texto de un PDF con el extractor Swift (PDFKit). '' si no tiene texto."""
    if not PDF_TEXTO.exists():
        raise RuntimeError("falta el binario pdf_texto (compilar con "
                           "swiftc -O pdf_texto.swift -o pdf_texto)")
    r = subprocess.run([str(PDF_TEXTO), str(ruta)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode == 2:
        log.warning("PDF sin texto (¿escaneado?): %s", ruta.name)
        return ""
    if r.returncode != 0:
        raise RuntimeError(f"pdf_texto fallo en {ruta.name}: {r.stderr.strip()}")
    return r.stdout


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extraer_docx(ruta: Path) -> str:
    """Texto de un .docx con la biblioteca estandar (es un ZIP con XML)."""
    import zipfile
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(ruta) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        raise RuntimeError(f"docx ilegible {ruta.name}: {e}") from e
    raiz = ET.fromstring(xml)
    lineas = []
    for parrafo in raiz.iter(f"{_W}p"):
        partes = []
        for nodo in parrafo.iter():
            if nodo.tag == f"{_W}t" and nodo.text:
                partes.append(nodo.text)
            elif nodo.tag == f"{_W}tab":
                partes.append("\t")
            elif nodo.tag in (f"{_W}br", f"{_W}cr"):
                partes.append("\n")
        linea = "".join(partes).strip()
        if linea:
            lineas.append(linea)
    return "\n".join(lineas)


def extraer_trabajo(ruta: Path) -> str:
    """Extrae el texto de un trabajo segun su formato (.pdf o .docx)."""
    if ruta.suffix.lower() == ".docx":
        return extraer_docx(ruta)
    return extraer_pdf(ruta)


PROMPT_TRABAJO = """Eres un asistente academico. Analiza este trabajo \
universitario escrito por el propio lector (archivo: "{nombre}"). Responde \
UNICAMENTE con JSON valido, en espanol:

{{
 "titulo_limpio": "titulo real del trabajo (limpia el nombre de archivo)",
 "materia": "asignatura o area a la que pertenece (deducela; vacio si no se infiere)",
 "tipo": "ensayo | informe | resena | monografia | examen | otro",
 "tesis": "la tesis o argumento central del trabajo en 1-2 frases",
 "resumen": "2 parrafos: que sostiene el trabajo y como lo argumenta",
 "ideas_clave": ["4 a 8 afirmaciones o aportes centrales del trabajo"],
 "conceptos": [{{"nombre": "concepto (1-3 palabras, sustantivo, generico y reutilizable)", "definicion": "como usa el trabajo este concepto, en una frase", "citas": ["1-3 fragmentos textuales BREVES y EXACTOS del trabajo que usan el concepto"]}}],
 "autores_citados": ["autores o fuentes que el trabajo menciona o cita (vacio si ninguno)"]
}}

Reglas:
- 4 a 10 conceptos. Nombres genericos y reutilizables entre trabajos y libros \
(p. ej. "Identidad", "Trauma", "Poder"), no frases largas: asi este trabajo \
se conecta con las lecturas del lector que traten el mismo concepto.
- Las citas deben ser texto literal del trabajo, cortas (menos de 300 caracteres).

TEXTO DEL TRABAJO:
{texto}
"""


def procesar_trabajo(nombre: str, texto: str, clave: str) -> dict:
    if len(texto) > 500_000:
        texto = texto[:500_000]
    return gemini(PROMPT_TRABAJO.format(nombre=nombre, texto=texto), clave)


def procesar_trabajo_local(nombre: str, texto: str) -> dict:
    """Analisis con el modelo local (contexto 4096 tokens: map-reduce)."""
    trozos = [texto[i:i + 5000] for i in range(0, min(len(texto), 60_000), 5000)]
    parciales, conceptos = [], []
    for t in trozos:
        r = PuenteLocal.pedir(
            f'Fragmento de un trabajo universitario ("{nombre[:60]}"):\n\n{t}'
            '\n\nDevuelve SOLO este JSON: {"resumen": "que dice este fragmento",'
            ' "conceptos": ["2-4 conceptos clave, 1-3 palabras"]}')
        parciales.append(r.get("resumen", ""))
        conceptos += [c for c in r.get("conceptos", []) if isinstance(c, str)]
    final = PuenteLocal.pedir(
        f'Resumenes de un trabajo universitario titulado (archivo) "{nombre[:80]}":\n'
        + "\n".join(f"- {p}" for p in parciales)[:4000]
        + f'\n\nConceptos: {", ".join(dict.fromkeys(conceptos))[:500]}'
        '\n\nDevuelve SOLO este JSON: {"titulo_limpio": "titulo legible", '
        '"materia": "asignatura o vacio", "tesis": "tesis central", '
        '"resumen": "2 parrafos", "ideas_clave": ["4-6 ideas"], '
        '"conceptos": [{"nombre": "concepto", "definicion": "una frase", "citas": []}]}')
    final.setdefault("tipo", "")
    final.setdefault("autores_citados", [])
    return final


def escribir_nota_trabajo(vault: Path, info: dict, analisis: dict) -> None:
    carpeta = vault / "Trabajos"
    carpeta.mkdir(parents=True, exist_ok=True)
    conceptos = [normalizar_concepto(c["nombre"])
                 for c in analisis.get("conceptos", []) if c.get("nombre")]
    hoy = date.today().isoformat()
    titulo = analisis.get("titulo_limpio", "").strip() or info["titulo"]
    materia = analisis.get("materia", "").strip()

    lineas = ["---", f'titulo: "{titulo}"']
    if materia:
        lineas.append(f'materia: "{materia}"')
    if analisis.get("tipo"):
        lineas.append(f'tipo: {analisis["tipo"]}')
    lineas += [f"archivo: \"{info['archivo']}\"", f"actualizado: {hoy}",
               "tags:", "  - trabajo"]
    lineas += [f"  - {re.sub(r'[^0-9A-Za-zÀ-ÿ]+', '-', c).strip('-').lower()}"
               for c in conceptos]
    lineas += ["---", "", f"# ✍️ {titulo}", ""]
    meta = " · ".join(x for x in [materia, analisis.get("tipo", "")] if x)
    lineas += [f"*{meta}* · actualizado {hoy}" if meta
               else f"*actualizado {hoy}*", ""]

    if analisis.get("tesis", "").strip():
        lineas += ["> [!abstract] Tesis", f"> {analisis['tesis'].strip()}", ""]

    lineas += ["## Resumen", "", analisis.get("resumen", "").strip(), ""]

    lineas += ["## Ideas clave", ""]
    lineas += [f"- {i}" for i in analisis.get("ideas_clave", [])]
    lineas.append("")

    lineas += ["## Conceptos", ""]
    for c in analisis.get("conceptos", []):
        nombre = normalizar_concepto(c.get("nombre", ""))
        if nombre:
            lineas.append(f"- [[{nombre}]] — {c.get('definicion', '').strip()}")
    lineas.append("")

    autores = [a for a in analisis.get("autores_citados", []) if a]
    if autores:
        lineas += ["## Autores citados", ""]
        lineas += [f"- {a}" for a in autores]
        lineas.append("")

    ruta = carpeta / f"{slug_archivo(titulo)}.md"
    ruta.write_text("\n".join(lineas).rstrip() + "\n", encoding="utf-8")
    log.info("Nota de trabajo escrita: %s", ruta.name)


def escribir_nota_libro(vault: Path, info: dict, entradas: list[dict],
                        analisis: dict) -> None:
    carpeta = vault / "Libros"
    carpeta.mkdir(parents=True, exist_ok=True)
    conceptos = [normalizar_concepto(c["nombre"])
                 for c in analisis.get("conceptos", []) if c.get("nombre")]
    hoy = date.today().isoformat()

    lineas = [
        "---",
        f'titulo: "{info["titulo"]}"',
        f'autor: "{info["autor"]}"',
        f"highlights: {len(entradas)}",
        f"actualizado: {hoy}",
        "tags:",
        "  - libro",
    ]
    lineas += [f"  - {re.sub(r'[^0-9A-Za-zÀ-ÿ]+', '-', c).strip('-').lower()}"
               for c in conceptos]
    lineas += ["---", "", f"# {info['titulo']}", "",
               f"*{info['autor']}* · {len(entradas)} highlights · "
               f"actualizado {hoy}", ""]

    lineas += ["## Resumen", "", analisis.get("resumen", "").strip(), ""]

    lineas.append("## Ideas clave")
    lineas.append("")
    for idea in analisis.get("ideas_clave", []):
        lineas.append(f"- {idea}")
    lineas.append("")

    lineas.append("## Conceptos")
    lineas.append("")
    for c in analisis.get("conceptos", []):
        nombre = normalizar_concepto(c.get("nombre", ""))
        if nombre:
            lineas.append(f"- [[{nombre}]] — {c.get('definicion', '').strip()}")
    lineas.append("")

    citas = set(analisis.get("citas_destacadas", []))
    if citas:
        lineas.append("## Citas destacadas")
        lineas.append("")
        for i in sorted(citas):
            if isinstance(i, int) and 0 <= i < len(entradas):
                lineas.append(f"> {entradas[i]['texto']}")
                lineas.append("")

    lineas.append("## Highlights por tema")
    lineas.append("")
    cubiertos = set()
    for tema in analisis.get("temas", []):
        indices = [i for i in tema.get("highlights", [])
                   if isinstance(i, int) and 0 <= i < len(entradas)]
        if not indices:
            continue
        lineas.append(f"### {tema.get('titulo', 'Tema')}")
        lineas.append("")
        for i in indices:
            cubiertos.add(i)
            e = entradas[i]
            prefijo = "📝 " if e["tipo"] == "nota" else ""
            lineas.append(f"- {prefijo}{e['texto']}")
        lineas.append("")
    sueltos = [i for i in range(len(entradas)) if i not in cubiertos]
    if sueltos:
        lineas.append("### Otros")
        lineas.append("")
        for i in sueltos:
            lineas.append(f"- {entradas[i]['texto']}")
        lineas.append("")

    ruta = carpeta / f"{slug_archivo(info['titulo'])}.md"
    ruta.write_text("\n".join(lineas), encoding="utf-8")
    log.info("Nota de libro escrita: %s", ruta.name)


def reconstruir_indice_conceptos(estado: dict) -> None:
    """Reconstruye estado['conceptos'] desde libros Y trabajos universitarios.

    Cada concepto sabe en qué libros aparece (lo que Hugo LEE) y en qué
    trabajos (lo que Hugo ESCRIBE), con sus citas por fuente. Esto es lo que
    permite que una nota de concepto ponga en dialogo lectura y produccion.
    """
    anterior = estado.get("conceptos", {})
    indice: dict[str, dict] = {}

    def nodo_de(nombre: str) -> dict:
        return indice.setdefault(nombre, {
            "definicion": "", "libros": [], "trabajos": [],
            "citas_por_libro": {}, "citas_por_trabajo": {}})

    for d in estado.get("libros", {}).values():
        # libros del esquema viejo: solo nombres, sin citas
        detalles = d.get("conceptos_detalle") or [
            {"nombre": n,
             "definicion": anterior.get(n, {}).get("definicion", ""),
             "citas": []}
            for n in d.get("conceptos", [])]
        for c in detalles:
            nombre = normalizar_concepto(c.get("nombre", ""))
            if not nombre:
                continue
            nodo = nodo_de(nombre)
            if not nodo["definicion"] and c.get("definicion"):
                nodo["definicion"] = c["definicion"]
            if d["titulo"] not in nodo["libros"]:
                nodo["libros"].append(d["titulo"])
            if c.get("citas"):
                nodo["citas_por_libro"][d["titulo"]] = c["citas"]

    for t in estado.get("trabajos", {}).values():
        for c in t.get("conceptos_detalle", []):
            nombre = normalizar_concepto(c.get("nombre", ""))
            if not nombre:
                continue
            nodo = nodo_de(nombre)
            if not nodo["definicion"] and c.get("definicion"):
                nodo["definicion"] = c["definicion"]
            if t["titulo"] not in nodo["trabajos"]:
                nodo["trabajos"].append(t["titulo"])
            if c.get("citas"):
                nodo["citas_por_trabajo"][t["titulo"]] = c["citas"]

    estado["conceptos"] = indice


PROMPT_CONCEPTO = """Eres un sintetizador de conocimiento profundo. Una \
persona ha encontrado el concepto "{nombre}" en varias fuentes: LIBROS que \
lee y TRABAJOS universitarios que ella misma escribe. Abajo tienes lo que \
aporta cada fuente (definicion + citas textuales). Tu trabajo es HILAR de \
verdad: no resumas fuente por fuente, construye el dialogo entre ellas. Si \
hay a la vez lecturas y trabajos propios, presta especial atencion a como lo \
que la persona ESCRIBE dialoga con lo que LEE (lo aplica, lo contradice, se \
adelanta, o tiene un punto ciego que sus lecturas podrian llenar). Responde \
UNICAMENTE con JSON valido, en espanol:

{{
 "sintesis": "2 parrafos que integren las perspectivas: donde el mismo mecanismo aparece con distinto nombre, que anade cada fuente, y que comprension emerge del conjunto que ninguna fuente tiene por si sola. Nombra explicitamente el puente entre sus lecturas y sus propios trabajos si ambos aparecen",
 "por_fuente": [{{"fuente": "titulo exacto tal como aparece abajo", "posicion": "1-2 frases: la postura o uso especifico del concepto en esta fuente"}}],
 "friccion": "donde las perspectivas chocan o se matizan entre si (vacio si no hay choque real; no lo inventes)",
 "pregunta": "una pregunta genuinamente abierta que este cruce le deja"
}}

LO QUE DICE CADA FUENTE SOBRE "{nombre}":
{contexto}
"""


def escribir_notas_conceptos(vault: Path, estado: dict,
                             clave: str | None, solo_local: bool) -> None:
    carpeta = vault / "Conceptos"
    carpeta.mkdir(parents=True, exist_ok=True)
    cache = estado.setdefault("sintesis_conceptos", {})
    titulos_trab = {d["titulo"] for d in estado.get("trabajos", {}).values()}

    for nombre, datos in estado["conceptos"].items():
        libros = sorted(set(datos.get("libros", [])))
        trabajos = sorted(set(datos.get("trabajos", [])))
        citas_libro = datos.get("citas_por_libro", {})
        citas_trab = datos.get("citas_por_trabajo", {})
        n_fuentes = len(libros) + len(trabajos)
        hay_citas = bool(citas_libro or citas_trab)

        # sintesis profunda solo para conceptos en 2+ fuentes (libros o trabajos)
        profunda = None
        if n_fuentes >= 2 and hay_citas:
            huella = json.dumps([libros, trabajos, citas_libro, citas_trab],
                                sort_keys=True, ensure_ascii=False)
            previo = cache.get(nombre)
            if previo and previo.get("huella") == huella:
                profunda = previo["analisis"]
            elif clave and not solo_local:
                bloques = []
                for t in libros:
                    bloques.append(
                        f"— LIBRO «{t}» (lectura):\n"
                        + "\n".join(f"  > {c[:350]}"
                                    for c in citas_libro.get(t, [])[:5]))
                for t in trabajos:
                    bloques.append(
                        f"— TU TRABAJO «{t}» (produccion propia):\n"
                        + "\n".join(f"  > {c[:350]}"
                                    for c in citas_trab.get(t, [])[:5]))
                contexto = (f"definicion: {datos.get('definicion', '')}\n\n"
                            + "\n\n".join(bloques))
                try:
                    profunda = gemini(PROMPT_CONCEPTO.format(
                        nombre=nombre, contexto=contexto[:60_000]), clave)
                    cache[nombre] = {"huella": huella, "analisis": profunda}
                    guardar_estado(estado)
                    time.sleep(PAUSA_ENTRE_LLAMADAS)
                except Exception as e:
                    if not es_error_de_cuota(e):
                        log.exception("Fallo sintetizando concepto '%s'", nombre)
                    else:
                        log.warning("Sin cuota para el concepto '%s'; "
                                    "quedara pendiente", nombre)
                    profunda = (previo or {}).get("analisis")

        lineas = ["---", "tags:", "  - concepto",
                  f"libros: {len(libros)}", f"trabajos: {len(trabajos)}",
                  "---", "", f"# {nombre}", "",
                  datos.get("definicion", "").strip(), ""]

        if profunda:
            lineas += ["## Síntesis entre fuentes", "",
                       profunda.get("sintesis", "").strip(), ""]
            # compat: por_fuente (nuevo) o por_libro (cache viejo)
            posiciones = profunda.get("por_fuente") or profunda.get("por_libro", [])
            if posiciones:
                conocidos = libros + trabajos
                lineas += ["## Qué aporta cada fuente", ""]
                for p in posiciones:
                    ref = p.get("fuente") or p.get("libro", "")
                    ref = casar_fuente(ref, conocidos)
                    lineas.append(f"- **{enlace_fuente(ref, titulos_trab)}** — "
                                  f"{p.get('posicion', '').strip()}")
                lineas.append("")
            if profunda.get("friccion", "").strip():
                lineas += ["## ⚡ Fricción", "",
                           profunda["friccion"].strip(), ""]
            if profunda.get("pregunta", "").strip():
                lineas += ["## ❓ Para seguir pensando", "",
                           profunda["pregunta"].strip(), ""]

        if hay_citas:
            lineas += ["## Citas", ""]
            for titulo in libros:
                for c in citas_libro.get(titulo, [])[:5]:
                    lineas += [f"> {c}", f"> — 📖 [[{slug_archivo(titulo)}]]", ""]
            for titulo in trabajos:
                for c in citas_trab.get(titulo, [])[:5]:
                    lineas += [f"> {c}",
                               f"> — ✍️ {enlace_fuente(titulo, titulos_trab)}", ""]

        if libros:
            lineas += ["## 📖 En tus lecturas", ""]
            lineas += [f"- [[{slug_archivo(t)}]]" for t in libros]
            lineas.append("")
        if trabajos:
            lineas += ["## ✍️ En tus trabajos", ""]
            lineas += [f"- {enlace_fuente(t, titulos_trab)}" for t in trabajos]
        (carpeta / f"{slug_archivo(nombre)}.md").write_text(
            "\n".join(lineas).rstrip() + "\n", encoding="utf-8")
    log.info("Notas de concepto actualizadas: %s", len(estado["conceptos"]))


PROMPT_SINTESIS = """Eres un sintetizador de conocimiento profundo, al nivel \
de un buen ensayista. Una persona tiene dos tipos de material: LIBROS que lee \
y TRABAJOS universitarios que ella misma escribe. De cada uno tienes ideas \
clave, conceptos y citas textuales, mas sintesis previas de los conceptos \
compartidos. Tu trabajo es HILAR: encontrar los hilos no obvios que \
atraviesan lo que lee y lo que produce.

Exigencias:
- Nada de obviedades tematicas ("ambos hablan de la mente"). Busca mecanismos \
compartidos, causas comunes, un mismo fenomeno visto desde disciplinas \
distintas, o una fuente que explica el punto ciego de otra.
- Presta atencion especial al dialogo LECTURA↔PRODUCCION: donde sus trabajos \
aplican, anticipan o contradicen sus lecturas, y que lecturas nutririan lo \
que esta escribiendo. Este puente es lo mas valioso que puedes senalar.
- Cada hilo debe apoyarse en citas textuales de las de abajo, confrontadas.
- Las tensiones deben ser choques reales de tesis, no diferencias de tema.
- Escribe como para alguien inteligente que quiere pensar, no un informe.

Responde UNICAMENTE con JSON valido, en espanol:

{{
 "hilos": [{{"titulo": "nombre evocador del hilo", "desarrollo": "2-3 parrafos que hilen las fuentes: el mecanismo comun, que aporta cada una, y que se entiende al juntarlas que no se entendia por separado", "citas_confrontadas": [{{"libro": "titulo exacto de la fuente", "cita": "cita textual exacta de las de abajo"}}], "libros": ["titulos de las fuentes implicadas"]}}],
 "tensiones": [{{"titulo": "...", "descripcion": "parrafo: donde las tesis chocan de verdad y que hay en juego", "libros": ["..."]}}],
 "puente_lectura_produccion": "parrafo (vacio si no hay trabajos propios): como conversan lo que lee y lo que escribe — que aplica, que le falta leer para lo que escribe",
 "preguntas_abiertas": ["preguntas genuinas que solo surgen de ESTE cruce"],
 "tesis": "2 parrafos finales: que esta buscando entender esta persona a traves de todo esto (el patron que lo une), y hacia donde apunta — que le falta leer o pensar"
}}

Reglas: 2-4 hilos, cada uno con 2+ fuentes y 2+ citas confrontadas. Si solo \
hay una fuente, hilos/tensiones vacios y centra la tesis en ella.

FUENTES:
{lista}

SINTESIS DE CONCEPTOS COMPARTIDOS (trabajo previo, aprovechalo y superalo):
{conceptos_compartidos}
"""


def sintetizar(vault: Path, estado: dict, clave: str | None,
               solo_local: bool = False) -> None:
    libros = estado["libros"]
    trabajos = estado.get("trabajos", {})
    if not libros and not trabajos:
        log.info("Sin libros ni trabajos en estado; nada que sintetizar")
        return

    def bloque_fuente(d: dict, tipo: str) -> str:
        if tipo == "libro":
            cab = f"LIBRO (lectura): {d['titulo']} ({d['autor']})"
            etiqueta = "Citas textuales subrayadas:"
        else:
            cab = f"TU TRABAJO (produccion propia): {d['titulo']}"
            etiqueta = "Citas textuales del trabajo:"
        partes = [cab, f"Conceptos: {', '.join(d.get('conceptos', []))}",
                  "Ideas clave:"]
        partes += [f"- {i}" for i in d.get("ideas_clave", [])]
        citas = [c for det in d.get("conceptos_detalle", [])
                 for c in det.get("citas", [])]
        if citas:
            partes.append(etiqueta)
            partes += [f'> "{c[:350]}"' for c in dict.fromkeys(citas)]
        return "\n".join(partes)

    lista = "\n\n".join(
        [bloque_fuente(d, "libro") for d in libros.values()]
        + [bloque_fuente(d, "trabajo") for d in trabajos.values()])

    compartidos = []
    for nombre, entrada in estado.get("sintesis_conceptos", {}).items():
        analisis = entrada.get("analisis") or {}
        if analisis.get("sintesis"):
            compartidos.append(f"CONCEPTO {nombre}: {analisis['sintesis']}")
    conceptos_compartidos = "\n\n".join(compartidos) or "(aun no hay)"

    fuentes = {**libros, **trabajos}
    if solo_local or clave is None:
        resultado = sintetizar_local(fuentes)
    else:
        try:
            resultado = gemini(PROMPT_SINTESIS.format(
                lista=lista[:500_000],
                conceptos_compartidos=conceptos_compartidos[:100_000]), clave)
        except Exception as e:
            if not es_error_de_cuota(e):
                raise
            log.warning("Gemini sin cuota para la sintesis; uso el modelo local")
            resultado = sintetizar_local(fuentes)

    titulos_reales = [d["titulo"] for d in fuentes.values()]
    titulos_trab = {d["titulo"] for d in trabajos.values()}

    def resolver_titulo(t: str) -> str:
        """Mapea el titulo que devuelve la IA al titulo real de la nota."""
        if t in titulos_reales:
            return t
        casado = casar_fuente(t, titulos_reales)
        if casado in titulos_reales:
            return casado
        sin_autor = re.sub(r"\s*\([^)]*\)\s*$", "", casado).strip()
        for real in titulos_reales:
            if real == sin_autor or real in casado or casado in real:
                return real
        return sin_autor or t

    def enlace_real(t: str) -> str:
        return enlace_fuente(resolver_titulo(t), titulos_trab)

    hoy = date.today()
    anio, semana, _ = hoy.isocalendar()
    carpeta = vault / "Convergencias"
    carpeta.mkdir(parents=True, exist_ok=True)

    n_l, n_t = len(libros), len(trabajos)
    alcance = f"{n_l} libros" + (f" y {n_t} trabajos" if n_t else "")
    lineas = ["---", "tags:", "  - convergencia",
              f"semana: {anio}-S{semana:02d}", "---", "",
              f"# Convergencias — semana {semana}, {anio}", "",
              f"*Generado el {hoy.isoformat()} cruzando {alcance}.*", ""]

    def seccion(titulo: str, items: list, emoji: str, con_citas: bool) -> None:
        if not items:
            return
        lineas.append(f"## {emoji} {titulo}")
        lineas.append("")
        for it in items:
            refs = " · ".join(enlace_real(t) for t in it.get("libros", []))
            lineas.append(f"### {it.get('titulo', '')}")
            lineas.append("")
            lineas.append((it.get("desarrollo") or it.get("descripcion", ""))
                          .strip())
            lineas.append("")
            if con_citas:
                for cc in it.get("citas_confrontadas", []):
                    if cc.get("cita"):
                        lineas.append(f"> {cc['cita'].strip()}")
                        lineas.append(f"> — {enlace_real(cc.get('libro', ''))}")
                        lineas.append("")
            if refs:
                lineas.append(f"Fuentes: {refs}")
                lineas.append("")

    # "hilos" (formato profundo de Gemini) o "convergencias" (respaldo local)
    titulo_hilos = ("Hilos que atraviesan lo que lees y escribes" if trabajos
                    else "Hilos que atraviesan tus lecturas")
    seccion(titulo_hilos,
            resultado.get("hilos") or resultado.get("convergencias", []),
            "🧵", con_citas=True)
    seccion("Tensiones", resultado.get("tensiones", []), "⚡", con_citas=False)

    puente = (resultado.get("puente_lectura_produccion") or "").strip()
    if puente:
        lineas += ["## 🔀 Lo que lees ↔ lo que escribes", "", puente, ""]

    preguntas = resultado.get("preguntas_abiertas", [])
    if preguntas:
        lineas.append("## ❓ Preguntas abiertas")
        lineas.append("")
        lineas += [f"- {p}" for p in preguntas]
        lineas.append("")

    lineas += ["## 🧭 Tesis de tu momento lector", "",
               (resultado.get("tesis") or resultado.get("sintesis", "")).strip(),
               ""]

    ruta = carpeta / f"{anio}-S{semana:02d}.md"
    ruta.write_text("\n".join(lineas), encoding="utf-8")
    estado["ultima_sintesis"] = hoy.isoformat()
    log.info("Sintesis semanal escrita: %s", ruta.name)


def podar_notas_huerfanas(vault: Path, estado: dict) -> None:
    """Borra notas generadas cuyo origen ya no existe (renombres, conceptos
    que desaparecieron). Solo toca archivos con el frontmatter del sistema
    (tags libro/trabajo/concepto): las notas propias del usuario se respetan.
    """
    esperados = {
        "Libros": {f"{slug_archivo(d['titulo'])}.md"
                   for d in estado.get("libros", {}).values()},
        "Trabajos": {f"{slug_archivo(d['titulo'])}.md"
                     for d in estado.get("trabajos", {}).values()},
        "Conceptos": {f"{slug_archivo(n)}.md" for n in estado.get("conceptos", {})},
    }
    marcas = ("  - libro", "  - trabajo", "  - concepto")
    for carpeta, nombres in esperados.items():
        d = vault / carpeta
        if not d.exists():
            continue
        for f in d.glob("*.md"):
            if f.name in nombres:
                continue
            try:
                cabecera = f.read_text(encoding="utf-8", errors="replace")[:400]
            except OSError:
                continue
            if cabecera.startswith("---") and any(m in cabecera for m in marcas):
                f.unlink()
                log.info("Nota huérfana eliminada: %s/%s", carpeta, f.name)


def escribir_dashboard(vault: Path, estado: dict) -> None:
    libros = sorted(estado["libros"].values(),
                    key=lambda d: d.get("actualizado", ""), reverse=True)
    trabajos = sorted(estado.get("trabajos", {}).values(),
                      key=lambda d: d.get("actualizado", ""), reverse=True)
    # conceptos mas transversales: los que cruzan mas fuentes (libros+trabajos)
    def alcance(kv):
        d = kv[1]
        return len(d.get("libros", [])) + len(d.get("trabajos", []))
    conceptos = sorted(estado["conceptos"].items(), key=alcance, reverse=True)
    # puente: conceptos que aparecen a la vez en lecturas Y en trabajos
    puente = [(n, d) for n, d in estado["conceptos"].items()
              if d.get("libros") and d.get("trabajos")]
    convergencias = sorted((vault / "Convergencias").glob("*.md"),
                           reverse=True) if (vault / "Convergencias").exists() else []

    lineas = ["---", "tags:", "  - dashboard", "---", "",
              "# 🧠 Cerebro de Lecturas", "",
              f"{len(libros)} libros · {len(trabajos)} trabajos · "
              f"{len(estado['conceptos'])} conceptos · "
              f"última síntesis: {estado.get('ultima_sintesis') or '—'}", ""]

    if puente:
        lineas += ["## 🔀 Conceptos que unen lo que lees y lo que escribes", ""]
        for nombre, d in sorted(puente, key=lambda x: alcance((None, x[1])),
                                reverse=True):
            lineas.append(f"- [[{nombre}]] — 📖 {len(d['libros'])} · "
                          f"✍️ {len(d['trabajos'])}")
        lineas.append("")

    lineas += ["## 📖 Libros", ""]
    for d in libros:
        lineas.append(f"- [[{slug_archivo(d['titulo'])}]] — {d['autor']} "
                      f"({d.get('n_highlights', '?')} highlights)")
    if trabajos:
        titulos_trab = {d["titulo"] for d in trabajos}
        lineas += ["", "## ✍️ Trabajos", ""]
        for d in trabajos:
            materia = f" · {d['materia']}" if d.get("materia") else ""
            lineas.append(f"- {enlace_fuente(d['titulo'], titulos_trab)}{materia}")

    lineas += ["", "## Conceptos más transversales", ""]
    for nombre, datos in conceptos[:20]:
        n = len(datos.get("libros", [])) + len(datos.get("trabajos", []))
        lineas.append(f"- [[{nombre}]] ({n} fuentes)")
    if convergencias:
        lineas += ["", "## Convergencias recientes", ""]
        lineas += [f"- [[{c.stem}]]" for c in convergencias[:8]]
    (vault / "🧠 Inicio.md").write_text("\n".join(lineas) + "\n",
                                        encoding="utf-8")


# -------------------------------------------------------------------- main

def main() -> None:
    args = sys.argv[1:]
    forzar_sintesis = "--sintesis" in args
    solo_local = "--local" in args
    vault = VAULT
    if "--vault" in args:
        vault = Path(args[args.index("--vault") + 1]).expanduser()
    vault.mkdir(parents=True, exist_ok=True)

    log.info("=== inicio (vault=%s) ===", vault)
    estado = cargar_estado()

    # 1. leer y unificar todos los clippings archivados
    entradas = []
    for f in sorted(ARCHIVO.glob("*.txt")):
        entradas += parsear_clippings(
            f.read_text(encoding="utf-8-sig", errors="replace"))
    entradas = deduplicar(entradas)
    log.info("Highlights únicos tras deduplicar: %s", len(entradas))

    # 2. agrupar por libro y detectar novedades
    por_libro: dict[str, dict] = {}
    for e in entradas:
        k = clave_libro(e["titulo"], e["autor"])
        por_libro.setdefault(k, {"titulo": e["titulo"], "autor": e["autor"],
                                 "entradas": []})["entradas"].append(e)

    clave = api_key()

    pendientes = []
    for k, grupo in por_libro.items():
        hashes = sorted(hash_entrada(e) for e in grupo["entradas"])
        previo = estado["libros"].get(k)
        # reprocesar si hay highlights nuevos, si la nota se genero con el
        # modelo local (respaldo), o si el esquema de analisis mejoro; las
        # mejoras solo se intentan cuando hay Gemini disponible
        mejorable = (previo is not None and clave is not None and not solo_local
                     and (previo.get("motor") == "local"
                          or previo.get("version", 1) < VERSION_ANALISIS))
        if previo is None or sorted(previo.get("hashes", [])) != hashes \
                or mejorable:
            pendientes.append((k, grupo, hashes))

    # 2b. escanear la carpeta de trabajos universitarios (PDF y DOCX)
    pendientes_trab = []
    estado.setdefault("trabajos", {})
    if TRABAJOS.exists():
        docs = sorted(f for patron in EXT_TRABAJO for f in TRABAJOS.glob(patron))
        for doc in docs:
            k = slug_archivo(doc.stem).lower()
            try:
                h = hash_archivo(doc)
            except Exception:
                log.exception("No pude leer %s", doc.name)
                continue
            previo = estado["trabajos"].get(k)
            mejorable = (previo is not None and clave is not None
                         and not solo_local
                         and (previo.get("motor") == "local"
                              or previo.get("version", 1) < VERSION_ANALISIS))
            if previo is None or previo.get("hash") != h or mejorable:
                pendientes_trab.append((k, doc, h))

    hubo_cambios = bool(pendientes) or bool(pendientes_trab)
    if not hubo_cambios and not forzar_sintesis:
        log.info("Sin novedades; fin.")
        return

    # 3. procesar libros con novedades
    errores = 0
    for k, grupo, hashes in pendientes:
        try:
            log.info("Procesando libro: %s (%s highlights)",
                     grupo["titulo"], len(grupo["entradas"]))
            # si es solo una mejora de una nota ya existente y no hay cuota,
            # se deja la nota como esta en vez de degradarla al modelo local
            previo = estado["libros"].get(k)
            es_mejora = (previo is not None
                         and sorted(previo.get("hashes", [])) == list(hashes))
            motor = "local"
            if solo_local or clave is None:
                if es_mejora:
                    continue
                analisis = procesar_libro_local(grupo, grupo["entradas"])
            else:
                try:
                    analisis = procesar_libro(grupo, grupo["entradas"], clave)
                    motor = "gemini"
                except Exception as e:
                    if not es_error_de_cuota(e):
                        raise
                    if es_mejora:
                        log.warning("Sin cuota para mejorar '%s'; quedara "
                                    "pendiente", grupo["titulo"])
                        continue
                    log.warning("Gemini sin cuota; uso el modelo local de Apple")
                    analisis = procesar_libro_local(grupo, grupo["entradas"])
            # titulo estable: una vez limpiado por Gemini (version>=2), se
            # conserva; si no, Gemini re-nombra un poco distinto cada vez y
            # se duplican las notas en el vault
            if previo and previo.get("titulo") and previo.get("version", 0) >= 2:
                grupo["titulo"] = previo["titulo"]
                grupo["autor"] = previo.get("autor") or grupo["autor"]
            else:
                grupo["titulo"] = (analisis.get("titulo_limpio") or "").strip() \
                    or grupo["titulo"]
                grupo["autor"] = (analisis.get("autor_limpio") or "").strip() \
                    or grupo["autor"]
            # si el nombre de la nota cambia, borrar la anterior
            nota = f"{slug_archivo(grupo['titulo'])}.md"
            nota_vieja = (previo or {}).get("nota")
            if nota_vieja and nota_vieja != nota:
                (vault / "Libros" / nota_vieja).unlink(missing_ok=True)
                log.info("Nota renombrada; borro la vieja: %s", nota_vieja)
            escribir_nota_libro(vault, grupo, grupo["entradas"], analisis)

            detalle = []
            for c in analisis.get("conceptos", []):
                nombre = normalizar_concepto(c.get("nombre", ""))
                if not nombre:
                    continue
                citas = [grupo["entradas"][i]["texto"]
                         for i in c.get("highlights", [])
                         if isinstance(i, int)
                         and 0 <= i < len(grupo["entradas"])]
                detalle.append({"nombre": nombre,
                                "definicion": c.get("definicion", ""),
                                "citas": citas[:5]})
            estado["libros"][k] = {
                "titulo": grupo["titulo"], "autor": grupo["autor"],
                "nota": nota,
                "hashes": hashes,
                "conceptos": [c["nombre"] for c in detalle],
                "conceptos_detalle": detalle,
                "ideas_clave": analisis.get("ideas_clave", []),
                "n_highlights": len(grupo["entradas"]),
                "actualizado": date.today().isoformat(),
                "motor": motor,
                "version": VERSION_ANALISIS if motor == "gemini" else 0,
            }
            guardar_estado(estado)
            if motor == "gemini":       # el modelo local no tiene rate limit
                time.sleep(PAUSA_ENTRE_LLAMADAS)
        except Exception:
            errores += 1
            log.exception("Fallo procesando '%s'", grupo["titulo"])

    # 3b. procesar trabajos universitarios con novedades
    for k, doc, h in pendientes_trab:
        try:
            texto = extraer_trabajo(doc)
            # umbral: PDFs escaneados o presentaciones basadas en imagenes dan
            # poco o ningun texto; no vale la pena analizarlos
            if len(texto.strip()) < 200:
                log.warning("Trabajo con muy poco texto (%s chars), lo salto: "
                            "%s", len(texto.strip()), doc.name)
                continue
            log.info("Procesando trabajo: %s (%s chars)", doc.name, len(texto))
            previo = estado["trabajos"].get(k)
            es_mejora = previo is not None and previo.get("hash") == h
            motor = "local"
            if solo_local or clave is None:
                if es_mejora:
                    continue
                analisis = procesar_trabajo_local(doc.stem, texto)
            else:
                try:
                    analisis = procesar_trabajo(doc.stem, texto, clave)
                    motor = "gemini"
                except Exception as e:
                    if not es_error_de_cuota(e):
                        raise
                    if es_mejora:
                        log.warning("Sin cuota para mejorar '%s'; pendiente",
                                    doc.name)
                        continue
                    log.warning("Gemini sin cuota; modelo local para el trabajo")
                    analisis = procesar_trabajo_local(doc.stem, texto)

            if previo and previo.get("titulo") and previo.get("version", 0) >= 2:
                titulo_estable = previo["titulo"]
                analisis["titulo_limpio"] = titulo_estable
            else:
                titulo_estable = (analisis.get("titulo_limpio", "").strip()
                                  or doc.stem)
            info = {"titulo": titulo_estable, "archivo": doc.name}
            nota = f"{slug_archivo(titulo_estable)}.md"
            nota_vieja = (previo or {}).get("nota")
            if nota_vieja and nota_vieja != nota:
                (vault / "Trabajos" / nota_vieja).unlink(missing_ok=True)
                log.info("Nota de trabajo renombrada; borro la vieja: %s",
                         nota_vieja)
            escribir_nota_trabajo(vault, info, analisis)

            detalle = []
            for c in analisis.get("conceptos", []):
                nombre = normalizar_concepto(c.get("nombre", ""))
                if not nombre:
                    continue
                citas = [str(x) for x in c.get("citas", []) if x][:5]
                detalle.append({"nombre": nombre,
                                "definicion": c.get("definicion", ""),
                                "citas": citas})
            estado["trabajos"][k] = {
                "titulo": info["titulo"], "archivo": doc.name, "hash": h,
                "nota": nota,
                "materia": analisis.get("materia", ""),
                "conceptos": [c["nombre"] for c in detalle],
                "conceptos_detalle": detalle,
                "ideas_clave": analisis.get("ideas_clave", []),
                "actualizado": date.today().isoformat(),
                "motor": motor,
                "version": VERSION_ANALISIS if motor == "gemini" else 0,
            }
            guardar_estado(estado)
            if motor == "gemini":       # el modelo local no tiene rate limit
                time.sleep(PAUSA_ENTRE_LLAMADAS)
        except Exception:
            errores += 1
            log.exception("Fallo procesando trabajo '%s'", doc.name)

    # 4. conceptos + sintesis + dashboard
    reconstruir_indice_conceptos(estado)
    escribir_notas_conceptos(vault, estado, clave, solo_local)
    podar_notas_huerfanas(vault, estado)
    if hubo_cambios or forzar_sintesis:
        try:
            sintetizar(vault, estado, clave, solo_local)
        except Exception:
            errores += 1
            log.exception("Fallo en la sintesis")
    escribir_dashboard(vault, estado)
    guardar_estado(estado)

    partes_res = []
    if pendientes:
        partes_res.append(f"{len(pendientes)} libros")
    if pendientes_trab:
        partes_res.append(f"{len(pendientes_trab)} trabajos")
    resumen = ((", ".join(partes_res) or "sin novedades") + " · actualizado"
               + (f", {errores} errores" if errores else ""))
    log.info("=== fin: %s ===", resumen)
    notificar(resumen + " ✓" if not errores else resumen + " ⚠️")


if __name__ == "__main__":
    main()
