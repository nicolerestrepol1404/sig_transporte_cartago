// SIG Web · Transporte de Cartago: el frontend solo consulta la API; los datos vienen de PostgreSQL/PostGIS.
const API = "http://127.0.0.1:8000/api";

// ---------- Mapa y capas base ----------
const mapa = L.map("mapa").setView([4.7487, -75.917], 13);
const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" }).addTo(mapa);
const satelite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19, attribution: "Tiles &copy; Esri" });
L.control.layers({ "OpenStreetMap": osm, "Imagen satelital": satelite }).addTo(mapa);
L.control.scale({ imperial: false }).addTo(mapa);

const COLORES_RUTA = ["#1B3A5C", "#3F7D3C", "#8B5E3C", "#b03030", "#6a3d9a"];
const COLORES_TIPO = { "Retraso": "#e67e22", "Tecnico": "#2980b9", "Comportamiento": "#8e44ad", "Objetos Perdidos": "#16a085" };
const capaRutas = L.layerGroup().addTo(mapa);
const capaIncidentes = L.layerGroup().addTo(mapa);
const capaConsulta = L.layerGroup().addTo(mapa);
let capasRuta = {};          // id_ruta -> capa Leaflet
let modoCercanos = false;    // true cuando se espera el clic de la consulta espacial

// ---------- Utilidades ----------
function esc(t) {            // evita insertar HTML proveniente de la base de datos
    return String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function aviso(id, texto) {
    document.getElementById(id).innerHTML = `<p class="error">${esc(texto)}</p>`;
}

// Solicitud GET a la API: arma la URL con parámetros, valida el estado HTTP y devuelve el JSON
async function pedir(ruta, params = {}) {
    const url = new URL(API + ruta);
    Object.entries(params).forEach(([k, v]) => { if (v !== "" && v != null) url.searchParams.set(k, v); });
    const respuesta = await fetch(url);
    if (!respuesta.ok) {
        let detalle = "Error " + respuesta.status;
        try { const d = (await respuesta.json()).detail; if (typeof d === "string") detalle = d; else detalle = "Parámetros no válidos."; } catch (e) { }
        throw new Error(detalle);
    }
    return respuesta.json();
}

// ---------- F1. Rutas e incidentes (GeoJSON desde PostGIS) ----------
async function cargarRutas() {
    const datos = await pedir("/rutas");
    capaRutas.clearLayers();
    L.geoJSON(datos, {
        style: f => ({ color: COLORES_RUTA[(f.properties.id_ruta - 1) % 5], weight: 5, opacity: 0.85 }),
        onEachFeature: (f, capa) => {
            capasRuta[f.properties.id_ruta] = capa;
            capa.bindTooltip(f.properties.codigo_ruta + " · " + f.properties.nombre_ruta);
            capa.on("click", () => { if (!modoCercanos) mostrarDetalle(f.properties.id_ruta); });
        }
    }).addTo(capaRutas);
}

async function cargarIncidentes() {
    try {
        const datos = await pedir("/incidentes", { tipo: document.getElementById("filtro-tipo").value });
        capaIncidentes.clearLayers();
        L.geoJSON(datos, {
            pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 8, color: "#fff", weight: 2, fillOpacity: 1,
                fillColor: COLORES_TIPO[f.properties.tipo] || "#555" }),
            onEachFeature: (f, capa) => capa.bindPopup(
                `<b>${esc(f.properties.tipo)}</b><br>Ruta ${esc(f.properties.codigo_ruta)}<br>` +
                `${esc(f.properties.fecha)}<br>${esc(f.properties.descripcion)}`)
        }).addTo(capaIncidentes);
    } catch (e) { aviso("estado", e.message); }
}

document.getElementById("filtro-tipo").onchange = cargarIncidentes;

// ---------- F2. Consulta de atributos de una ruta ----------
async function mostrarDetalle(id) {
    try {
        const d = await pedir("/rutas/" + id);
        Object.values(capasRuta).forEach(c => c.setStyle({ weight: 5 }));
        capasRuta[id].setStyle({ weight: 9 });
        mapa.fitBounds(capasRuta[id].getBounds(), { padding: [40, 40] });
        const r = d.ruta;
        const horarios = d.horarios.map(h => `<li>${esc(h.dia_semana)}: ${esc(h.hora_inicio)} – ${esc(h.hora_fin)}</li>`).join("");
        const servicios = d.servicios.map(s =>
            `<tr><td>${esc(s.fecha)}</td><td>${esc(s.placa)}</td><td>${esc(s.conductor)}</td></tr>`).join("");
        document.getElementById("detalle").innerHTML = `
            <p><b>${esc(r.codigo_ruta)} · ${esc(r.nombre_ruta)}</b><br>
            ${esc(r.punto_inicio)} → ${esc(r.punto_final)}<br>
            Distancia registrada: ${esc(r.distancia_km)} km · Duración: ${esc(r.duracion_aprox)}<br>
            Incidentes asociados: <b>${esc(d.incidentes)}</b></p>
            <b>Horarios</b><ul>${horarios || "<li>Sin horarios</li>"}</ul>
            <b>Últimos servicios</b>
            <table><tr><th>Fecha</th><th>Placa</th><th>Conductor</th></tr>${servicios}</table>`;
    } catch (e) { aviso("detalle", e.message); }
}

// ---------- F3. Consulta espacial: incidentes dentro de un radio ----------
document.getElementById("btn-cercanos").onclick = () => {
    modoCercanos = true;
    mapa.getContainer().style.cursor = "crosshair";
    document.getElementById("res-cercanos").innerHTML = '<p class="nota">Haz clic en el mapa para fijar el punto.</p>';
};

mapa.on("click", async e => {
    if (!modoCercanos) return;
    modoCercanos = false;
    mapa.getContainer().style.cursor = "";
    const radio = Number(document.getElementById("radio").value);
    if (!Number.isFinite(radio) || radio < 50 || radio > 5000) {
        aviso("res-cercanos", "El radio debe estar entre 50 y 5000 m.");
        return;
    }
    try {
        const d = await pedir("/incidentes/cercanos", { lat: e.latlng.lat, lng: e.latlng.lng, radio: radio });
        capaConsulta.clearLayers();
        L.circle(e.latlng, { radius: radio, color: "#b03030", fillOpacity: 0.08 }).addTo(capaConsulta);
        L.marker(e.latlng).addTo(capaConsulta);
        L.geoJSON(d.incidentes, { pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 12, color: "#b03030", weight: 3, fill: false }) }).addTo(capaConsulta);
        const filas = d.incidentes.features.map(f => `<tr><td>${esc(f.properties.tipo)}</td><td>${esc(f.properties.codigo_ruta)}</td><td>${esc(f.properties.distancia_m)} m</td></tr>`).join("");
        const rc = d.ruta_mas_cercana;
        document.getElementById("res-cercanos").innerHTML =
            `<p><b>${d.incidentes.features.length}</b> incidente(s) a menos de ${esc(d.radio_m)} m.<br>` +
            (rc ? `Ruta más cercana: <b>${esc(rc.codigo_ruta)}</b> (${esc(rc.distancia_m)} m)</p>` : "</p>") +
            (filas ? `<table><tr><th>Tipo</th><th>Ruta</th><th>Distancia</th></tr>${filas}</table>` : "");
    } catch (err) { aviso("res-cercanos", err.message); }
});

// ---------- F4. Resumen por ruta ----------
document.getElementById("btn-resumen").onclick = async () => {
    try {
        const filas = await pedir("/resumen-rutas");
        const html = filas.map(f => `<tr><td>${esc(f.codigo_ruta)}</td><td>${esc(f.km_registrados)}</td><td>${esc(f.km_medidos)}</td>` +
            `<td>${esc(f.incidentes)}</td><td>${esc(f.incidentes_por_km)}</td></tr>`).join("");
        document.getElementById("res-resumen").innerHTML =
            `<table><tr><th>Ruta</th><th>Km reg.</th><th>Km med.</th><th>Inc.</th><th>Inc./km</th></tr>${html}</table>` +
            '<p class="nota">Km medidos: ST_Length sobre el trazado (geography). Ordenado por incidentes por km.</p>';
    } catch (e) { aviso("res-resumen", e.message); }
};

// ---------- Inicio ----------
(async function () {
    try {
        const s = await pedir("/salud");
        document.getElementById("estado").textContent = "Conectado a PostgreSQL · PostGIS " + s.postgis.split(" ")[0];
        await cargarRutas();
        const tipos = await pedir("/incidentes/tipos");
        document.getElementById("filtro-tipo").innerHTML += tipos.map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
        await cargarIncidentes();
    } catch (e) {
        document.getElementById("estado").innerHTML = `<span class="error">No se pudo conectar con la API: ${esc(e.message)}</span>`;
    }
})();
