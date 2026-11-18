"""
Carga la geometría (LineString) de cada ruta en PostGIS.

Para cada ruta se consulta el servicio de enrutamiento OSRM (OpenStreetMap) con las coordenadas de inicio y fin,
se recibe el trazado sobre las calles reales (GeoJSON) y se guarda en la columna ruta.geom.

Uso:  python cargar_rutas.py
La contraseña se pide por teclado: no se escribe en el código.
"""
import getpass
import json
import time
import urllib.request

import psycopg

# Coordenadas (longitud, latitud) de inicio y fin, tomadas manualmente de mapas.
# Fuente: Google Maps / OpenStreetMap. Orden en OSRM: longitud, latitud.
RUTAS = {
    "R001": ((-75.91310400476506, 4.749452860381801), (-75.9433288203164, 4.763404180152223)),
    "R002": ((-75.90948644915143, 4.734073971020364), (-75.90743207798705, 4.7457193442965995)),
    "R003": ((-75.91741889231885, 4.760496380443598), (-75.9094521203159, 4.750072659031147)),
    "R004": ((-75.91527960497339, 4.752194858469439), (-75.9044048049736, 4.753506382314548)),
    "R005": ((-75.94766223565844, 4.759509307592297), (-75.90948644915143, 4.734073971020364)),
}

OSRM = "https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson"


def trazar(origen, destino):
    """Pide a OSRM el trazado entre dos puntos. Devuelve (geometria_geojson, distancia_km)."""
    url = OSRM.format(lon1=origen[0], lat1=origen[1], lon2=destino[0], lat2=destino[1])
    peticion = urllib.request.Request(url, headers={"User-Agent": "tarea-sig-uniquindio"})
    with urllib.request.urlopen(peticion, timeout=30) as respuesta:
        datos = json.load(respuesta)
    if datos.get("code") != "Ok":
        raise RuntimeError("OSRM respondió: " + str(datos.get("code")))
    ruta = datos["routes"][0]
    return ruta["geometry"], ruta["distance"] / 1000


def main():
    host = input("Servidor [localhost]: ") or "localhost"
    puerto = input("Puerto [5432]: ") or "5432"
    usuario = input("Usuario [postgres]: ") or "postgres"
    clave = getpass.getpass("Contraseña de PostgreSQL: ")

    with psycopg.connect(host=host, port=puerto, dbname="transporte_cartago_sig",
                         user=usuario, password=clave) as conexion:
        with conexion.cursor() as cur:
            for codigo, (origen, destino) in RUTAS.items():
                try:
                    geometria, km = trazar(origen, destino)
                except Exception as error:
                    print(f"{codigo}: no se pudo trazar ({error})")
                    continue
                # Parámetros %s: los valores no se concatenan en el SQL
                cur.execute(
                    "UPDATE ruta SET geom = ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326) WHERE codigo_ruta = %s",
                    (json.dumps(geometria), codigo),
                )
                print(f"{codigo}: trazado guardado ({cur.rowcount} fila, {km:.2f} km por calle)")
                time.sleep(1)  # el servidor público de OSRM es para uso ligero
        conexion.commit()
    print("Listo.")


if __name__ == "__main__":
    main()
