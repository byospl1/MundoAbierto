#!/usr/bin/env node
/**
 * actualizar.mjs — Pipeline automático semanal.
 * 1. Copia tus archivos de la universidad al vault (vault/Uni/)
 * 2. Regenera world.json con Gemini (build-world.mjs)
 * 3. Hace copia de seguridad del mundo anterior (backups/)
 * 4. Commit + push a GitHub → GitHub Pages republica solo
 *
 * Config por variables de entorno (o edita las constantes de abajo):
 *   UNI_SOURCE   carpeta de tu Mac con los trabajos de la uni (.md). Ej: ~/Documents/Universidad
 *   GEMINI_API_KEY   tu clave de Gemini
 *
 * Uso: node scripts/actualizar.mjs   (o doble clic en Actualizar.command)
 */
import { existsSync, mkdirSync, readdirSync, copyFileSync, readFileSync, writeFileSync, statSync } from 'node:fs';
import { execSync } from 'node:child_process';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
process.chdir(ROOT);

// Carpeta de la uni: por defecto ~/Documents/Universidad (cámbiala si quieres)
const UNI_SOURCE = (process.env.UNI_SOURCE || path.join(os.homedir(), 'Documents', 'Universidad')).replace(/^~/, os.homedir());
const UNI_DEST = path.join(ROOT, 'vault', 'Uni');

const log = (...a) => console.log('•', ...a);
function sh(cmd) { return execSync(cmd, { stdio: ['ignore', 'pipe', 'pipe'] }).toString().trim(); }

console.log('\n🐉 Actualizando La Torre del Erudito…\n');

// 1) Copiar trabajos de la universidad al vault
try {
  if (existsSync(UNI_SOURCE)) {
    mkdirSync(UNI_DEST, { recursive: true });
    let n = 0;
    const walk = dir => {
      for (const f of readdirSync(dir)) {
        const p = path.join(dir, f);
        if (statSync(p).isDirectory()) { walk(p); continue; }
        if (f.endsWith('.md')) { copyFileSync(p, path.join(UNI_DEST, f)); n++; }
      }
    };
    walk(UNI_SOURCE);
    log(`Universidad: ${n} nota(s) copiadas desde ${UNI_SOURCE} → vault/Uni/`);
  } else {
    log(`(Sin carpeta de universidad en ${UNI_SOURCE} — se omite. Crea la carpeta o define UNI_SOURCE.)`);
  }
} catch (e) { log('Aviso al copiar universidad:', e.message); }

// 2) Copia de seguridad del mundo anterior (local + rama en GitHub)
try {
  if (existsSync('world.json')) {
    mkdirSync('backups', { recursive: true });
    const stamp = new Date().toISOString().slice(0, 10);
    copyFileSync('world.json', path.join('backups', `world-${stamp}.json`));
    log(`Copia de seguridad local: backups/world-${stamp}.json`);
  }
  // Duplicado en GitHub: sube el estado actual a una rama de respaldo antes de regenerar
  try {
    const stamp = new Date().toISOString().slice(0, 10);
    const branch = `backup/${stamp}`;
    sh('git fetch origin --quiet || true');
    sh(`git push origin HEAD:refs/heads/${branch} --force`);
    log(`Duplicado en GitHub: rama ${branch}`);
  } catch (e) { log('Aviso: no se pudo crear la rama de respaldo en GitHub (', e.message.split('\n')[0], ')'); }
} catch (e) { log('Aviso al respaldar:', e.message); }

// 3) Regenerar el mundo
log('Generando preguntas con IA (Gemini)…');
try {
  execSync('node scripts/build-world.mjs', { stdio: 'inherit', env: process.env });
} catch (e) {
  console.error('\n❌ Error al generar el mundo. Revisa tu GEMINI_API_KEY.\n');
  process.exit(1);
}

// 4) Commit + push
try {
  const changed = sh('git status --porcelain');
  if (!changed) { log('No hubo cambios: el vault no cambió esta semana.'); }
  else {
    sh('git add -A');
    sh(`git commit -m "🐉 Actualización automática ${new Date().toISOString().slice(0, 10)}"`);
    sh('git push');
    log('¡Subido a GitHub! GitHub Pages republicará en 1-2 min.');
  }
} catch (e) {
  console.error('\n⚠️  No se pudo subir a GitHub:', e.message);
  console.error('   Verifica que el repo esté clonado con acceso de escritura (git remote + credenciales).\n');
  process.exit(1);
}

console.log('\n✅ Listo. Abre el juego y verás el mundo actualizado.\n');
