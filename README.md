# Mercadillo

Rastreador público de precios del Mercadillo de Habbo (por ejemplo `habbo.es` y `habbo.com`). Todo corre gratis en GitHub: Actions consulta los precios y GitHub Pages muestra la página.

## Qué tiene

- **Mercado:** todos los furnis con ventas, con su precio justo (mediana de 30 días), rango normal, minigráfica de 30 días y días para revender. Buscador de los ~18,000 furnis de cada hotel.
- **Lanzamientos:** furnis nuevos de los últimos 30 días y cuánto han bajado desde su máximo.
- **Presupuesto:** arma una lista de compras según tus créditos y en cuánto quieres revender.
- **Radar:** furnis cuyas señales (menos ofertas, más ventas, tendencia, caídas que se corrigen) solían venir antes de una subida. Compiten tres modelos: señales lineales, XGBoost (~20 señales, incluido el precio en el otro hotel) y la mezcla de ambos. Se prueban en cada actualización contra el último año sin mirar el futuro, comprando en los 3 días *siguientes* a la señal, y solo se recomienda si el mejor le gana al mercado con claridad y además deja ganancia.
- **Índice del mercado:** cómo se mueve el precio del furni típico contra su propio nivel de los últimos dos años, con su cambio a 7, 30 y 90 días.
- **Pronóstico por furni:** rango probable del precio dentro de 14 días (XGBoost por cuantiles) y la probabilidad de ganar si compras al precio actual, ya con comisión. Se dibuja en la gráfica del detalle.
- **Historial de 2 años** por furni, traído de habboapi.site. La gráfica muestra los datos de cada día al pasar el mouse y tiene rangos de 7 días a todo el historial.

## Archivos

| Archivo | Para qué sirve |
|---|---|
| `index.html` | La página. |
| `recolector.py` | Consulta precios en Habbo. |
| `historial.py` | Rellena el historial de 2 años desde habboapi.site. |
| `prediccion.py` | El radar: calcula señales, prueba los modelos contra el último año y publica recomendaciones solo si le ganan al mercado. Escribe `prediccion.json` y `pronostico.json`. |
| `modelo.py` | Índice del mercado, retador XGBoost y pronóstico por cuantiles. Si `xgboost` no está instalado, el radar sigue solo con las señales lineales. |
| `publicar.sh` | Publica los datos en la rama `datos`, que es la que muestra Pages. |
| `avisos.json` | Configuración: hoteles, comisión del Mercadillo y dirección de los precios en vivo. |
| `en_vivo.py` | Corre sin parar en una máquina virtual de Azure y mantiene los precios en vivo. |
| `.github/workflows/recolectar.yml` | Corre el recolector a las 7 a. m. y 7 p. m. (hora del centro de México). |
| `.github/workflows/historial-completo.yml` | Carga inicial del historial, a mano y una sola vez. |

La rama `datos` la crea el recolector sola y siempre tiene un solo commit. No la edites. Si hay varios hoteles, cada uno vive en su propia carpeta dentro de esa rama.

## Cómo funciona cada corrida

1. Trae los datos guardados de la rama `datos`.
2. Actualiza primero los furnis que llevan más tiempo sin actualizarse (y entre ellos, los que más se venden), con un máximo de 45 minutos.
3. Rellena hasta 30 minutos de historial largo.
4. Publica la página.

La página siempre sigue visible con la última versión publicada mientras una corrida trabaja.

## Precios en vivo

Una máquina virtual B1s de Azure (incluida en Azure for Students) puede correr una instancia de `en_vivo.py` por hotel todo el tiempo:

- Toma la lista de furnis de `indice.json` en la página publicada del hotel correspondiente.
- Refresca los 500 más vendidos lo más rápido que Habbo permite y rota el resto del mercado. Empieza con una consulta cada 3 s y acelera sola mientras Habbo no se queje (hasta `PAUSA_MIN`, 1 s por defecto); si Habbo responde 429, frena y no vuelve a ese ritmo durante una hora.
- Escribe `vivo-<hotel>.json` cada 10 segundos, que Caddy entrega por HTTPS con certificado automático.

La página lo consulta cada 15 segundos (solo con la pestaña visible; si el archivo no cambió, el navegador no lo vuelve a descargar) cuando `en_vivo_url` está en `avisos.json`. Si la máquina no responde, la página sigue con los datos publicados por GitHub.

La máquina baja la versión más reciente de `en_vivo.py` desde el repo cada día a las 10:00 UTC, así que para actualizarla basta con cambiar el archivo en GitHub.

## Alertas

Las alertas por Telegram están desactivadas por ahora. Mientras `telegram_bot` esté vacío en `avisos.json`, la página no muestra nada relacionado con alertas.

La tarjeta **Precios en Vivo** del panel muestra cuántos precios hay, qué tan recientes son y cada cuántos segundos se refrescan los más vendidos.
