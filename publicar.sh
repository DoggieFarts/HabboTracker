#!/usr/bin/env bash
# Publica la carpeta sitio en la rama datos, que es la que muestra GitHub Pages.
# La rama siempre queda con un solo commit, así el repo no crece cada día.
set -euo pipefail
mensaje="${1:-Datos del $(date -u +%F)}"
# cada hotel recibe el mismo index.html con su nombre fijo; así no hay que deducirlo en el navegador
for d in sitio/habbo.*/; do
  [ -f "$d/indice.json" ] || continue
  hotel=$(basename "$d")
  sed "s|__HOTEL__|$hotel|g" index.html > "$d/index.html"
done
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
