#!/usr/bin/env bash
# ============================================================
# setup_voces_es.sh — Voces españolas para Piper TTS en OpenMontage
# Ejecutar desde la raíz del repo OpenMontage, en tu Mac.
# 100% gratis, offline, licencia permisiva (MIT / dominio público).
# ============================================================
set -euo pipefail

VOICE_DIR="$HOME/.piper/models"
mkdir -p "$VOICE_DIR"
cd "$VOICE_DIR"

BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/es"

echo "==> Descargando voces españolas Piper a $VOICE_DIR"

# --- Voz España, masculina, registro neutro (recomendada para narración doc) ---
dl () {
  local name="$1" path="$2"
  if [ -f "$name.onnx" ]; then
    echo "  [skip] $name ya existe"
  else
    echo "  [get ] $name"
    curl -sL "$BASE/$path/$name.onnx"      -o "$name.onnx"
    curl -sL "$BASE/$path/$name.onnx.json" -o "$name.onnx.json"
  fi
}

# España — David FX, medium. La opción por defecto: clara, sobria, narrador.
dl "es_ES-davefx-medium"   "es_ES/davefx/medium"

# España — Sharvard, medium. Alternativa, timbre algo más cálido.
dl "es_ES-sharvard-medium" "es_ES/sharvard/medium"

# Argentina — Daniela, high. Acento rioplatense (femenina). Por si encaja con tu voz/tono.
dl "es_AR-daniela-high"    "es_AR/daniela/high"

echo ""
echo "==> Verificando que Piper genera audio en español..."
TEST_OUT="/tmp/piper_test_es.wav"
echo "Esto es una prueba de narración en español para OpenMontage." \
  | piper --model "es_ES-davefx-medium" --output_file "$TEST_OUT" 2>/dev/null || {
    echo "  [aviso] El binario 'piper' no encontró el modelo por nombre."
    echo "          Usa la ruta absoluta en su lugar (ver NOTA abajo)."
  }

if [ -f "$TEST_OUT" ]; then
  echo "  OK — audio de prueba en $TEST_OUT (ábrelo para escuchar)"
fi

echo ""
echo "============================================================"
echo "LISTO. Voces instaladas en $VOICE_DIR:"
ls -1 "$VOICE_DIR"/es_*.onnx 2>/dev/null | sed 's/^/  /'
echo "============================================================"
