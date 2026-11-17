/**
 * Lógica unificada de resolución de signaturas y localización en plano.
 */
(function (global) {
    'use strict';

    const cachePorBase = new Map();

    function extraerSignaturaBase(signatura) {
        const raw = String(signatura || '').trim();
        if (!raw) return '';
        let fichero = raw.replace(/\/AST$/i, '').replace(/\/COMIC$/i, '');
        if (!fichero.endsWith('/4')) {
            fichero = fichero.replace(/(?<![a-zA-Z])CR(?![a-zA-Z]).*/i, 'CR');
        }
        const ultimoIndiceBarra = fichero.lastIndexOf('/');
        if (ultimoIndiceBarra !== -1) {
            const parteDerecha = fichero.substring(ultimoIndiceBarra + 1);
            return parteDerecha === '4' ? 'HG/4' : fichero.substring(0, ultimoIndiceBarra);
        }
        return fichero;
    }

    function normalizarSigBusqueda(sigBuscada) {
        if (!sigBuscada) return '';
        return String(sigBuscada).replace(/\/[A-Za-zÁÉÍÓÚÜÑáéíóúüñ\s]+(?=\d+=)/gi, '/');
    }

    function compararSignaturaNatural(a, b) {
        return String(a || '').localeCompare(String(b || ''), 'es', { numeric: true, sensitivity: 'base' });
    }

    function obtenerDatosUbicacion(basePath = '') {
        if (cachePorBase.has(basePath)) return cachePorBase.get(basePath);

        const promesa = Promise.all([
            fetch(`${basePath}signaturas.json`).then(r => {
                if (!r.ok) throw new Error('Error cargando signaturas.json');
                return r.json();
            }),
            fetch(`${basePath}plano.json`).then(r => {
                if (!r.ok) throw new Error('Error cargando plano.json');
                return r.json();
            })
        ]).then(([baseDatos, planosDatos]) => {
            const arrDatos = Array.isArray(baseDatos)
                ? baseDatos
                : Object.keys(baseDatos).map(k => {
                    const v = baseDatos[k];
                    if (!v.signatura && !v.signaturas) v.signatura = k;
                    return v;
                });
            const arrPlanos = Array.isArray(planosDatos) ? planosDatos : Object.values(planosDatos);
            return { arrDatos, arrPlanos };
        });

        cachePorBase.set(basePath, promesa);
        return promesa;
    }

    function buscarRegistroSignatura(arrDatos, sigBuscada, sigOriginal) {
        const sigBuscadaUpper = String(sigBuscada || '').trim().toUpperCase();
        const sigOriginalUpper = String(sigOriginal || sigBuscada || '').trim().toUpperCase();

        const candidatos = arrDatos.filter(item => {
            const sigItem = (item.signatura || item.signaturas || '').trim().toUpperCase();
            if (!sigItem || sigItem.startsWith('EXP')) return false;
            if (sigBuscadaUpper === sigItem) return true;
            if (sigBuscadaUpper.startsWith(sigItem)) {
                const nextChar = sigBuscadaUpper.charAt(sigItem.length);
                if (/[0-9]/.test(nextChar) && /[0-9]/.test(sigItem.slice(-1))) return false;
                return true;
            }
            return false;
        });

        candidatos.sort((a, b) => {
            const lenA = (a.signatura || a.signaturas || '').trim().length;
            const lenB = (b.signatura || b.signaturas || '').trim().length;
            return lenB - lenA;
        });

        for (const item of candidatos) {
            const inicio = (item.inicio || '').trim().toUpperCase();
            const fin = (item.fin || '').trim().toUpperCase();
            if (inicio === '') return item;

            const esMayorOIgual = compararSignaturaNatural(sigOriginalUpper, inicio) >= 0;
            const esMenorOIgual = compararSignaturaNatural(sigOriginalUpper, fin) <= 0;
            const empiezaPorFin = sigOriginalUpper.startsWith(fin);
            if (esMayorOIgual && (esMenorOIgual || empiezaPorFin)) {
                return item;
            }
        }
        return null;
    }

    function construirUrlDestino(datosEncontrados, arrPlanos, sigBuscada, sigOriginal, meta = {}, basePath = '') {
        const zona = datosEncontrados.zona || '';
        const planoInfo = arrPlanos.find(p => p.pos_plano == zona) || {};
        const paramsUrl = new URLSearchParams({
            sig: sigBuscada || datosEncontrados.signatura || datosEncontrados.signaturas || '',
            orig: sigOriginal || sigBuscada || datosEncontrados.signatura || '',
            planta: datosEncontrados.planta || '',
            sala: datosEncontrados.sala || '',
            ubicacion: datosEncontrados.ubicacion || '',
            estanteria: datosEncontrados.estanteria || '',
            titulo: meta.titulo || datosEncontrados.titulo || 'Sin título especificado',
            autor: meta.autor || datosEncontrados.autor || datosEncontrados.Autor || '',
            fecha: meta.fecha || datosEncontrados.fecha || datosEncontrados.Fecha || '',
            mapa: planoInfo.mapa || '',
            pos_x: planoInfo.pos_x || '',
            pos_y: planoInfo.pos_y || '',
            tam_punto: planoInfo.tam_punto || '20'
        });
        const destinoHtml = datosEncontrados.html || 'Encontrado.html';
        return `${basePath}${destinoHtml}?${paramsUrl.toString()}`;
    }

    function construirUrlLocalizador(signatura, titulo = '', autor = '', fecha = '', basePath = '') {
        const sig = extraerSignaturaBase(signatura);
        return `${basePath}localizador.html?sig=${encodeURIComponent(sig)}` +
            `&orig=${encodeURIComponent(signatura || '')}` +
            `&titulo=${encodeURIComponent(titulo || '')}` +
            `&autor=${encodeURIComponent(autor || '')}` +
            `&fecha=${encodeURIComponent(fecha || '')}`;
    }

    async function resolverUrlLocalizador(signatura, titulo = '', autor = '', fecha = '', basePath = '') {
        const sigBase = normalizarSigBusqueda(extraerSignaturaBase(signatura));
        if (!sigBase || !sigBase.trim().toUpperCase().startsWith('HG')) {
            return construirUrlLocalizador(signatura, titulo, autor, fecha, basePath);
        }
        try {
            const { arrDatos, arrPlanos } = await obtenerDatosUbicacion(basePath);
            const datosEncontrados = buscarRegistroSignatura(arrDatos, sigBase, signatura);
            if (datosEncontrados) {
                return construirUrlDestino(
                    datosEncontrados,
                    arrPlanos,
                    sigBase,
                    signatura,
                    { titulo, autor, fecha },
                    basePath
                );
            }
        } catch (err) {
            console.warn('Resolución directa no disponible, usando localizador.html:', err);
        }
        return construirUrlLocalizador(signatura, titulo, autor, fecha, basePath);
    }

    global.BUOSignaturas = {
        extraerSignaturaBase,
        normalizarSigBusqueda,
        compararSignaturaNatural,
        obtenerDatosUbicacion,
        buscarRegistroSignatura,
        construirUrlDestino,
        construirUrlLocalizador,
        resolverUrlLocalizador
    };
})(window);
