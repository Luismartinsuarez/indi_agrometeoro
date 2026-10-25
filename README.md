# Índices agrometeorológicos

Cálculo y visualización de índices agrometeorológicos (temperaturas, heladas, precipitación,
grados-día, horas frío, ETo FAO-56, índice de humedad) para 28 estaciones automáticas de Perú.

## Dashboard

```
pip install -r requirements.txt
streamlit run scripts/dashboard.py
```

Lee `data/indices/` y `data/processed/estaciones.csv`, que van incluidos en el repositorio.

## Recalcular los índices

Los datos crudos (`data/raw/`) no se versionan. Con ellos en su sitio:

```
python3 scripts/procesar_raw.py      # data/raw/estaciones/*.csv -> data/processed/
python3 scripts/calcular_indices.py  # data/processed/ -> data/indices/
```

Ambos scripts solo usan la biblioteca estándar de Python.
