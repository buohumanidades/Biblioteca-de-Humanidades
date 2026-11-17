/**
 * Sistema de aprendizaje continuo del Asistente Virtual.
 * - Almacena en localStorage refuerzos por clics implícitos (Ver plano, OPAC,
 *   Copiar citas, Descargar PDF), votos explícitos (👍/👎) y lagunas (0 resultados).
 * - Sincroniza de forma no bloqueante con Google Apps Script / Google Sheets cuando
 *   el backend soporta las acciones de aprendizaje, y aplica re-ranking dinámico.
 */
(function (global) {
    'use strict';

    const STORAGE_KEY = 'buo_asistente_aprendizaje_v1';
    const MAX_ENTRADAS = 400;
    const MAX_LAGUNAS = 100;

    let estadoLocal = cargarEstadoLocal();
    let pesosColectivos = new Map(); // clave "kw|tituloNorm" -> score numérico
    let sinonimosColectivos = {};
    let sincronizacionIniciada = false;

    function cargarEstadoLocal() {
        try {
            const raw = global.localStorage ? global.localStorage.getItem(STORAGE_KEY) : null;
            if (raw) {
                const parsed = JSON.parse(raw);
                return {
                    pesos: parsed.pesos && typeof parsed.pesos === 'object' ? parsed.pesos : {},
                    lagunas: Array.isArray(parsed.lagunas) ? parsed.lagunas : [],
                    historialVotos: parsed.historialVotos && typeof parsed.historialVotos === 'object' ? parsed.historialVotos : {}
                };
            }
        } catch (e) {}
        return { pesos: {}, lagunas: [], historialVotos: {} };
    }

    function guardarEstadoLocal() {
        try {
            if (!global.localStorage) return;
            // Limitar tamaño para no saturar localStorage
            const keys = Object.keys(estadoLocal.pesos);
            if (keys.length > MAX_ENTRADAS) {
                keys.sort((a, b) => Math.abs(estadoLocal.pesos[a].score || 0) - Math.abs(estadoLocal.pesos[b].score || 0));
                keys.slice(0, keys.length - MAX_ENTRADAS).forEach(k => delete estadoLocal.pesos[k]);
            }
            if (estadoLocal.lagunas.length > MAX_LAGUNAS) {
                estadoLocal.lagunas = estadoLocal.lagunas.slice(-MAX_LAGUNAS);
            }
            global.localStorage.setItem(STORAGE_KEY, JSON.stringify(estadoLocal));
        } catch (e) {}
    }

    function claveAprendizaje(keyword, tituloNorm) {
        return `${String(keyword || '').trim()}|${String(tituloNorm || '').trim()}`;
    }

    function enviarSenalRemota(gasUrl, payload) {
        if (!gasUrl) return;
        try {
            const body = JSON.stringify({ tipoEvento: 'aprendizaje', ...payload });
            fetch(gasUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'text/plain;charset=utf-8' },
                body,
                keepalive: true
            }).catch(() => {});
        } catch (e) {}
    }

    /**
     * Descarga pesos colectivos y sinónimos vivos desde Google Apps Script si el endpoint lo soporta.
     */
    async function cargarConocimientoColectivo(gasUrl) {
        if (sincronizacionIniciada || !gasUrl) return;
        sincronizacionIniciada = true;

        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 3500);
        try {
            const res = await fetch(`${gasUrl}?accion=aprendizaje_leer`, { signal: controller.signal });
            if (!res.ok) return;
            const data = await res.json();
            if (data && Array.isArray(data.pesos)) {
                for (const p of data.pesos) {
                    if (p.clave && typeof p.score === 'number') {
                        pesosColectivos.set(p.clave, p.score);
                    }
                }
            }
            if (data && data.sinonimos && typeof data.sinonimos === 'object') {
                sinonimosColectivos = data.sinonimos;
            }
        } catch (e) {
            // Silencioso si el GAS aún no tiene implementado ?accion=aprendizaje_leer
        } finally {
            clearTimeout(timer);
        }
    }

    /**
     * Registra una interacción positiva o negativa sobre un libro concreto para las keywords actuales.
     */
    function registrarInteraccionLibro({ keywords = [], titulo = '', signatura = '', accion = 'clic', delta = 1, gasUrl = '' }) {
        const { limpiarTexto } = global.BUOUtils;
        const tituloNorm = limpiarTexto(titulo);
        if (!tituloNorm || !Array.isArray(keywords) || keywords.length === 0) return;

        const ahora = Date.now();
        for (const kw of keywords.slice(0, 5)) {
            const kwNorm = limpiarTexto(kw);
            if (!kwNorm) continue;
            const clave = claveAprendizaje(kwNorm, tituloNorm);
            const prev = estadoLocal.pesos[clave] || { score: 0, usos: 0, ts: ahora };
            const nuevoScore = Math.max(-12, Math.min(20, (prev.score || 0) + delta));
            estadoLocal.pesos[clave] = { score: nuevoScore, usos: (prev.usos || 0) + 1, ts: ahora };
        }
        guardarEstadoLocal();

        enviarSenalRemota(gasUrl, {
            accion,
            keywords: keywords.slice(0, 5),
            titulo,
            tituloNorm,
            signatura,
            delta
        });
    }

    function registrarVotoRespuesta({ idRespuesta, keywords = [], libros = [], esPositivo = true, gasUrl = '' }) {
        if (!idRespuesta || estadoLocal.historialVotos[idRespuesta]) return false;
        estadoLocal.historialVotos[idRespuesta] = esPositivo ? 1 : -1;

        const delta = esPositivo ? 2.5 : -3;
        for (const libro of (libros || []).slice(0, 8)) {
            registrarInteraccionLibro({
                keywords,
                titulo: libro.titulo || '',
                signatura: Array.isArray(libro.signaturas) ? (libro.signaturas[0] || '') : (libro.signatura || ''),
                accion: esPositivo ? 'voto_positivo' : 'voto_negativo',
                delta,
                gasUrl
            });
        }
        guardarEstadoLocal();
        return true;
    }

    function registrarLagunaBusqueda({ consulta = '', keywords = [], gasUrl = '' }) {
        const consultaLimpia = String(consulta || '').trim();
        if (!consultaLimpia) return;

        const existe = estadoLocal.lagunas.find(l => l.consulta.toLowerCase() === consultaLimpia.toLowerCase());
        if (existe) {
            existe.veces += 1;
            existe.ts = Date.now();
        } else {
            estadoLocal.lagunas.push({
                consulta: consultaLimpia,
                keywords: [...keywords],
                veces: 1,
                ts: Date.now()
            });
        }
        guardarEstadoLocal();

        enviarSenalRemota(gasUrl, {
            accion: 'sin_resultados',
            consulta: consultaLimpia,
            keywords
        });
    }

    /**
     * Devuelve un bonus/penalización de utilidad aprendida para un libro respecto a las keywords actuales.
     */
    function obtenerBonusAprendido(tituloNorm, keywords = []) {
        if (!tituloNorm || !Array.isArray(keywords) || keywords.length === 0) return 0;
        let total = 0;
        for (const kw of keywords) {
            const clave = claveAprendizaje(kw, tituloNorm);
            const local = estadoLocal.pesos[clave];
            if (local && typeof local.score === 'number') {
                total += local.score * 0.65;
            }
            if (pesosColectivos.has(clave)) {
                total += pesosColectivos.get(clave) * 0.55;
            }
        }
        return Math.max(-8, Math.min(18, total));
    }

    function getSinonimosColectivos() {
        return sinonimosColectivos;
    }

    function getResumenAprendizaje() {
        return {
            relacionesAprendidas: Object.keys(estadoLocal.pesos).length,
            lagunasDetectadas: estadoLocal.lagunas.slice(-10).reverse()
        };
    }

    function limpiarAprendizajeLocal() {
        estadoLocal = { pesos: {}, lagunas: [], historialVotos: {} };
        guardarEstadoLocal();
    }

    global.BUOAprendizaje = {
        cargarConocimientoColectivo,
        registrarInteraccionLibro,
        registrarVotoRespuesta,
        registrarLagunaBusqueda,
        obtenerBonusAprendido,
        getSinonimosColectivos,
        getResumenAprendizaje,
        limpiarAprendizajeLocal
    };
})(window);
