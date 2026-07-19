#!/bin/bash
# Captura My Clippings.txt del Kindle y lanza el pipeline.
# Soporta dos vias:
#   A) Kindles antiguos que montan como disco USB (/Volumes/...)
#   B) Kindles 2024+ (p. ej. Scribe) via MTP con libmtp
# Se ejecuta al montar un volumen (StartOnMount) y cada 2 min (StartInterval).
# El marcador .kindle_conectado evita recapturar en cada ciclo mientras el
# Kindle siga enchufado: se captura UNA vez por conexion.

BASE="$HOME/Documents/kindle-cerebro"
ARCHIVO="$BASE/archivo"
LOG="$BASE/logs/captura.log"
MARCA="$BASE/.kindle_conectado"
PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
mkdir -p "$ARCHIVO" "$BASE/logs"

registrar() { echo "$(date '+%F %T') $1" >> "$LOG"; }

procesar_si_nuevo() {   # $1 = fichero recien capturado (temporal)
    local tmp="$1"
    [ -s "$tmp" ] || { registrar "Captura vacía, la descarto"; rm -f "$tmp"; return 1; }
    local ultimo
    ultimo=$(ls -t "$ARCHIVO"/*.txt 2>/dev/null | head -1)
    if [ -n "$ultimo" ] && cmp -s "$tmp" "$ultimo"; then
        registrar "Sin cambios respecto a la última copia"
        rm -f "$tmp"
        return 0
    fi
    local destino="$ARCHIVO/clippings-$(date '+%Y%m%d-%H%M%S').txt"
    mv "$tmp" "$destino"
    registrar "Copiado a $destino — lanzando pipeline"
    /usr/bin/osascript -e 'display notification "Highlights copiados del Kindle. Procesando…" with title "Cerebro de Lecturas"' 2>/dev/null
    /usr/bin/python3 "$BASE/procesar.py" >> "$LOG" 2>&1
}

# ---------- via A: volumen montado ----------
for clippings in /Volumes/*/documents/"My Clippings.txt" \
                 /Volumes/*/"My Clippings.txt"; do
    [ -f "$clippings" ] || continue
    registrar "Kindle (disco USB) detectado: $clippings"
    tmp=$(mktemp)
    cp "$clippings" "$tmp"
    procesar_si_nuevo "$tmp"
    touch "$MARCA"
    exit 0
done

# ---------- via B: MTP (Kindle Scribe / 2024+) ----------
if ! ioreg -p IOUSB 2>/dev/null | grep -q Kindle; then
    rm -f "$MARCA"          # kindle ausente: rearmar para la proxima conexion
    exit 0
fi

# ya capturado en esta conexion
[ -f "$MARCA" ] && exit 0

[ -x "$BASE/mtp_clippings" ] || { registrar "falta mtp_clippings compilado"; exit 0; }

registrar "Kindle (MTP) detectado"
# el USB File Manager de Amazon (Send to Kindle) monopoliza la interfaz MTP
pkill -f "USB File Manager" 2>/dev/null && sleep 3

# una UNICA sesion MTP: el Scribe abandona el modo USB si se abren varias
tmp=$(mktemp -d)/clippings.txt
if "$BASE/mtp_clippings" "$tmp" >> "$LOG" 2>&1; then
    registrar "MTP: descargado My Clippings.txt"
    procesar_si_nuevo "$tmp"
    touch "$MARCA"
else
    registrar "MTP: fallo la descarga; reintento en el próximo ciclo"
fi

exit 0
