# Mercadillo

Rastreador público de precios del Mercadillo de Habbo (habbo.es). Todo corre gratis en GitHub: Actions consulta los precios y GitHub Pages muestra la página.

## Qué tiene

- **Mercado:** todos los furnis con ventas, con su precio justo (mediana de 30 días), rango normal y días para revender. Buscador de los ~18,000 furnis del hotel.
- **Lanzamientos:** furnis nuevos de los últimos 30 días y cuánto han bajado desde su máximo.
- **Presupuesto:** arma una lista de compras según tus créditos y en cuánto quieres revender.
- **Radar:** furnis cuyas señales (menos ofertas, más ventas, tendencia, caídas que se corrigen) solían venir antes de una subida. Se prueba en cada actualización contra el último año, sin mirar el futuro, y solo recomienda si le gana al mercado con claridad.
- **Historial de 2 años** por furni, traído de habboapi.site.

## Archivos

| Archivo | Para qué sirve |
|---|---|
| `index.html` | La página. |
| `recolector.py` | Consulta precios en Habbo. |
| `historial.py` | Rellena el historial de 2 años desde habboapi.site. |
| `prediccion.py` | El radar: calcula señales, las prueba contra el último año y publica recomendaciones solo si le ganan al mercado. |
| `publicar.sh` | Publica los datos en la rama `datos`, que es la que muestra Pages. |
| `avisos.json` | Configuración: hotel, comisión del Mercadillo y dirección de los precios en vivo. |
| `en_vivo.py` | Corre sin parar en una máquina virtual de Azure y mantiene los precios en vivo. |
| `azure-cloud-init.yml` | Configuración que se pega al crear la máquina de Azure; la deja instalada sola. |
| `.github/workflows/recolectar.yml` | Corre el recolector a las 7 a. m. y 7 p. m. (hora del centro de México). |
| `.github/workflows/historial-completo.yml` | Carga inicial del historial, a mano y una sola vez. |

La rama `datos` la crea el recolector sola y siempre tiene un solo commit. No la edites.

## Cómo funciona cada corrida

1. Trae los datos guardados de la rama `datos`.
2. Actualiza primero los furnis que llevan más tiempo sin actualizarse (y entre ellos, los que más se venden), con un máximo de 45 minutos.
3. Rellena hasta 30 minutos de historial largo.
4. Publica la página.

La página siempre sigue visible con la última versión publicada mientras una corrida trabaja.

## Precios en vivo

Una máquina virtual B1s de Azure (incluida en Azure for Students) corre `en_vivo.py` todo el tiempo:

- Toma la lista de furnis de `indice.json` en la página publicada.
- Refresca los 500 más vendidos cada par de minutos y rota el resto del mercado.
- Escribe `vivo.json`, que Caddy entrega por HTTPS con certificado automático.

La página lo consulta cada 2 minutos (solo con la pestaña visible) cuando `en_vivo_url` está en `avisos.json`. Si la máquina no responde, la página sigue con los datos publicados por GitHub.

La máquina baja la versión más reciente de `en_vivo.py` desde el repo cada día a las 10:00 UTC, así que para actualizarla basta con cambiar el archivo en GitHub.

## Alertas

Las alertas por Telegram están desactivadas por ahora. Mientras `telegram_bot` esté vacío en `avisos.json`, la página no muestra nada relacionado con alertas.
