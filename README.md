# Mercadillo (versión web, todos los furnis)

Rastreador de precios del Mercadillo de Habbo que corre solo en GitHub y se ve en el celular.

No tienes que elegir furnis. Cada día el recolector:

1. Descarga el catálogo completo del hotel (una vez por semana).
2. Revisa una séptima parte del catálogo, así en una semana pasa por todos los furnis.
3. Sigue a diario todos los que tuvieron ventas en los últimos 30 días.

La primera corrida revisa el catálogo entero de una vez, así que tarda más (unos 30 a 40 minutos).

## Qué tiene la página

- **Mercado:** todos los furnis con ventas. Cada uno muestra su **precio justo** (la mediana de 30 días, pesada por ventas), su rango normal y los **días para revender** (ofertas abiertas entre ventas por día).
- **Lanzamientos:** furnis que aparecieron en el catálogo en los últimos 30 días y cuánto han bajado desde su precio más alto.
- **Presupuesto:** pones cuántos créditos quieres invertir y en cuánto tiempo quieres revender, y arma una lista de compras con la ganancia estimada.

Las imágenes de los furnis vienen de habboapi.site, un servicio externo. Si no carga alguna, la página sigue funcionando.

## Archivos

- `avisos.json`: el único archivo que editas. Solo sirve para recibir avisos de precio.
- `recolector.py`: consulta los precios. Lo ejecuta GitHub.
- `.github/workflows/recolectar.yml`: le dice a GitHub que corra el recolector diario y publique la página.
- `index.html`: el tablero.

Los datos se guardan en la rama `datos` del repo, que siempre tiene un solo commit para que el repo no crezca con los días. No la edites.

## Avisos

En la página, abre un furni, escribe el precio y toca **Copiar aviso**. Luego abre `avisos.json`, pega la línea dentro de la lista y guarda:

```json
{
  "hotel": "habbo.es",
  "comision_pct": 1,
  "avisos": [
    { "classname": "throne", "tipo": "piso", "precio": 950 },
    { "classname": "rare_fan*4", "tipo": "piso", "precio": 80 }
  ]
}
```

Cada aviso lleva coma al final menos el último. Cuando un furni llega a tu precio se abre un issue en el repo y la app de GitHub te notifica.

## Si algo falla

En **Actions**, abre la última corrida y revisa el paso "Consultar el Mercadillo". Si dice 403, la protección contra bots de Habbo está bloqueando a GitHub.
