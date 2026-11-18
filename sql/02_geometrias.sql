-- =====================================================================
-- 02_geometrias.sql
-- Componente espacial agregado a la base "transporte_cartago_sig"
-- (copia de la base original "ProyectoFinal" de Bases de Datos).
--
-- Requisitos: PostgreSQL con la extensión PostGIS disponible.
-- El script se puede ejecutar varias veces sin dañar nada.
-- Sistema de referencia: EPSG:4326 (WGS 84, longitud/latitud en grados).
-- =====================================================================

-- 1) Activar PostGIS
CREATE EXTENSION IF NOT EXISTS postgis;

-- 2) Columnas de geometría
--    ruta.geom      -> LineString (trazado de la ruta sobre las calles)
--    incidente.geom -> Point      (ubicación aproximada del incidente)
ALTER TABLE ruta      ADD COLUMN IF NOT EXISTS geom geometry(LineString, 4326);
ALTER TABLE incidente ADD COLUMN IF NOT EXISTS geom geometry(Point, 4326);

-- 3) Índices espaciales GIST (aceleran ST_DWithin, ST_Distance, etc.)
CREATE INDEX IF NOT EXISTS idx_ruta_geom      ON ruta      USING gist (geom);
CREATE INDEX IF NOT EXISTS idx_incidente_geom ON incidente USING gist (geom);

-- 4) Geometría de las rutas
--    NO se carga con SQL: se obtiene ejecutando scripts/cargar_rutas.py,
--    que consulta el servicio OSRM (OpenStreetMap) y guarda el trazado con
--    ST_SetSRID(ST_GeomFromGeoJSON(...), 4326).
--    IMPORTANTE: ejecutar cargar_rutas.py ANTES del paso 5.

-- 5) Geometría de los incidentes
--    La base original no tenía ubicación. Cada incidente se coloca sobre la
--    ruta de su servicio, en una fracción de la línea entre 0.10 y 0.89
--    (calculada a partir de su id).
--    LIMITACIÓN: son posiciones aproximadas/derivadas, no medidas en campo.
UPDATE incidente i
SET geom = ST_LineInterpolatePoint(r.geom, ((i.id_incidente * 37) % 80 + 10) / 100.0)
FROM servicio s
JOIN ruta r ON r.id_ruta = s.id_ruta
WHERE s.id_servicio = i.id_servicio
  AND r.geom IS NOT NULL;

-- 6) Comprobación rápida (debe dar 5 rutas y 8 incidentes con geometría)
SELECT (SELECT count(*) FROM ruta      WHERE geom IS NOT NULL) AS rutas_con_geom,
       (SELECT count(*) FROM incidente WHERE geom IS NOT NULL) AS incidentes_con_geom;
