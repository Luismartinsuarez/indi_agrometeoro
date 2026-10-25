#!/usr/bin/env python3
"""Limpia los CSV horarios de data/raw y genera data/processed/.

Salidas:
  horario/<estacion>.csv   serie horaria completa (horas faltantes vacías), columnas normalizadas y QC aplicado
  diario/<estacion>.csv    agregados diarios (solo días con >= --min-horas valores válidos por variable)
  diario_todas.csv         todos los diarios juntos, con columna `estacion`
  estaciones.csv           metadatos, coordenadas, altitud y cobertura por estación
  variables.csv            columna normalizada -> nombre original y unidad
  qc_resumen.csv           cuántos valores eliminó o ajustó cada regla, por estación y columna

Solo usa la biblioteca estándar. Las fechas se asumen en hora local (UTC-5).
"""
import argparse
import csv
import re
import statistics
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FMT_FECHA = "%Y-%m-%d %H:%M"

# Nombres del esquema reducido de San Jose que no siguen el formato del resto
RENOMBRAR = {
    "Temperatura": "temperatura_instantanea",
    "Humedad": "humedad_instantanea",
    "Velocidad": "velocidad_viento_medio_1h",
    "Direccion": "direccion_viento_medio_1h",
    "Presion": "presion",
    "Lluvia": "precipitacion_acc_1h",
    "Presencia PS": "presencia_panel_solar",
}
# Duplicado exacto de "Velocidad de Viento Medio (1h)" en USI y Rincon de la Cruz
DESCARTAR = {"Vel. Viento med (1h)"}
SUELO = re.compile(r"^(Temperatura|Humedad|Salinidad) -(\d+)$")
SUELO_NOMBRE = {"Temperatura": "temperatura", "Humedad": "vwc", "Salinidad": "salinidad"}

# (patrón de columna, mínimo, máximo); gana el primer patrón que coincide
RANGOS = [
    (r"^suelo_temperatura", -10, 60),
    (r"^suelo_vwc", 0, 100),
    (r"^suelo_salinidad", 0, None),
    (r"^punto_rocio", -40, 35),
    (r"^temperatura", -30, 45),
    (r"^humedad", 1.01, 100),
    (r"^rh_hoja", 0, 110),  # el sensor satura en 109-110
    (r"^radiacion", 0, 1400),
    (r"^energia_solar_1h", 0, 5040),
    (r"^energia_solar_10_min", 0, None),  # ~10x lo esperado para kJ/m2: unidad dudosa, solo se exige >= 0
    (r"^precipitacion_acc_1h", 0, 100),
    (r"^precipitacion_acc_10_min", 0, 50),
    (r"^intensidad_precipitacion", 0, 300),
    (r"^velocidad_viento", 0, 60),
    (r"^direccion_viento", 0, 360),
    (r"^presion", 500, 1100),
    (r"^bateria", 9, 16),
    (r"^presencia_panel_solar", 0, 100),
]
CENTINELA = -999  # valores <= a esto son códigos de error (p. ej. -9299, -35996)
# Negativos pequeños por offset del sensor (sobre todo de noche): se llevan a 0 en vez de borrarse
AJUSTE_A_CERO = [(r"^radiacion", -5), (r"^energia_solar", -10), (r"^rh_hoja", -2)]
SALTO_PICO = 10   # °C de diferencia con ambas horas vecinas para considerar un pico aislado
HORAS_NOCHE = {20, 21, 22, 23, 0, 1, 2, 3, 4}
RAD_NOCHE_MAX = 20  # W/m2; por encima, de noche, se invalidan radiación y energía de esa hora
T_AIRE = ["temperatura_instantanea", "temperatura_media", "temperatura_minima", "temperatura_maxima"]

# Periodos revisados a mano: (estación, patrón de columna, desde, hasta exclusivo o None, motivo)
EXCLUSIONES = [
    ("j4_acolla", r"precipitacion", "2025-04-10", None,
     "pluviómetro en 0 mm desde abr-2025, incluida la época de lluvias 2025-26"),
    ("payhua", r"precipitacion", "2025-06-01", "2026-09-01",
     "pluviómetro en 0 mm durante la época de lluvias 2025-26"),
    ("c2_usi", r"^(radiacion|energia_solar)", "2022-05-01", "2024-08-12",
     "piranómetro con valores de 1500-2000 W/m2 y radiación nocturna"),
]

# (salida, columnas candidatas: se usa la primera que exista en la estación, agregación)
DIARIO = [
    ("t_med", ["temperatura_media", "temperatura_instantanea"], statistics.fmean),
    ("t_min", ["temperatura_minima", "temperatura_instantanea"], min),
    ("t_max", ["temperatura_maxima", "temperatura_instantanea"], max),
    ("hr_med", ["humedad_media", "humedad_instantanea"], statistics.fmean),
    ("hr_min", ["humedad_minima", "humedad_instantanea"], min),
    ("hr_max", ["humedad_maxima", "humedad_instantanea"], max),
    ("td_med", ["punto_rocio_medio"], statistics.fmean),
    ("pp_mm", ["precipitacion_acc_1h"], sum),
    ("rad_wm2", ["radiacion_solar_media_1h"], statistics.fmean),
    ("viento_ms", ["velocidad_viento_medio_1h"], statistics.fmean),
    ("viento_max_ms", ["velocidad_viento_maximo_1h"], max),
    ("presion_hpa", ["presion"], statistics.fmean),
    ("hf_horas", ["temperatura_media", "temperatura_instantanea"], lambda v: sum(x < 7 for x in v)),
]


def slugify(texto):
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_")


def parsear_encabezado(h):
    """'J4 - Acolla - Temperatura Media - Val (ºC)' -> ('J4 - Acolla', 'Temperatura Media', '°C')"""
    izq, _, unidad = h.partition(" - Val (")
    prefijo, _, var = izq.rpartition(" - ")
    return prefijo, var, unidad[:-1].replace("º", "°")


def nombre_columna(var):
    if var in RENOMBRAR:
        return RENOMBRAR[var]
    m = SUELO.match(var)
    if m:
        return f"suelo_{SUELO_NOMBRE[m[1]]}_{m[2]}cm"
    return slugify(var.removeprefix("copia de ")).replace("_de_", "_")


def rango(col):
    for patron, lo, hi in RANGOS:
        if re.match(patron, col):
            return lo, hi
    return None, None


def umbral_a_cero(col):
    return next((u for patron, u in AJUSTE_A_CERO if re.match(patron, col)), None)


def a_float(s):
    try:
        return float(s)
    except ValueError:
        return None


def leer_estacion(path, variables):
    with open(path, encoding="utf-8", newline="") as fh:
        lector = csv.reader(fh)
        encabezado = next(lector)
        filas = list(lector)

    prefijos, columnas = set(), []  # columnas: (índice en el CSV, nombre normalizado)
    for i, h in enumerate(encabezado[1:], start=1):
        prefijo, var, unidad = parsear_encabezado(h)
        prefijos.add(prefijo)
        if var in DESCARTAR:
            continue
        col = nombre_columna(var)
        assert col not in [c for _, c in columnas], f"{path.name}: columna repetida {col}"
        columnas.append((i, col))
        if var not in RENOMBRAR or col not in variables:  # el nombre del esquema estándar tiene prioridad
            variables[col] = (var, unidad)
    assert len(prefijos) == 1, f"{path.name}: varios prefijos {prefijos}"
    prefijo = prefijos.pop()

    datos = {}
    for fila in filas:
        fecha = datetime.strptime(fila[0], FMT_FECHA)
        datos[fecha] = {col: fila[i].strip() if i < len(fila) else "" for i, col in columnas}
    return prefijo, [c for _, c in columnas], datos, len(filas)


def limpiar(estacion, columnas, datos, qc):
    fechas = sorted(datos)
    grilla = [fechas[0] + timedelta(hours=h)
              for h in range(int((fechas[-1] - fechas[0]).total_seconds() // 3600) + 1)]
    serie = {col: [None] * len(grilla) for col in columnas}

    # 1) conversión, centinelas, ajustes menores y rangos físicos
    rangos = {col: rango(col) for col in columnas}
    a_cero = {col: umbral_a_cero(col) for col in columnas}
    for k, fecha in enumerate(grilla):
        fila = datos.get(fecha)
        if fila is None:
            continue
        for col in columnas:
            s = fila[col]
            if s == "":
                continue
            v = a_float(s)
            if v is None:
                qc[(estacion, col, "no_numerico")] += 1
                continue
            if v <= CENTINELA:
                qc[(estacion, col, "centinela")] += 1
                continue
            if a_cero[col] is not None and a_cero[col] <= v < 0:
                qc[(estacion, col, "ajuste_negativo_a_0")] += 1
                v = 0.0
            if col.startswith("humedad") and 100 < v <= 103:
                qc[(estacion, col, "ajuste_a_100")] += 1
                v = 100.0
            lo, hi = rangos[col]
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                qc[(estacion, col, "fuera_de_rango")] += 1
                continue
            serie[col][k] = v

    def borrar(col, k, regla):
        if serie[col][k] is not None:
            serie[col][k] = None
            qc[(estacion, col, regla)] += 1

    # 2) consistencia dentro de cada hora
    tmed, tmin, tmax = (serie.get(c) for c in ("temperatura_media", "temperatura_minima", "temperatura_maxima"))
    hmed = serie.get("humedad_media")
    t_ref = tmax or serie.get("temperatura_instantanea")
    rocio = [c for c in columnas if c.startswith("punto_rocio")]
    solares = [c for c in columnas if re.match(r"^(radiacion|energia_solar)", c)]
    rad = serie.get("radiacion_solar_media_1h")
    for k in range(len(grilla)):
        if rad and rad[k] is not None and rad[k] > RAD_NOCHE_MAX and grilla[k].hour in HORAS_NOCHE:
            for col in solares:
                borrar(col, k, "radiacion_nocturna")
        if tmed and tmin and tmax and hmed and tmed[k] == tmin[k] == tmax[k] == hmed[k] == 0:
            for col in T_AIRE + ["humedad_instantanea", "humedad_media", "humedad_minima", "humedad_maxima"]:
                if col in serie:
                    borrar(col, k, "ceros_simultaneos")
            continue
        if tmed and tmed[k] is not None:
            if tmin and tmin[k] is not None and (tmin[k] == 0 and tmed[k] > 4 or tmed[k] - tmin[k] > 10):
                borrar("temperatura_minima", k, "inconsistencia_t")
            if tmax and tmax[k] is not None and tmax[k] - tmed[k] > 10:
                borrar("temperatura_maxima", k, "inconsistencia_t")
        if t_ref and t_ref[k] is not None:
            for col in rocio:
                if serie[col][k] is not None and serie[col][k] > t_ref[k] + 1:
                    borrar(col, k, "rocio_mayor_que_t")

    # 3) picos aislados de temperatura del aire
    for col in T_AIRE:
        s = serie.get(col)
        if not s:
            continue
        picos = [k for k in range(1, len(s) - 1)
                 if None not in (s[k - 1], s[k], s[k + 1])
                 and min(abs(s[k] - s[k - 1]), abs(s[k] - s[k + 1])) > SALTO_PICO
                 and (s[k] - s[k - 1]) * (s[k] - s[k + 1]) > 0]
        for k in picos:
            borrar(col, k, "pico_aislado")

    # 4) exclusiones manuales
    for est, patron, desde, hasta, _ in EXCLUSIONES:
        if est != estacion:
            continue
        d = datetime.fromisoformat(desde)
        h = datetime.fromisoformat(hasta) if hasta else datetime.max
        for col in columnas:
            if re.search(patron, col):
                for k, fecha in enumerate(grilla):
                    if d <= fecha < h:
                        borrar(col, k, "exclusion_manual")

    return grilla, serie


def agregar_diario(grilla, serie, min_horas):
    dias = {}
    for k, fecha in enumerate(grilla):
        dias.setdefault(fecha.date(), []).append(k)

    salidas = []
    for nombre, candidatas, f in DIARIO:
        col = next((c for c in candidatas if c in serie), None)
        if col:
            salidas.append((nombre, col, f))
    salidas += [(c, c, statistics.fmean) for c in serie if c.startswith("suelo_")]

    filas = []
    for dia, idx in dias.items():
        fila = {"fecha": dia.isoformat()}
        for nombre, col, f in salidas:
            vals = [serie[col][k] for k in idx if serie[col][k] is not None]
            fila[nombre] = round(f(vals), 2) if len(vals) >= min_horas else None
            if nombre == "t_med":
                fila["horas_t"] = len(vals)
        if fila.get("rad_wm2") is not None:
            fila["rad_mj_m2"] = round(fila["rad_wm2"] * 0.0864, 2)
        filas.append(fila)
    campos = ["fecha", "horas_t"] + [n for n, _, _ in salidas]
    if "rad_wm2" in campos:
        campos.insert(campos.index("rad_wm2") + 1, "rad_mj_m2")
    return campos, filas


def cargar_coordenadas(path_coords, path_alt):
    """Devuelve {slug: (lat, lon, altitud_m)}. Todas las estaciones están en el hemisferio sur."""
    altitudes = {}
    if path_alt.exists():
        with open(path_alt, encoding="utf-8", newline="") as fh:
            altitudes = {slugify(r["estacion"]): float(r["altitud_m"]) for r in csv.DictReader(fh)}
    coords = {}
    with open(path_coords, encoding="utf-8-sig", newline="") as fh:
        lector = csv.reader(fh, delimiter=";")
        next(lector)
        for fila in lector:
            if len(fila) < 3 or not fila[0].strip():
                continue
            nombre, lat, lon = fila[0].strip(), float(fila[1]), float(fila[2])
            if lat > 0:
                print(f"AVISO: latitud positiva en '{nombre}' ({lat}); se usa {-lat}")
                lat = -lat
            coords[slugify(nombre)] = (lat, lon, altitudes.get(slugify(nombre)))
    return coords


def fmt(v):
    return "" if v is None else f"{v:.10g}"


def escribir_csv(path, campos, filas):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=campos, restval="", extrasaction="ignore")
        w.writeheader()
        for fila in filas:
            w.writerow({k: fmt(v) if isinstance(v, float) or v is None else v for k, v in fila.items()})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "raw" / "estaciones")
    ap.add_argument("--coordenadas", type=Path, default=ROOT / "data" / "raw" / "coordenadas_estaciones.csv")
    ap.add_argument("--altitudes", type=Path, default=ROOT / "data" / "external" / "altitud_dem.csv")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "processed")
    ap.add_argument("--min-horas", type=int, default=20, help="horas válidas mínimas para un valor diario")
    args = ap.parse_args()

    (args.out / "horario").mkdir(parents=True, exist_ok=True)
    (args.out / "diario").mkdir(parents=True, exist_ok=True)

    coords = cargar_coordenadas(args.coordenadas, args.altitudes)
    usadas = set()
    variables, qc, estaciones, todas, campos_todas = {}, Counter(), [], [], ["estacion"]
    for path in sorted(args.raw.glob("*.csv")):
        prefijo, columnas, datos, n_filas = leer_estacion(path, variables)
        estacion = slugify(prefijo)
        m = re.match(r"^([A-Z]\d+) - (.+)$", prefijo)
        codigo, nombre = (m[1], m[2]) if m else ("", prefijo)
        # las coordenadas a veces vienen sin el código (p. ej. "Lambayeque - Olmos")
        clave = next((k for k in (estacion, slugify(nombre)) if k in coords), None)
        usadas.add(clave)
        lat, lon, alt = coords.get(clave, (None, None, None))
        if clave is None:
            print(f"AVISO: {estacion} no tiene coordenadas")

        grilla, serie = limpiar(estacion, columnas, datos, qc)
        con_datos = [c for c in columnas if any(v is not None for v in serie[c])]
        filas_h = [{"fecha": f.strftime(FMT_FECHA), **{c: serie[c][k] for c in con_datos}}
                   for k, f in enumerate(grilla)]
        escribir_csv(args.out / "horario" / f"{estacion}.csv", ["fecha"] + con_datos, filas_h)

        campos_d, filas_d = agregar_diario(grilla, {c: serie[c] for c in con_datos}, args.min_horas)
        escribir_csv(args.out / "diario" / f"{estacion}.csv", campos_d, filas_d)
        todas += [{"estacion": estacion, **f} for f in filas_d]
        campos_todas += [c for c in campos_d if c not in campos_todas]

        estaciones.append({
            "estacion": estacion, "codigo": codigo, "nombre": nombre, "archivo_raw": path.name,
            "lat": lat, "lon": lon, "altitud_m": alt,
            "inicio": grilla[0].strftime(FMT_FECHA), "fin": grilla[-1].strftime(FMT_FECHA),
            "horas_esperadas": len(grilla), "horas_con_datos": n_filas,
            "cobertura_pct": round(100 * n_filas / len(grilla), 1),
            "dias": len(filas_d), "dias_con_t_med": sum(f.get("t_med") is not None for f in filas_d),
            "dias_con_pp": sum(f.get("pp_mm") is not None for f in filas_d),
            "columnas": len(con_datos), "columnas_vacias_descartadas": len(columnas) - len(con_datos),
        })
        print(f"{estacion:30s} {len(grilla):6d} h  cobertura {estaciones[-1]['cobertura_pct']:5.1f} %  "
              f"días con T {estaciones[-1]['dias_con_t_med']}/{len(filas_d)}")

    for k in sorted(set(coords) - usadas):
        print(f"AVISO: coordenadas de '{k}' sin archivo de datos")

    escribir_csv(args.out / "diario_todas.csv", campos_todas, todas)
    escribir_csv(args.out / "estaciones.csv", list(estaciones[0]), estaciones)
    escribir_csv(args.out / "variables.csv", ["columna", "nombre_original", "unidad"],
                 [{"columna": c, "nombre_original": v, "unidad": u} for c, (v, u) in sorted(variables.items())])
    escribir_csv(args.out / "qc_resumen.csv", ["estacion", "columna", "regla", "n"],
                 [{"estacion": e, "columna": c, "regla": r, "n": n} for (e, c, r), n in sorted(qc.items())])
    print(f"\nQC: {sum(qc.values())} valores eliminados/ajustados. Salida en {args.out}")


if __name__ == "__main__":
    main()
