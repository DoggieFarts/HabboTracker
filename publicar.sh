#!/usr/bin/env bash
# Publica la carpeta sitio en la rama datos, que es la que muestra GitHub Pages.
# Si el recolector ya preparó varios hoteles, respeta ese árbol; si falta la portada,
# usa el index.html del repo como respaldo.
set -euo pipefail
mensaje="${1:-Datos del $(date -u +%F)}"
if [ ! -f sitio/index.html ]; then
  cp index.html sitio/index.html
fi
cd sitio
rm -rf .git
git init --quiet -b datos
git config user.name "mercadillo-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
git commit --quiet -m "$mensaje"
git push --quiet --force "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" datos
rm -rf .git
echo "Página publicada: $mensaje"
