#!/usr/bin/env bash
# Publica la carpeta sitio en la rama datos, que es la que muestra GitHub Pages.
set -euo pipefail

mensaje="${1:-Datos del $(date -u +'%F %H:%M') UTC}"

echo "--- Iniciando proceso de publicación ---"

# 1. Asegurar que la UI más reciente se use en la publicación.
if [ -f index.html ]; then
    cp index.html sitio/index.html
    echo "index.html copiado a sitio/"
else
    echo "ERROR: index.html no encontrado en el root."
    exit 1
fi

# 2. UI para hoteles secundarios
for d in sitio/*/ ; do
    if [ -d "$d" ] && [ -f "$d/indice.json" ]; then
        cp index.html "$d/index.html"
        echo "UI copiada a subdirectorio: $d"
    fi
done

# CNAME para el dominio personalizado
printf "habbomarket.live" > sitio/CNAME

# 3. Preparar el repositorio temporal para la rama 'datos'
cd sitio
rm -rf .git
git init --quiet
git checkout -b datos
git config user.name "mercadillo-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# Verificación de frescura de los datos
if [ -f indice.json ]; then
    echo "Timestamp del índice a publicar:"
    grep -o '"updated":"[^"]*"' indice.json | head -n 1 || echo "No se encontró el campo updated"
else
    echo "ERROR: indice.json no encontrado en sitio/"
    exit 1
fi

git add -A

# Solo hacer commit y push si hay cambios reales
if git diff --staged --quiet; then
    echo "No hay cambios nuevos en los datos. Saltando publicación."
else
    git commit -m "$mensaje"
    echo "Publicando en la rama 'datos' de GitHub..."
    # Eliminamos --quiet para ver errores si el push falla
    git push --force "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" datos
    echo "Despliegue completado: $mensaje"
fi

cd ..
rm -rf sitio/.git
echo "--- Proceso finalizado ---"
