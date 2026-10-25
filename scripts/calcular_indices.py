#!/usr/bin/env python3
"""Calcula los índices de indices_agrometeorologicos_formulas.docx a partir de data/processed/.

Salidas en data/indices/:
  diario.csv     valores diarios intermedios (Tmed, AT, Tnoc, Tdiu, GD, HF, ETo...)
  decadal.csv    índices 001-016 por década (días 1-10, 11-20 y 21-fin de mes)
  mensual.csv    índices 001-016 por mes, más HF de Da Mota y Sharpe
  campanas.csv   precipitación por campaña agrícola agosto-julio (índice 017)

Un índice queda vacío si el periodo no cumple el mínimo de días del documento.
Solo usa la biblioteca estándar.
"""
import argparse
import calendar
import csv
import math
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

UMBRAL_LLUVIA = 1.0     # mm; índice 011 (el documento menciona también 0,1 mm)
T_HELADA = 0.0          # °C; criterio OMM, incluye el 0
ALTURA_ANEMOMETRO = 2.0 # m
U2_DEFECTO = 2.0        # m/s; valor recomendado por FAO 56 cuando falta el viento
TB_MAIZ, TMAX_MAIZ = 10.0, 30.0  # método residual modificado TT10
TB_VID = 10.0           # Ometto
TB_WINKLER = 10.0
T_FRIO = 7.0            # °C; horas frío
MIN_CAMPANAS = 30       # series necesarias para los quintiles del índice 017

COMPLETO = "completo"   # todos los días del periodo
# (columna de salida, campo diario, agregación, mínimo por década, mínimo por mes)
INDICES = [
    ("tmax", "tmax", "media", 7, 21),
    ("tmin", "tmin", "media", 7, 21),
    ("tmed", "tmed", "media", 7, 21),
    ("at", "at", "media", 7, COMPLETO),
    ("tmin_abs", "tmin", "min", COMPLETO, COMPLETO),
    ("tmax_abs", "tmax", "max", COMPLETO, COMPLETO),
    ("tnoc", "tnoc", "media", 7, COMPLETO),
    ("tdiu", "tdiu", "media", 7, COMPLETO),
    ("hd", "helada", "suma", COMPLETO, COMPLETO),
    ("pp", "pp", "suma", COMPLETO, COMPLETO),
    ("dpp_humedos", "dia_humedo", "suma", COMPLETO, COMPLETO),
    ("dpp_secos", "dia_seco", "suma", COMPLETO, COMPLETO),
    ("gd_tt10", "gd_tt10", "suma", COMPLETO, COMPLETO),
    ("gd_ometto", "gd_ometto", "suma", COMPLETO, COMPLETO),
    ("gd_winkler", "gd_winkler", "suma", COMPLETO, COMPLETO),
    ("hf", "hf", "suma", COMPLETO, COMPLETO),
    ("hf_crossa", "hf_crossa", "suma", COMPLETO, COMPLETO),
    ("eto", "eto", "media_x_dias", 7, 26),
]


def a_float(s):
    return float(s) if s not in ("", None) else None


# --- astronomía y FAO 56 ------------------------------------------------------

def sol(lat, j):
    """Radiación extraterrestre Ra (MJ/m2/día) y horas de luz N para la latitud (°) y el día juliano."""
    phi = math.radians(lat)
    dr = 1 + 0.033 * math.cos(2 * math.pi * j / 365)
    delta = 0.409 * math.sin(2 * math.pi * j / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    ra = 24 * 60 / math.pi * 0.0820 * dr * (
        ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws))
    return ra, 24 / math.pi * ws


def es(t):
    return 0.6108 * math.exp(17.27 * t / (t + 237.3))


def eto_fao56(tmax, tmin, rs, u2, ea, z, ra):
    tmed = (tmax + tmin) / 2
    es_max, es_min = es(tmax), es(tmin)
    delta = 2049 * (es_max / (tmax + 237.3) ** 2 + es_min / (tmin + 237.3) ** 2)
    p = 101.3 * ((293 - 0.0065 * z) / 293) ** 5.26
    gamma = 0.0016286 * p / (2.501 - 0.002361 * tmed)
    rso = (0.75 + 2e-5 * z) * ra
    rnl = (4.903e-9 * ((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2
           * (0.34 - 0.14 * math.sqrt(ea)) * (1.35 * min(rs / rso, 1.0) - 0.35))
    rn = 0.77 * rs - rnl  # G = 0 a escala diaria
    eto = (0.408 * delta * rn + gamma * 900 / (tmed + 273) * u2 * ((es_max + es_min) / 2 - ea)) \
        / (delta + gamma * (1 + 0.34 * u2))
    return max(eto, 0.0)


# --- índices diarios ----------------------------------------------------------

def gd_ometto(tx, tn, tb):
    if tb > tx:
        return 0.0
    if tn > tb:
        return (tx - tn) / 2 + (tn - tb)
    return (tx - tb) ** 2 / (2 * (tx - tn))


def hf_crossa(tx, tn):
    if tx <= T_FRIO:
        return 24.0
    if tn >= T_FRIO:
        return 0.0
    return (T_FRIO - tn) / (tx - tn) * 24


def calcular_diario(r, meta):
    tx, tn, pp = r["t_max"], r["t_min"], r["pp_mm"]
    d = {"estacion": r["estacion"], "fecha": r["fecha"], "tmax": tx, "tmin": tn, "pp": pp, "hf": r["hf_horas"]}
    if pp is not None:
        d["dia_humedo"] = int(pp >= UMBRAL_LLUVIA)
        d["dia_seco"] = 1 - d["dia_humedo"]
    if tx is None or tn is None or tn > tx:
        return d

    tmed = (tx + tn) / 2
    d.update(tmed=tmed, at=tx - tn, helada=int(tn <= T_HELADA),
             gd_tt10=max(0.0, 0.5 * (min(tx, TMAX_MAIZ) + tn) - TB_MAIZ),
             gd_ometto=gd_ometto(tx, tn, TB_VID),
             gd_winkler=max(0.0, tmed - TB_WINKLER),
             hf_crossa=hf_crossa(tx, tn))

    lat, z = meta["lat"], meta["altitud_m"]
    if lat is None:
        return d
    j = date.fromisoformat(r["fecha"]).timetuple().tm_yday
    ra, n = sol(lat, j)
    t0 = 12 - 0.5 * n
    ajuste = (tx - tn) * (11 + t0) / (4 * math.pi * (12 - t0)) * math.sin(math.pi * (11 - t0) / (11 + t0))
    d.update(tnoc=tmed - ajuste, tdiu=tmed + ajuste)

    rs = r["rad_mj_m2"]
    if rs is None or z is None:
        return d
    if r["hr_max"] is not None and r["hr_min"] is not None:
        ea = (es(tn) * r["hr_max"] + es(tx) * r["hr_min"]) / 200
    elif r["hr_med"] is not None:
        ea = r["hr_med"] / 100 * (es(tx) + es(tn)) / 2
    else:
        ea = es(tn)
    u = r["viento_ms"]
    u2 = U2_DEFECTO if u is None else u * 4.87 / math.log(67.8 * ALTURA_ANEMOMETRO - 5.42)
    d.update(eto=eto_fao56(tx, tn, rs, u2, ea, z, ra), eto_u2_defecto=int(u is None))
    return d


# --- agregación por periodo ---------------------------------------------------

def agregar(dias, n_dias, decadal):
    """dias: registros diarios del periodo (los que existen); n_dias: días del calendario."""
    fila = {"dias_t": sum(d.get("tmed") is not None for d in dias),
            "dias_pp": sum(d.get("pp") is not None for d in dias)}
    for col, campo, modo, min_dec, min_mes in INDICES:
        vals = [d[campo] for d in dias if d.get(campo) is not None]
        minimo = min_dec if decadal else min_mes
        if not vals or len(vals) < (n_dias if minimo == COMPLETO else minimo):
            fila[col] = None
        elif modo == "media":
            fila[col] = sum(vals) / len(vals)
        elif modo == "media_x_dias":
            fila[col] = sum(vals) / len(vals) * n_dias
        elif modo == "suma":
            fila[col] = sum(vals)
        else:
            fila[col] = (min if modo == "min" else max)(vals)
    fila["ih"] = fila["pp"] / fila["eto"] if fila["pp"] is not None and fila["eto"] else None
    if not decadal:
        t = fila["tmed"]
        fila["hf_da_mota"] = None if t is None else max(0.0, 485.1 - 28.52 * t)
        fila["hf_sharpe"] = None if t is None else max(0.0, (654.95 - 34.44 * t) / 2)
    return fila


def quintiles(x):
    """Cuantiles 0,2..0,8 interpolados como en el documento (Q1 = 0,2·x6 + 0,8·x7 con n = 30)."""
    x = sorted(x)
    out = []
    for q in (0.2, 0.4, 0.6, 0.8):
        h = (len(x) - 1) * q
        i = int(h)
        out.append(x[i] + (h - i) * (x[min(i + 1, len(x) - 1)] - x[i]))
    return out


def clasificar(p, q):
    return -2 if p < q[0] else -1 if p < q[1] else 0 if p < q[2] else 1 if p < q[3] else 2


def campanas(diario):
    por_est = defaultdict(lambda: defaultdict(list))
    for d in diario:
        f = date.fromisoformat(d["fecha"])
        inicio = f.year if f.month >= 8 else f.year - 1
        por_est[d["estacion"]][inicio].append(d)
    filas = []
    for est, camps in por_est.items():
        est_filas = []
        for inicio, dias in sorted(camps.items()):
            n_cal = (date(inicio + 1, 8, 1) - date(inicio, 8, 1)).days
            vals = [d["pp"] for d in dias if d.get("pp") is not None]
            est_filas.append({"estacion": est, "campana": f"{inicio}-{inicio + 1}", "dias_pp": len(vals),
                              "dias_campana": n_cal, "completa": int(len(vals) == n_cal),
                              "pp": sum(vals) if len(vals) == n_cal else None})
        completas = [f["pp"] for f in est_filas if f["pp"] is not None]
        if len(completas) >= MIN_CAMPANAS:
            q = quintiles(completas)
            for f in est_filas:
                if f["pp"] is not None:
                    f["clase"] = clasificar(f["pp"], q)
        filas += est_filas
    return filas


def escribir(path, filas, campos):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=campos, restval="", extrasaction="ignore")
        w.writeheader()
        for f in filas:
            w.writerow({k: "" if v is None else round(v, 2) if isinstance(v, float) else v for k, v in f.items()})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--processed", type=Path, default=ROOT / "data" / "processed")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "indices")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with open(args.processed / "estaciones.csv", encoding="utf-8", newline="") as fh:
        meta = {r["estacion"]: {"lat": a_float(r["lat"]), "altitud_m": a_float(r["altitud_m"])}
                for r in csv.DictReader(fh)}

    campos_num = ["t_max", "t_min", "pp_mm", "hr_max", "hr_min", "hr_med", "rad_mj_m2", "viento_ms", "hf_horas"]
    diario = []
    with open(args.processed / "diario_todas.csv", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            r.update({c: a_float(r.get(c)) for c in campos_num})
            diario.append(calcular_diario(r, meta[r["estacion"]]))

    grupos_dec, grupos_mes = defaultdict(list), defaultdict(list)
    for d in diario:
        f = date.fromisoformat(d["fecha"])
        grupos_dec[(d["estacion"], f.year, f.month, min((f.day - 1) // 10, 2) + 1)].append(d)
        grupos_mes[(d["estacion"], f.year, f.month)].append(d)

    decadal = []
    for (est, y, m, dec), dias in sorted(grupos_dec.items()):
        n = 10 if dec < 3 else calendar.monthrange(y, m)[1] - 20
        decadal.append({"estacion": est, "anio": y, "mes": m, "decada": dec, **agregar(dias, n, True)})
    mensual = [{"estacion": est, "anio": y, "mes": m, **agregar(dias, calendar.monthrange(y, m)[1], False)}
               for (est, y, m), dias in sorted(grupos_mes.items())]

    indices = [c for c, *_ in INDICES] + ["ih"]
    escribir(args.out / "diario.csv", diario,
             ["estacion", "fecha", "tmax", "tmin", "tmed", "at", "tnoc", "tdiu", "helada", "pp", "dia_humedo",
              "dia_seco", "gd_tt10", "gd_ometto", "gd_winkler", "hf", "hf_crossa", "eto", "eto_u2_defecto"])
    escribir(args.out / "decadal.csv", decadal, ["estacion", "anio", "mes", "decada", "dias_t", "dias_pp"] + indices)
    escribir(args.out / "mensual.csv", mensual,
             ["estacion", "anio", "mes", "dias_t", "dias_pp"] + indices + ["hf_da_mota", "hf_sharpe"])
    escribir(args.out / "campanas.csv", campanas(diario),
             ["estacion", "campana", "dias_pp", "dias_campana", "completa", "pp", "clase"])
    print(f"{len(diario)} días, {len(decadal)} décadas, {len(mensual)} meses -> {args.out}")


if __name__ == "__main__":
    main()
