#!/usr/bin/env node
/**
 * build-world.mjs — Genera world.json desde el vault de Obsidian.
 * - Lee vault/Libros/*.md, vault/Conceptos/*.md y vault/Convergencias/*.md
 * - Si hay GEMINI_API_KEY, genera salas/preguntas nuevas con Gemini (solo para libros nuevos o modificados; usa .world-cache.json)
 * - Sin API key: genera preguntas de respaldo a partir de las "Ideas clave"
 * Uso: GEMINI_API_KEY=xxx node scripts/build-world.mjs
 */
import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';

const VAULT = process.env.VAULT_DIR || 'vault';
const OUT = 'world.json';
const CACHE_FILE = '.world-cache.json';
const API_KEY = process.env.GEMINI_API_KEY || '';
const MODEL = process.env.GEMINI_MODEL || 'gemini-flash-latest';

const slug = s => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40);
const hash = s => createHash('sha256').update(s).digest('hex').slice(0, 16);

function parseNote(txt) {
  const fm = {};
  const m = txt.match(/^---\n([\s\S]*?)\n---/);
  if (m) for (const line of m[1].split('\n')) {
    const kv = line.match(/^(\w[\w-]*):\s*"?(.*?)"?\s*$/);
    if (kv) fm[kv[1]] = kv[2];
  }
  const sections = {};
  let cur = '_pre';
  for (const line of txt.replace(/^---\n[\s\S]*?\n---/, '').split('\n')) {
    const h = line.match(/^##\s+(.*)/);
    if (h) { cur = h[1].replace(/[^\wÀ-ÿ ]/g, '').trim(); sections[cur] = []; continue; }
    (sections[cur] = sections[cur] || []).push(line);
  }
  const sec = name => (sections[Object.keys(sections).find(k => k.toLowerCase().includes(name)) || ''] || []).join('\n').trim();
  const bullets = name => sec(name).split('\n').filter(l => l.trim().startsWith('- ')).map(l => l.replace(/^- /, '').trim());
  return { fm, sec, bullets };
}

function listMd(dir) {
  const p = path.join(VAULT, dir);
  if (!existsSync(p)) return [];
  return readdirSync(p).filter(f => f.endsWith('.md')).map(f => ({ name: f.replace(/\.md$/, ''), txt: readFileSync(path.join(p, f), 'utf8') }));
}

/* ---------- Gemini ---------- */
function parseLooseJSON(txt) {
  let s = String(txt || '').trim();
  s = s.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/i, '');   // quita cercas markdown
  const a = s.indexOf('{'), b = s.lastIndexOf('}');
  if (a >= 0 && b > a) s = s.slice(a, b + 1);
  try { return JSON.parse(s); } catch (e) {}
  // Reparación: escapa comillas dobles dentro de valores de texto (causa típica de "Según "Lett"...")
  let fixed = s.replace(/:\s*"((?:[^"\\]|\\.)*)"/g, (m, inner) => ': "' + inner.replace(/\\"/g, '"').replace(/"/g, '\\"') + '"');
  return JSON.parse(fixed);
}
async function gemini(prompt, retry = true) {
  const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent?key=${API_KEY}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }], generationConfig: { responseMimeType: 'application/json', temperature: 0.5 } })
  });
  if (!res.ok) throw new Error('Gemini ' + res.status + ': ' + (await res.text()).slice(0, 300));
  const data = await res.json();
  const txt = data.candidates?.[0]?.content?.parts?.[0]?.text || '';
  try { return parseLooseJSON(txt); }
  catch (e) {
    if (retry) { console.warn('  (JSON inválido, reintentando una vez…)'); return gemini(prompt + '\n\nIMPORTANTE: responde SOLO JSON válido. Escapa las comillas dobles dentro de los textos como \\". No uses comillas tipográficas.', false); }
    throw e;
  }
}

const SPRITES = ['ghost', 'slime', 'bat', 'golem', 'snake', 'imp', 'eye', 'cultist', 'wolf'];
const BOSS_SPRITES = ['lich', 'golem', 'wolf'];

async function dungeonWithGemini(book) {
  const nRooms = Math.max(1, Math.min(5, Math.ceil(book.ideas.length / 2)));
  const prompt = `Eres diseñador de un juego educativo de mazmorras (D&D) en español. A partir de estas notas de lectura, crea una mazmorra.

LIBRO: ${book.titulo} — ${book.autor}
RESUMEN: ${book.resumen}
IDEAS CLAVE:\n${book.ideas.map(i => '- ' + i).join('\n')}
CONCEPTOS:\n${book.conceptos.map(c => '- ' + c).join('\n')}
CITAS:\n${book.citas.slice(0, 4).map(c => '> ' + c.slice(0, 300)).join('\n')}

Devuelve SOLO JSON con este esquema exacto:
{"nombre":"nombre evocador de mazmorra (2-4 palabras, fantasía medieval)","salas":[{"nombre":"nombre de sala temático","monstruo":"nombre de monstruo fantástico relacionado al tema","sprite":"uno de: ${SPRITES.join(', ')}","preguntas":[{"q":"pregunta clara sobre el contenido","o":["opción correcta","distractor plausible","distractor plausible"],"c":0,"x":"explicación de 1 frase basada en las notas"}]}],"jefe":{"nombre":"nombre de jefe épico","sprite":"uno de: ${BOSS_SPRITES.join(', ')}","preguntas":[...igual, 4 preguntas de síntesis...]}}

Reglas: ${nRooms} salas, 3 preguntas por sala. Las preguntas SOLO sobre el contenido dado, rigurosas académicamente. "c" es el índice de la correcta (varíalo). Tono: épico con humor ligero.`;
  return await gemini(prompt);
}

async function dragonWithGemini(conv, semana) {
  const prompt = `Juego educativo de mazmorras en español. Crea el jefe semanal "Dragón de las Convergencias" a partir de esta nota que cruza varios libros:\n\n${conv.slice(0, 3000)}\n\nDevuelve SOLO JSON: {"intro":"1 frase épica sobre qué une estos saberes","preguntas":[{"q":"...","o":["correcta","distractor","distractor"],"c":0,"x":"..."}]} con 3-4 preguntas sobre las convergencias. Varía el índice "c".`;
  const d = await gemini(prompt);
  return { nombre: 'Dragón de las Convergencias', semana, intro: d.intro, preguntas: d.preguntas };
}

/* ---------- Fallback sin API ---------- */
function fallbackDungeon(book, allBooks) {
  const others = allBooks.filter(b => b.id !== book.id).flatMap(b => b.ideas);
  const distract = correct => {
    const pool = others.filter(o => o !== correct).sort(() => Math.random() - .5);
    return [pool[0] || 'Ninguna de las anteriores', pool[1] || 'No se menciona en el libro'];
  };
  const mkQ = (idea, n) => ({ q: `(${book.titulo.split(':')[0]} · ${n}) ¿Cuál de estas ideas es correcta?`, o: [idea, ...distract(idea)], c: 0, x: 'Idea clave de tus notas de ' + book.titulo + '.' });
  const salas = [];
  for (let i = 0; i < book.ideas.length; i += 3) {
    const ideas = book.ideas.slice(i, i + 3);
    salas.push({ id: `${book.id}-${salas.length + 1}`, nombre: `Sala ${salas.length + 1} de ${book.titulo.split(':')[0]}`, monstruo: 'Guardián del Saber', sprite: SPRITES[salas.length % SPRITES.length], preguntas: ideas.map((idea, k) => mkQ(idea, `S${salas.length + 1}-${k + 1}`)) });
  }
  const jefeQs = book.conceptos.slice(0, 4).map(c => {
    const [nombre, def] = c.split('—').map(s => s.trim());
    const otherDefs = book.conceptos.filter(x => x !== c).map(x => (x.split('—')[1] || '').trim()).filter(Boolean);
    return { q: `¿Qué es "${(nombre || '').replace(/\[|\]/g, '')}"?`, o: [def || nombre, otherDefs[0] || '—', otherDefs[1] || '—'], c: 0, x: 'Concepto de ' + book.titulo + '.' };
  }).filter(q => q.o[0]);
  return { nombre: 'Mazmorra de ' + book.titulo.split(':')[0], salas, jefe: { nombre: 'Guardián Final', sprite: 'lich', preguntas: jefeQs.length ? jefeQs : salas[0].preguntas } };
}

/* ---------- Main ---------- */
const cache = existsSync(CACHE_FILE) ? JSON.parse(readFileSync(CACHE_FILE, 'utf8')) : {};
const prev = existsSync(OUT) ? JSON.parse(readFileSync(OUT, 'utf8')) : { mazmorras: [] };

const books = [...listMd('Libros'), ...listMd('Uni')].map(({ name, txt }) => {
  const { fm, sec, bullets } = parseNote(txt);
  const b = {
    id: slug(fm.titulo || name), titulo: fm.titulo || name, autor: fm.autor || 'Desconocido',
    resumen: sec('resumen').split('\n\n')[0] || '', ideas: bullets('ideas'), conceptos: bullets('concepto'),
    citas: sec('citas').split('\n').filter(l => l.startsWith('>')).map(l => l.replace(/^>\s*/, '')),
  };
  // Hash SOLO del contenido de estudio (ideas/conceptos/citas/resumen), no del frontmatter:
  // así fechas o metadatos que cambian en cada sync de Obsidian no fuerzan re-generar (ahorra tokens).
  // Solo se re-generará cuando agregues o edites entradas reales.
  b.hash = hash(JSON.stringify([b.titulo, b.autor, b.resumen, b.ideas, b.conceptos, b.citas]));
  return b;
});

// ── Dedup global de preguntas: normaliza el enunciado; descarta repetidas entre libros/salas/jefes ──
const _seenQ = new Set();
const normQ = s => String(s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
function dedupPreguntas(qs) {
  const out = [];
  for (const q of (qs || [])) {
    const k = normQ(q.q);
    if (!k || _seenQ.has(k)) continue;
    _seenQ.add(k); out.push(q);
  }
  return out;
}

const mazmorras = [];
for (const book of books) {
  const cached = cache[book.id];
  const prevDg = prev.mazmorras.find(m => m.id === book.id);
  if (cached && cached.hash === book.hash && prevDg) {
    console.log('= sin cambios:', book.titulo);
    // aún así registra sus preguntas en el set para no duplicarlas en libros nuevos
    prevDg.salas.forEach(s => s.preguntas.forEach(q => _seenQ.add(normQ(q.q))));
    if (prevDg.jefe) prevDg.jefe.preguntas.forEach(q => _seenQ.add(normQ(q.q)));
    mazmorras.push(prevDg);
    continue;
  }
  let dg;
  if (API_KEY) {
    try { console.log('~ generando con Gemini:', book.titulo); dg = await dungeonWithGemini(book); }
    catch (e) { console.warn('  Gemini falló (' + e.message + '), usando fallback'); }
  }
  if (!dg) { console.log('~ fallback local:', book.titulo); dg = fallbackDungeon(book, books); }
  dg.salas.forEach((s, i) => { s.id = s.id || `${book.id}-${i + 1}`; s.preguntas = dedupPreguntas(s.preguntas); });
  dg.salas = dg.salas.filter(s => s.preguntas.length);
  if (dg.jefe) dg.jefe.preguntas = dedupPreguntas(dg.jefe.preguntas);
  mazmorras.push({ id: book.id, libro: book.titulo, autor: book.autor, nombre: dg.nombre, resumen: book.resumen.slice(0, 260), salas: dg.salas, jefe: dg.jefe });
  cache[book.id] = { hash: book.hash };
}

// Cartas (flashcards) desde Conceptos/ — dedup por 'frente' normalizado
const _seenC = new Set();
const cartas = listMd('Conceptos').map(({ name, txt }) => {
  const { sec } = parseNote(txt);
  const body = txt.replace(/^---\n[\s\S]*?\n---/, '').split('\n').filter(l => l.trim() && !l.startsWith('#') && !l.startsWith('- ['))[0] || '';
  const libros = (sec('libros que tratan') || '').match(/\[\[(.*?)\]\]/);
  return { id: 'c-' + slug(name), frente: name, dorso: body.trim(), libro: libros ? libros[1].split(':')[0] : '' };
}).filter(c => {
  if (!c.dorso) return false;
  const k = normQ(c.frente);
  if (_seenC.has(k)) return false;
  _seenC.add(k); return true;
});

// Dragón desde la convergencia más reciente
let dragon = prev.dragon || null;
const convs = listMd('Convergencias').sort((a, b) => a.name.localeCompare(b.name));
if (convs.length) {
  const last = convs[convs.length - 1];
  const semana = (parseNote(last.txt).fm.semana) || last.name;
  const convHash = hash(last.txt);
  if (!cache._dragon || cache._dragon.hash !== convHash || !dragon) {
    if (API_KEY) {
      try { console.log('~ dragón con Gemini:', semana); dragon = await dragonWithGemini(last.txt, semana); }
      catch (e) { console.warn('  Gemini falló para el dragón:', e.message); }
    }
    if (!dragon || dragon.semana !== semana) {
      const frases = last.txt.split('\n').filter(l => l.trim() && !l.startsWith('#') && !l.startsWith('-') && !l.startsWith('*') && !l.includes('[[') && !l.startsWith('---') && !l.includes(':')).slice(0, 4);
      dragon = { nombre: 'Dragón de las Convergencias', semana, intro: 'El dragón cruza los saberes de tus libros.', preguntas: frases.slice(0, 3).map(f => ({ q: '¿Cuál es una convergencia de esta semana?', o: [f.trim(), 'Los libros no se relacionan', 'No hubo síntesis esta semana'], c: 0, x: 'De tu nota ' + semana + '.' })) };
    }
    cache._dragon = { hash: convHash };
  }
}

const version = dragon?.semana || new Date().toISOString().slice(0, 10);
const world = { version, generado: new Date().toISOString().slice(0, 10), mazmorras, dragon, cartas };
writeFileSync(OUT, JSON.stringify(world, null, 1));
writeFileSync(CACHE_FILE, JSON.stringify(cache, null, 1));
console.log(`✓ world.json v${version}: ${mazmorras.length} mazmorras, ${cartas.length} cartas${dragon ? ', dragón ' + dragon.semana : ''}`);
