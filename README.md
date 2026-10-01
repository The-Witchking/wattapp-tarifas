# wattapp-tarifas

Cuadro tarifario residencial de EPEC (Córdoba) en JSON, para la app Wattsapp.

Una GitHub Action corre los días 3 y 18 de cada mes (o a mano) y hace esto:

1. Lee la [carpeta pública de Drive](https://drive.google.com/drive/folders/1pkF_dODi3sjEsf5Kvly_Q8kNamfIaH97)
   donde EPEC publica los cuadros.
2. Toma el cuadro **PARCIAL** más reciente. Es el que se aplica en las facturas
   residenciales; el PLENO no.
3. Interpreta la Tarifa Nº 1 Residencial, acápites a, b, c, e, f y h, con y sin
   subsidio energético focalizado (SEF).
4. Valida el resultado. Si algo no cierra, la Action falla y no publica nada.
5. Publica `tariffs.json` (el último) y `history/epec/AAAA-MM-DD.json`.

Los precios están sin impuestos. `schedule` usa los mismos campos que `TariffSchedule`
en la app.

## Cooperativas (ERSEP)
En la misma corrida se actualizan los cuadros de las cooperativas eléctricas de Córdoba:

1. `cooperativas/catalog.json`: las cooperativas de [ERSEP](https://ersep.cba.gov.ar/prestadores-por-gerencia/)
   con su área de concesión y el link fijo de Drive a su cuadro. Se relee el día 3 de cada mes.
2. Cada PDF usa la plantilla de ERSEP. Se interpreta la tarifa residencial, que viene **sin
   subsidio** (nivel NOSEF). El nivel con subsidio (SEF) se calcula restando el diferencial
   del apartado B.1 a los primeros 300, 150 o 200 kWh según el mes (Decreto 943/2025).
3. Si el PDF no cambió pero empezó un mes con otro volumen subsidiado, el cuadro se regenera
   con vigencia desde el 1 del mes.
4. Se publican `cooperativas/<id>.json` (mismo formato que `tariffs.json`) y
   `cooperativas/index.json`. Las que no se pudieron interpretar figuran en
   `cooperativas/report.md` con el motivo. La Action falla solo si no se puede interpretar
   más del 30 %.

## Uso local
```
pip install -r requirements.txt
python -m unittest discover -s tests -t .
python -m scraper.main          # EPEC; --force para reprocesar el último
python -m scraper.coops_main    # cooperativas; --catalog para releer ERSEP
```
