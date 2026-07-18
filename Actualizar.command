#!/bin/bash
# Doble clic en Finder para actualizar el juego con tu vault + universidad.
# (Si macOS no lo deja abrir: clic derecho → Abrir, una sola vez.)
cd "$(dirname "$0")" || exit 1

# Carga tu clave de Gemini desde .env si existe (soporta valores con espacios)
if [ -f .env ]; then set -a; . ./.env; set +a; fi

echo "======================================"
echo "  🐉 La Torre del Erudito — Actualizar"
echo "======================================"

if ! command -v node >/dev/null 2>&1; then
  echo "❌ Necesitas Node.js. Instálalo desde https://nodejs.org (versión LTS)."
  read -n 1 -s -r -p "Pulsa una tecla para cerrar…"
  exit 1
fi

node scripts/actualizar.mjs
CODE=$?

echo ""
if [ $CODE -eq 0 ]; then echo "✔ Terminado."; else echo "✖ Hubo un problema (ver arriba)."; fi
read -n 1 -s -r -p "Pulsa una tecla para cerrar…"
