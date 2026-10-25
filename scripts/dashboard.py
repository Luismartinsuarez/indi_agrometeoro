"""Dashboard de índices agrometeorológicos.

    streamlit run scripts/dashboard.py

Lee data/indices/ (generado por calcular_indices.py) y data/processed/estaciones.csv.
"""
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
INDICES_DIR = ROOT / "data" / "indices"
MAX_ESTACIONES = 3  # la paleta categórica solo es distinguible para daltónicos hasta 3 series

# Paleta categórica (slots 1-3) y rampa secuencial azul; colores por estación en orden fijo
COLORES = ["#2a78d6", "#eb6834", "#1baf7a"]
SECUENCIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

# columna: (id, nombre, unidad, tipo, descripción). tipo "suma" admite acumulado y se dibuja en barras
INDICES = {
    "tmax": ("001", "Tmax – Temperatura máxima media", "°C", "nivel", "Media de las Tmax diarias. Mín. 7 días/década, 21/mes."),
    "tmin": ("002", "Tmin – Temperatura mínima media", "°C", "nivel", "Media de las Tmin diarias. Mín. 7 días/década, 21/mes."),
    "tmed": ("003", "Tmed – Temperatura media", "°C", "nivel", "Media de (Tmax + Tmin)/2. Mín. 7 días/década, 21/mes."),
    "at": ("004", "AT – Amplitud térmica", "°C", "nivel", "Media de Tmax − Tmin. Mín. 7 días/década, mes completo."),
    "tmin_abs": ("005", "Tmin_abs – Temperatura mínima absoluta", "°C", "nivel", "Mínima de las Tmin del periodo. Periodo completo."),
    "tmax_abs": ("006", "Tmax_abs – Temperatura máxima absoluta", "°C", "nivel", "Máxima de las Tmax del periodo. Periodo completo."),
    "tnoc": ("007", "Tnoc – Temperatura nocturna", "°C", "nivel", "Tmed − ajuste por amplitud y duración del día (FAO 56). Mín. 7 días/década, mes completo."),
    "tdiu": ("008", "Tdiu – Temperatura diurna", "°C", "nivel", "Tmed + ajuste por amplitud y duración del día (FAO 56). Mín. 7 días/década, mes completo."),
    "hd": ("009", "Hd – Días con helada", "días", "suma", "Días con Tmin ≤ 0 °C. Periodo completo."),
    "pp": ("010", "PP – Precipitación acumulada", "mm", "suma", "Suma de la precipitación diaria. Periodo completo."),
    "dpp_humedos": ("011", "DPP>0 – Días con lluvia", "días", "suma", "Días con pp ≥ 1 mm. Periodo completo."),
    "dpp_secos": ("012", "DPP<0 – Días sin lluvia", "días", "suma", "Días con pp < 1 mm. Periodo completo."),
    "gd_tt10": ("013", "GD – Grados-día maíz (TT10)", "°C·día", "suma", "0,5·(min(TM, 30) + Tm) − 10. Periodo completo."),
    "gd_ometto": ("013", "GD – Grados-día vid (Ometto)", "°C·día", "suma", "Método de Ometto con Tb = 10 °C. Periodo completo."),
    "gd_winkler": ("013", "GD – Grados-día (Winkler)", "°C·día", "suma", "(TM + Tm)/2 − 10, sin negativos. Periodo completo."),
    "hf": ("014", "HF – Horas frío (conteo horario)", "h", "suma", "Horas con T < 7 °C medidas en la serie horaria. Periodo completo."),
    "hf_crossa": ("014", "HF – Horas frío (Crossa-Raynaud)", "h", "suma", "(7 − m)/(M − m)·24 por día. Periodo completo."),
    "hf_da_mota": ("014", "HF – Horas frío (Da Mota)", "h", "suma", "485,1 − 28,52·Tmed mensual. Solo mensual."),
    "hf_sharpe": ("014", "HF – Horas frío (Sharpe)", "h", "suma", "(654,95 − 34,44·Tmed mensual)/2. Solo mensual."),
    "eto": ("015", "ETo – Evapotranspiración de referencia", "mm", "suma", "Penman-Monteith FAO 56; media diaria × días del periodo. Mín. 7 días/década, 26/mes."),
    "ih": ("016", "Ih – Índice de humedad (PP/ETo)", "", "nivel", "PP / ETo del mismo periodo. > 1: excedente; < 1: déficit."),
}


@st.cache_data
def cargar():
    est = pd.read_csv(ROOT / "data" / "processed" / "estaciones.csv")
    est["etiqueta"] = est.apply(lambda r: f"{r['nombre']} ({r['codigo']})" if isinstance(r["codigo"], str) else r["nombre"], axis=1)
    mensual = pd.read_csv(INDICES_DIR / "mensual.csv")
    mensual["fecha"] = pd.to_datetime(dict(year=mensual.anio, month=mensual.mes, day=1))
    mensual["periodo"] = mensual.fecha.dt.strftime("%Y-%m")
    mensual["clave_clim"] = mensual.mes
    decadal = pd.read_csv(INDICES_DIR / "decadal.csv")
    decadal["fecha"] = pd.to_datetime(dict(year=decadal.anio, month=decadal.mes, day=decadal.decada * 10 - 9))
    decadal["periodo"] = decadal.fecha.dt.strftime("%Y-%m") + " · D" + decadal.decada.astype(str)
    decadal["clave_clim"] = (decadal.mes - 1) * 3 + decadal.decada
    campanas = pd.read_csv(INDICES_DIR / "campanas.csv")
    return est, {"Mensual": mensual, "Decadal": decadal}, campanas


def etiqueta_indice(col):
    i, nombre, unidad, *_ = INDICES[col]
    return f"{i} · {nombre}" + (f" ({unidad})" if unidad else "")


def eje_y(col):
    _, nombre, unidad, *_ = INDICES[col]
    return f"{nombre.split(' – ')[0]} ({unidad})" if unidad else nombre.split(" – ")[0]


def estilo(fig, titulo_y, alto=380):
    fig.update_layout(
        height=alto, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
        legend=dict(orientation="h", y=1.08, x=0), barcornerradius=4, bargap=0.25, bargroupgap=0.1,
        yaxis_title=titulo_y, xaxis_title=None,
    )
    fig.update_xaxes(showgrid=False, automargin=True)
    fig.update_yaxes(zeroline=False, automargin=True)
    return fig


def ticks_es(fechas):
    """Marcas del eje temporal con meses en español (plotly solo los rotula en inglés)."""
    inicio, fin = fechas.min(), fechas.max()
    paso = 1 if (fin - inicio).days < 400 else 3 if (fin - inicio).days < 1100 else 6
    marcas = [f for f in pd.date_range(inicio.to_period("M").to_timestamp(), fin, freq="MS")
              if (f.month - 1) % paso == 0]
    return dict(tickvals=marcas, ticktext=[f"{MESES[f.month - 1]} {f.year}" for f in marcas])


def grafico_serie(df, col, estaciones, etiquetas, escala, acumulado):
    tipo = INDICES[col][3]
    fig = go.Figure()
    for i, est in enumerate(estaciones):
        d = df[df.estacion == est].sort_values("fecha")
        y = d[col].cumsum() if acumulado else d[col]  # cumsum salta los periodos sin dato
        nombre, color = etiquetas[est], COLORES[i]
        hover = f"{nombre}: %{{y:.1f}} {INDICES[col][2]}<extra></extra>"
        if tipo == "suma" and not acumulado and len(estaciones) == 1:
            fig.add_bar(x=d.fecha, y=y, name=nombre, marker_color=color, hovertemplate=hover,
                        marker_line_width=0)
        else:
            modo = "lines+markers" if escala == "Mensual" else "lines"
            fig.add_scatter(x=d.fecha, y=y, name=nombre, mode=modo, line=dict(color=color, width=2),
                            marker=dict(size=8, line=dict(width=2, color="white")), hovertemplate=hover,
                            connectgaps=acumulado)
    if col == "ih":
        fig.add_hline(y=1, line_dash="dot", line_width=1, line_color="gray",
                      annotation_text="PP = ETo", annotation_position="top left")
    fig.update_layout(barmode="group", showlegend=len(estaciones) > 1)
    if not df.empty:
        fig.update_xaxes(**ticks_es(df.fecha))
    titulo = eje_y(col) + (" acumulado" if acumulado else "")
    return estilo(fig, titulo)


def grafico_climatologia(df, col, estaciones, etiquetas, escala):
    fig = go.Figure()
    for i, est in enumerate(estaciones):
        c = df[df.estacion == est].groupby("clave_clim")[col].agg(["mean", "count"]).reset_index()
        if escala == "Mensual":
            x = [MESES[k - 1] for k in c.clave_clim]
        else:
            x = [f"{MESES[(k - 1) // 3]} D{(k - 1) % 3 + 1}" for k in c.clave_clim]
        fig.add_bar(x=x, y=c["mean"], name=etiquetas[est], marker_color=COLORES[i], marker_line_width=0,
                    customdata=c["count"],
                    hovertemplate=f"{etiquetas[est]}: %{{y:.1f}} {INDICES[col][2]} (%{{customdata}} años)<extra></extra>")
    fig.update_layout(barmode="group", showlegend=len(estaciones) > 1)
    return estilo(fig, eje_y(col) + " promedio", alto=320)


def grafico_calor(df, col, escala, etiqueta):
    d = df.copy()
    if escala == "Mensual":
        d["x"] = d.mes.map(lambda m: MESES[m - 1])
        orden = MESES
    else:
        d["x"] = d.apply(lambda r: f"{MESES[r.mes - 1]} D{r.decada}", axis=1)
        orden = [f"{m} D{k}" for m in MESES for k in (1, 2, 3)]
    tabla = d.pivot_table(index="anio", columns="x", values=col).reindex(columns=orden)
    fig = go.Figure(go.Heatmap(
        z=tabla.values, x=tabla.columns, y=tabla.index.astype(str), colorscale=SECUENCIAL, xgap=2, ygap=2,
        hovertemplate=f"%{{y}} %{{x}}: %{{z:.1f}} {INDICES[col][2]}<extra>{etiqueta}</extra>",
        colorbar=dict(title=INDICES[col][2], thickness=12),
    ))
    fig.update_yaxes(autorange="reversed", type="category")
    return estilo(fig, None, alto=80 + 45 * len(tabla))


def metricas(df, col, est, etiqueta):
    d = df[df.estacion == est].dropna(subset=[col])
    unidad = INDICES[col][2]
    st.markdown(f"**{etiqueta}**")
    c1, c2, c3, c4 = st.columns(4)
    if d.empty:
        c1.metric("Sin datos válidos", "–")
        return
    mn, mx = d.loc[d[col].idxmin()], d.loc[d[col].idxmax()]
    c1.metric("Promedio por periodo", f"{d[col].mean():.1f} {unidad}")
    c2.metric("Mínimo", f"{mn[col]:.1f} {unidad}", mn.periodo, delta_color="off", delta_arrow="off")
    c3.metric("Máximo", f"{mx[col]:.1f} {unidad}", mx.periodo, delta_color="off", delta_arrow="off")
    total = (df.estacion == est).sum()
    c4.metric("Periodos válidos", f"{len(d)} / {total}",
              help="Periodos que cumplen el mínimo de días del documento de índices")


def main():
    st.set_page_config(page_title="Índices agrometeorológicos", page_icon="🌦️", layout="wide")
    est, tablas, campanas = cargar()
    etiquetas = dict(zip(est.estacion, est.etiqueta))
    orden_est = est.sort_values("etiqueta").estacion.tolist()

    st.title("Índices agrometeorológicos")

    with st.sidebar:
        st.header("Filtros")
        estaciones = st.multiselect(
            "Estación", orden_est, default=[orden_est[0]], format_func=etiquetas.get,
            max_selections=MAX_ESTACIONES, help=f"Hasta {MAX_ESTACIONES} estaciones para comparar")
        escala = st.radio("Escala", ["Mensual", "Decadal"], horizontal=True)
        df = tablas[escala]
        disponibles = [c for c in INDICES if c in df.columns]
        col = st.selectbox("Índice", disponibles, format_func=etiqueta_indice)
        periodos = sorted(df.fecha.dt.strftime("%Y-%m").unique())
        desde, hasta = st.select_slider("Periodo", options=periodos, value=(periodos[0], periodos[-1]))
        acumulado = INDICES[col][3] == "suma" and st.toggle(
            "Acumulado en el periodo", help="Suma desde el inicio del periodo elegido; los periodos sin dato no suman")

    if not estaciones:
        st.info("Elige al menos una estación en la barra lateral.")
        return

    mes = df.fecha.dt.strftime("%Y-%m")
    sel = df[df.estacion.isin(estaciones) & (mes >= desde) & (mes <= hasta)]

    st.subheader(etiqueta_indice(col))
    st.caption(f"{INDICES[col][4]} · Escala {escala.lower()} · {desde} a {hasta}")

    tab_serie, tab_clim, tab_calor, tab_tabla, tab_camp = st.tabs(
        ["Serie temporal", "Ciclo anual", "Mapa de calor", "Tabla", "Campañas (017)"])

    with tab_serie:
        for e in estaciones:
            metricas(sel, col, e, etiquetas[e])
        st.plotly_chart(grafico_serie(sel, col, estaciones, etiquetas, escala, acumulado), width="stretch")
        faltan = sel[col].isna().sum()
        if faltan:
            st.caption(f"{faltan} periodos sin dato (no cumplen el mínimo de días) aparecen como huecos.")

    with tab_clim:
        st.caption("Promedio de cada mes o década del año sobre los años del periodo elegido.")
        st.plotly_chart(grafico_climatologia(sel, col, estaciones, etiquetas, escala), width="stretch")

    with tab_calor:
        for e in estaciones:
            d = sel[sel.estacion == e]
            st.markdown(f"**{etiquetas[e]}**")
            if d[col].notna().any():
                st.plotly_chart(grafico_calor(d, col, escala, etiquetas[e]), width="stretch")
            else:
                st.caption("Sin datos válidos.")

    with tab_tabla:
        tabla = sel.pivot_table(index="periodo", columns="estacion", values=col, dropna=False)
        tabla = tabla.reindex(columns=estaciones).rename(columns=etiquetas).round(2)
        st.dataframe(tabla, width="stretch")
        st.download_button("Descargar CSV", tabla.to_csv().encode("utf-8"),
                           file_name=f"{col}_{escala.lower()}_{desde}_{hasta}.csv", mime="text/csv")

    with tab_camp:
        st.caption("Precipitación por campaña agrícola (agosto a julio). Solo se muestran campañas completas.")
        c = campanas[campanas.estacion.isin(estaciones)]
        fig = go.Figure()
        for i, e in enumerate(estaciones):
            d = c[c.estacion == e]
            fig.add_bar(x=d.campana, y=d.pp, name=etiquetas[e], marker_color=COLORES[i], marker_line_width=0,
                        customdata=d.dias_pp, hovertemplate=f"{etiquetas[e]}: %{{y:.0f}} mm<extra></extra>")
        fig.update_layout(barmode="group", showlegend=len(estaciones) > 1)
        st.plotly_chart(estilo(fig, "PP campaña (mm)", alto=320), width="stretch")
        if c["clase"].isna().all():
            st.info("La clasificación en año seco o húmedo (quintiles OMM) necesita al menos 30 campañas completas; "
                    "la serie actual tiene muy pocas.")
        st.dataframe(c.assign(estacion=c.estacion.map(etiquetas)).set_index(["estacion", "campana"]), width="stretch")


main()
