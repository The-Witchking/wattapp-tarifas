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

## Uso local
```
pip install -r requirements.txt
python -m unittest discover -s tests -t .
python -m scraper.main          # --force para reprocesar el último
```
