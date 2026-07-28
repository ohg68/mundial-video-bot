#!/bin/bash
# Arranca el servidor local del LayerCut Downloader (puerto 5757).
# Se abre con "open" desde el launcher del ícono del Desktop — Terminal
# lo corre como apertura normal de archivo, sin pedir permisos de
# Automatización.
cd "$(dirname "$0")"
echo "Iniciando LayerCut Downloader..."
python3 server.py
