#!/usr/bin/env bash
# Publica la carpeta sitio en la rama datos, que es la que muestra GitHub Pages.
# Si el recolector ya preparó varios hoteles, respeta ese árbol; si falta la portada,
# usa el index.html del repo como respaldo.
set -euo pipefail
mensaje="${1:-Datos del $(date -u +%F)}"
# 1. Asegurar que la UI más reciente del repositorio se use en la publicación.
# Sobreescribimos siempre para que los cambios en index.html se reflejen.
cp index.html sitio/index.html

# 2. Si hay subdirectorios para otros hoteles, también les ponemos el index.html
# Esto permite que habbomarket.live/habbo.com/ funcione con la misma interfaz.
for d in sitio/*/ ; do
    if [ -d "$d" ]; then
        cp index.html "$d/index.html"
    fi
done

printf "habbomarket.live" > sitio/CNAME
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
