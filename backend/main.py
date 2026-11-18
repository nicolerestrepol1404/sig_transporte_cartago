"""API del SIG Web de transporte de Cartago (FastAPI + psycopg + PostgreSQL/PostGIS)."""
import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row

load_dotenv()  # lee backend/.env (credenciales fuera del código)

app = FastAPI(title="SIG Web transporte Cartago")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


# ---------- Conexión a PostgreSQL/PostGIS ----------
def consultar(sql, params=()):
    """Ejecuta una consulta con parámetros (%s) y devuelve una lista de diccionarios."""
    try:
        with psycopg.connect(
            host=os.getenv("DB_HOST", "localhost"), port=os.getenv("DB_PORT", "5432"),
            dbname=os.getenv("DB_NAME", "transporte_cartago_sig"),
            user=os.getenv("DB_USER", "postgres"), password=os.getenv("DB_PASSWORD"),
            row_factory=dict_row,
        ) as conexion:
            return conexion.execute(sql, params).fetchall()
    except psycopg.Error as error:
        print("Error PostgreSQL:", error)
        raise HTTPException(status_code=500, detail="Error al consultar la base de datos.")


# ---------- Construcción de GeoJSON ----------
def a_feature(fila):
    """Convierte una fila (con la columna 'geometria' en texto GeoJSON) en un Feature."""
    geometria = json.loads(fila.pop("geometria"))
    return {"type": "Feature", "geometry": geometria, "properties": fila}


def coleccion(filas):
    return {"type": "FeatureCollection", "features": [a_feature(f) for f in filas]}


SQL_INCIDENTES = """
    SELECT i.id_incidente, i.fecha, i.descripcion, i.tipo, r.codigo_ruta,
           ST_AsGeoJSON(i.geom) AS geometria
    FROM incidente i
    JOIN servicio s ON s.id_servicio = i.id_servicio
    JOIN ruta r ON r.id_ruta = s.id_ruta
"""


@app.get("/api/salud")
def salud():
    """Comprueba la conexión con PostgreSQL y devuelve la versión de PostGIS."""
    return {"estado": "ok", "postgis": consultar("SELECT PostGIS_Version() AS v")[0]["v"]}


# ---------- F1. Visualización geográfica ----------
@app.get("/api/rutas")
def rutas():
    filas = consultar("""
        SELECT id_ruta, codigo_ruta, nombre_ruta, punto_inicio, punto_final,
               distancia_km::float AS distancia_km, ST_AsGeoJSON(geom) AS geometria
        FROM ruta WHERE geom IS NOT NULL ORDER BY id_ruta""")
    return coleccion(filas)


@app.get("/api/incidentes/tipos")
def tipos_incidente():
    return [f["tipo"] for f in consultar("SELECT DISTINCT tipo FROM incidente ORDER BY tipo")]


@app.get("/api/incidentes")
def incidentes(tipo: str | None = None):
    """Capa de incidentes; el filtro por tipo es opcional."""
    filas = consultar(
        SQL_INCIDENTES + " WHERE i.geom IS NOT NULL AND (%s::text IS NULL OR i.tipo = %s) ORDER BY i.id_incidente",
        (tipo, tipo))
    return coleccion(filas)


# ---------- F3. Consulta espacial: incidentes dentro de un radio ----------
# Las geometrías están en EPSG:4326 (grados). Se convierten a geography para que
# ST_DWithin y ST_Distance trabajen en METROS sobre el elipsoide.
@app.get("/api/incidentes/cercanos")
def incidentes_cercanos(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180),
                        radio: float = Query(500, ge=50, le=5000)):
    filas = consultar(SQL_INCIDENTES.replace("ST_AsGeoJSON(i.geom) AS geometria", """
           round(ST_Distance(i.geom::geography, p.g)::numeric, 1)::float AS distancia_m,
           ST_AsGeoJSON(i.geom) AS geometria""") + """
        CROSS JOIN (SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography AS g) p
        WHERE i.geom IS NOT NULL AND ST_DWithin(i.geom::geography, p.g, %s)
        ORDER BY distancia_m""", (lng, lat, radio))
    cercana = consultar("""
        SELECT codigo_ruta, nombre_ruta, round(d::numeric, 1)::float AS distancia_m FROM (
            SELECT codigo_ruta, nombre_ruta,
                   ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) AS d
            FROM ruta WHERE geom IS NOT NULL) t
        ORDER BY d LIMIT 1""", (lng, lat))
    return {"radio_m": radio, "incidentes": coleccion(filas), "ruta_mas_cercana": cercana[0] if cercana else None}


# ---------- F2. Consulta de atributos de una ruta ----------
@app.get("/api/rutas/{id_ruta}")
def detalle_ruta(id_ruta: int):
    ruta = consultar("""
        SELECT id_ruta, codigo_ruta, nombre_ruta, punto_inicio, punto_final,
               distancia_km::float AS distancia_km, duracion_aprox::text AS duracion_aprox
        FROM ruta WHERE id_ruta = %s""", (id_ruta,))
    if not ruta:
        raise HTTPException(status_code=404, detail="La ruta no existe.")
    horarios = consultar("""
        SELECT dia_semana, hora_inicio::text AS hora_inicio, hora_fin::text AS hora_fin
        FROM horario WHERE id_ruta = %s ORDER BY id_horario""", (id_ruta,))
    servicios = consultar("""
        SELECT s.fecha, s.hora_inicio::text AS hora_inicio, v.placa, c.nombre || ' ' || c.apellido AS conductor
        FROM servicio s
        JOIN vehiculo v ON v.id_vehiculo = s.id_vehiculo
        JOIN conductor c ON c.id_conductor = s.id_conductor
        WHERE s.id_ruta = %s ORDER BY s.fecha DESC, s.hora_inicio DESC LIMIT 5""", (id_ruta,))
    total = consultar("""
        SELECT count(*) AS n FROM incidente i JOIN servicio s ON s.id_servicio = i.id_servicio
        WHERE s.id_ruta = %s""", (id_ruta,))[0]["n"]
    return {"ruta": ruta[0], "horarios": horarios, "servicios": servicios, "incidentes": total}


# ---------- F4. Resumen por ruta: longitud medida vs registrada e incidentes por km ----------
@app.get("/api/resumen-rutas")
def resumen_rutas():
    return consultar("""
        SELECT r.id_ruta, r.codigo_ruta, r.nombre_ruta,
               r.distancia_km::float AS km_registrados,
               round((ST_Length(r.geom::geography) / 1000)::numeric, 2)::float AS km_medidos,
               count(i.id_incidente)::int AS incidentes,
               round((count(i.id_incidente) / (ST_Length(r.geom::geography) / 1000))::numeric, 2)::float AS incidentes_por_km
        FROM ruta r
        LEFT JOIN servicio s ON s.id_ruta = r.id_ruta
        LEFT JOIN incidente i ON i.id_servicio = s.id_servicio
        WHERE r.geom IS NOT NULL
        GROUP BY r.id_ruta
        ORDER BY incidentes_por_km DESC""")


# El frontend se sirve desde la misma API: http://127.0.0.1:8000
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "frontend", html=True), name="web")
