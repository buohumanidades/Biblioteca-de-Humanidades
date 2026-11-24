/**
 * Gestión centralizada de horarios, periodos especiales y festivos
 * (nacionales, Principado de Asturias, Oviedo y Universidad de Oviedo)
 * leídos desde data/horarios.json.
 */
(function (global) {
    'use strict';

    const DEFAULT_URL = 'data/horarios.json';

    const CONFIG_DEFECTO = {
        zonaHoraria: 'Europe/Madrid',
        horarioHabitual: {
            nombre: 'Horario lectivo habitual',
            lunesAViernes: [['08:00', '20:00']],
            sabado: [],
            domingo: []
        },
        periodosEspeciales: [],
        festivosFijos: [],
        festivosPorAnio: {},
        excepcionesPuntuales: []
    };

    const DIAS_SEMANA_ES = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];

    const ETIQUETA_AMBITO = {
        nacional: 'Festivo nacional',
        asturias: 'Festivo en Asturias',
        oviedo: 'Festivo local (Oviedo)',
        uniovi: 'Festivo Universidad de Oviedo',
        biblioteca: 'Cierre de biblioteca'
    };

    let cacheConfig = null;
    let promesaCarga = null;

    function aMinutos(hhmm) {
        const partes = String(hhmm || '').split(':');
        if (partes.length < 2) return null;
        const h = parseInt(partes[0], 10);
        const m = parseInt(partes[1], 10);
        if (Number.isNaN(h) || Number.isNaN(m)) return null;
        return h * 60 + m;
    }

    function formatearFranjas(franjas) {
        if (!Array.isArray(franjas) || franjas.length === 0) return '';
        return franjas
            .filter(f => Array.isArray(f) && f.length >= 2)
            .map(f => `${f[0]} a ${f[1]} h`)
            .join(' y ');
    }

    function formatearFechaCorta(fechaISO) {
        const [anio, mes, dia] = String(fechaISO || '').split('-');
        if (!anio || !mes || !dia) return fechaISO;
        return `${dia}/${mes}`;
    }

    function obtenerPartesEnZona(fechaDate, zonaHoraria) {
        const partes = new Intl.DateTimeFormat('en-GB', {
            timeZone: zonaHoraria || 'Europe/Madrid',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            weekday: 'short',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        }).formatToParts(fechaDate);

        const mapa = Object.fromEntries(partes.map(p => [p.type, p.value]));
        const hora = mapa.hour === '24' ? 0 : parseInt(mapa.hour, 10);
        const minuto = parseInt(mapa.minute, 10);
        const fechaISO = `${mapa.year}-${mapa.month}-${mapa.day}`;
        const mapDia = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

        return {
            anio: mapa.year,
            mes: mapa.month,
            dia: mapa.day,
            fechaISO,
            mmdd: `${mapa.month}-${mapa.day}`,
            diaSemana: mapDia[mapa.weekday] ?? 1,
            hora,
            minuto,
            minutosActuales: hora * 60 + minuto
        };
    }

    function sumarDiasISO(fechaISO, dias) {
        const [y, m, d] = fechaISO.split('-').map(Number);
        const dt = new Date(Date.UTC(y, m - 1, d + dias));
        const yy = dt.getUTCFullYear();
        const mm = String(dt.getUTCMonth() + 1).padStart(2, '0');
        const dd = String(dt.getUTCDate()).padStart(2, '0');
        return `${yy}-${mm}-${dd}`;
    }

    function diaSemanaDeISO(fechaISO) {
        const [y, m, d] = fechaISO.split('-').map(Number);
        return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
    }

    function franjasParaDiaSemana(bloque, diaSemana) {
        if (!bloque) return [];
        if (diaSemana === 6) return Array.isArray(bloque.sabado) ? bloque.sabado : [];
        if (diaSemana === 0) return Array.isArray(bloque.domingo) ? bloque.domingo : [];
        return Array.isArray(bloque.lunesAViernes) ? bloque.lunesAViernes : [];
    }

    function buscarFestivoEnFecha(fechaISO, config) {
        const anio = fechaISO.slice(0, 4);
        const mmdd = fechaISO.slice(5, 10);

        const listaAnio = config.festivosPorAnio && Array.isArray(config.festivosPorAnio[anio])
            ? config.festivosPorAnio[anio]
            : [];
        const festivoAnio = listaAnio.find(f => f && f.fecha === fechaISO);
        if (festivoAnio) return festivoAnio;

        const listaFijos = Array.isArray(config.festivosFijos) ? config.festivosFijos : [];
        const festivoFijo = listaFijos.find(f => f && f.fecha === mmdd);
        if (festivoFijo) return festivoFijo;

        return null;
    }

    function buscarPeriodoEnFecha(fechaISO, config) {
        const periodos = Array.isArray(config.periodosEspeciales) ? config.periodosEspeciales : [];
        return periodos.find(p => p && p.desde && p.hasta && fechaISO >= p.desde && fechaISO <= p.hasta) || null;
    }

    /**
     * Evalúa qué regla aplica a un día YYYY-MM-DD según prioridad:
     * 1) excepcionesPuntuales
     * 2) festivosPorAnio / festivosFijos
     * 3) periodosEspeciales
     * 4) horarioHabitual
     */
    function evaluarDia(fechaISO, config) {
        const cfg = config || cacheConfig || CONFIG_DEFECTO;
        const diaSemana = diaSemanaDeISO(fechaISO);
        const esFinDeSemana = diaSemana === 0 || diaSemana === 6;

        // 1. Excepciones puntuales (máxima prioridad)
        const excepciones = Array.isArray(cfg.excepcionesPuntuales) ? cfg.excepcionesPuntuales : [];
        const exc = excepciones.find(e => e && e.fecha === fechaISO);
        if (exc) {
            const franjasExc = exc.cerrado ? [] : (Array.isArray(exc.franjas) ? exc.franjas : []);
            return {
                fechaISO,
                diaSemana,
                esFinDeSemana,
                origen: 'excepcion',
                cerrado: Boolean(exc.cerrado) || franjasExc.length === 0,
                motivo: exc.nombre || 'Horario especial',
                ambito: exc.ambito || 'biblioteca',
                periodoNombre: exc.nombre || 'Horario especial',
                esEspecial: true,
                franjas: franjasExc
            };
        }

        // 2. Festivos (por año o fijos)
        const festivo = buscarFestivoEnFecha(fechaISO, cfg);
        if (festivo) {
            return {
                fechaISO,
                diaSemana,
                esFinDeSemana,
                origen: 'festivo',
                cerrado: true,
                motivo: festivo.nombre || 'Festivo',
                ambito: festivo.ambito || 'nacional',
                periodoNombre: festivo.nombre || 'Festivo',
                esEspecial: true,
                franjas: []
            };
        }

        // 3. Periodos especiales (verano, Navidad, exámenes, cierre estival...)
        const periodo = buscarPeriodoEnFecha(fechaISO, cfg);
        if (periodo) {
            if (periodo.cerrado) {
                return {
                    fechaISO,
                    diaSemana,
                    esFinDeSemana,
                    origen: 'periodo',
                    cerrado: true,
                    motivo: periodo.nombre || 'Periodo no lectivo',
                    ambito: 'biblioteca',
                    periodoNombre: periodo.nombre || 'Periodo cerrado',
                    esEspecial: true,
                    franjas: []
                };
            }
            const franjasPeriodo = franjasParaDiaSemana(periodo, diaSemana);
            return {
                fechaISO,
                diaSemana,
                esFinDeSemana,
                origen: 'periodo',
                cerrado: franjasPeriodo.length === 0,
                motivo: franjasPeriodo.length === 0 ? 'Fin de semana' : (periodo.nombre || 'Horario especial'),
                ambito: 'biblioteca',
                periodoNombre: periodo.nombre || 'Horario especial',
                esEspecial: true,
                franjas: franjasPeriodo
            };
        }

        // 4. Horario habitual
        const habitual = cfg.horarioHabitual || CONFIG_DEFECTO.horarioHabitual;
        const franjasHabituales = franjasParaDiaSemana(habitual, diaSemana);
        return {
            fechaISO,
            diaSemana,
            esFinDeSemana,
            origen: 'habitual',
            cerrado: franjasHabituales.length === 0,
            motivo: franjasHabituales.length === 0 ? 'Fin de semana' : (habitual.nombre || 'Horario lectivo habitual'),
            ambito: null,
            periodoNombre: habitual.nombre || 'Horario lectivo habitual',
            esEspecial: false,
            franjas: franjasHabituales
        };
    }

    function buscarProximaApertura(fechaISO, minutosActuales, config) {
        const cfg = config || cacheConfig || CONFIG_DEFECTO;

        // Comprobar primero si aún abre más tarde hoy
        const hoy = evaluarDia(fechaISO, cfg);
        if (!hoy.cerrado && hoy.franjas.length > 0) {
            for (const franja of hoy.franjas) {
                const ini = aMinutos(franja[0]);
                if (ini !== null && minutosActuales < ini) {
                    return {
                        esHoy: true,
                        fechaISO,
                        hora: franja[0],
                        texto: `Abre hoy a las ${franja[0]} h`
                    };
                }
            }
        }

        // Buscar en los siguientes 45 días laborables
        for (let offset = 1; offset <= 45; offset++) {
            const sigISO = sumarDiasISO(fechaISO, offset);
            const dia = evaluarDia(sigISO, cfg);
            if (!dia.cerrado && dia.franjas.length > 0) {
                const hora = dia.franjas[0][0];
                const nombreDia = DIAS_SEMANA_ES[dia.diaSemana];
                const texto = offset === 1
                    ? `Abre mañana (${nombreDia}) a las ${hora} h`
                    : `Próxima apertura: ${nombreDia} ${formatearFechaCorta(sigISO)} a las ${hora} h`;
                return { esHoy: false, fechaISO: sigISO, hora, texto };
            }
        }

        return null;
    }

    function buscarProximoAviso(fechaISO, config, diasHorizonte = 30) {
        const cfg = config || cacheConfig || CONFIG_DEFECTO;
        const hoy = evaluarDia(fechaISO, cfg);

        for (let offset = 1; offset <= diasHorizonte; offset++) {
            const sigISO = sumarDiasISO(fechaISO, offset);
            const dia = evaluarDia(sigISO, cfg);

            if ((dia.origen === 'festivo' || (dia.origen === 'excepcion' && dia.cerrado)) && !dia.esFinDeSemana) {
                return `Próximo cierre: ${formatearFechaCorta(sigISO)} (${dia.motivo})`;
            }
            if (dia.origen === 'excepcion' && !dia.cerrado) {
                return `Horario especial el ${formatearFechaCorta(sigISO)}: ${formatearFranjas(dia.franjas)} (${dia.motivo})`;
            }
            if (dia.origen === 'periodo' && dia.periodoNombre !== hoy.periodoNombre) {
                if (dia.cerrado) {
                    return `Desde el ${formatearFechaCorta(sigISO)}: ${dia.periodoNombre}`;
                }
                if (dia.franjas.length > 0) {
                    return `Desde el ${formatearFechaCorta(sigISO)}: ${dia.periodoNombre} (${formatearFranjas(dia.franjas)})`;
                }
            }
        }
        return '';
    }

    function evaluarEstado(fechaDate, config) {
        const cfg = config || cacheConfig || CONFIG_DEFECTO;
        const ahora = fechaDate instanceof Date ? fechaDate : new Date();
        const p = obtenerPartesEnZona(ahora, cfg.zonaHoraria);
        const dia = evaluarDia(p.fechaISO, cfg);

        let abierta = false;
        let franjaActiva = null;
        if (!dia.cerrado && dia.franjas.length > 0) {
            for (const franja of dia.franjas) {
                const ini = aMinutos(franja[0]);
                const fin = aMinutos(franja[1]);
                if (ini !== null && fin !== null && p.minutosActuales >= ini && p.minutosActuales < fin) {
                    abierta = true;
                    franjaActiva = franja;
                    break;
                }
            }
        }

        const horarioHoyStr = formatearFranjas(dia.franjas);
        const proxima = abierta ? null : buscarProximaApertura(p.fechaISO, p.minutosActuales, cfg);
        const proximoAviso = buscarProximoAviso(p.fechaISO, cfg, 30);

        let estado = 'cerrada_horario';
        let textoBarra = '';
        let resumenHoy = '';
        let tituloTooltip = '';

        if (abierta) {
            estado = 'abierta';
            if (dia.esEspecial) {
                textoBarra = `ABIERTA AHORA · ${dia.periodoNombre} (${horarioHoyStr})`;
                resumenHoy = `${dia.periodoNombre}: ${horarioHoyStr}`;
            } else {
                textoBarra = `ABIERTA AHORA · L–V ${horarioHoyStr}`;
                resumenHoy = `Lunes a viernes: ${horarioHoyStr}`;
            }
            tituloTooltip = `${dia.periodoNombre} · Abierta hasta las ${franjaActiva[1]} h`;
        } else if (dia.origen === 'festivo') {
            estado = 'cerrada_festivo';
            const tipoFestivo = ETIQUETA_AMBITO[dia.ambito] || 'Festivo';
            textoBarra = `CERRADA HOY · ${dia.motivo}`;
            resumenHoy = `Hoy cerrada (${tipoFestivo}: ${dia.motivo})`;
            tituloTooltip = `${tipoFestivo}: ${dia.motivo}${proxima ? ' · ' + proxima.texto : ''}`;
        } else if (dia.origen === 'excepcion' && dia.cerrado) {
            estado = 'cerrada_excepcion';
            textoBarra = `CERRADA HOY · ${dia.motivo}`;
            resumenHoy = `Hoy cerrada (${dia.motivo})`;
            tituloTooltip = `${dia.motivo}${proxima ? ' · ' + proxima.texto : ''}`;
        } else if (dia.origen === 'periodo' && dia.cerrado) {
            estado = 'cerrada_periodo';
            textoBarra = `CERRADA · ${dia.motivo}`;
            resumenHoy = `Cerrada (${dia.motivo})`;
            tituloTooltip = `${dia.motivo}${proxima ? ' · ' + proxima.texto : ''}`;
        } else if (dia.cerrado && dia.esFinDeSemana) {
            estado = 'cerrada_fin_de_semana';
            const habStr = formatearFranjas((cfg.horarioHabitual || CONFIG_DEFECTO.horarioHabitual).lunesAViernes);
            textoBarra = proxima
                ? `CERRADA · Fin de semana · ${proxima.texto}`
                : `CERRADA · Fin de semana · L–V ${habStr}`;
            resumenHoy = `Fin de semana (cerrada) · L–V: ${habStr}`;
            tituloTooltip = proxima ? `Cerrada por fin de semana · ${proxima.texto}` : 'Cerrada los fines de semana';
        } else {
            estado = 'cerrada_horario';
            textoBarra = dia.esEspecial
                ? `CERRADA AHORA · ${dia.periodoNombre} (${horarioHoyStr})`
                : `CERRADA AHORA · L–V ${horarioHoyStr}`;
            resumenHoy = dia.esEspecial
                ? `${dia.periodoNombre}: ${horarioHoyStr}`
                : `Lunes a viernes: ${horarioHoyStr}`;
            tituloTooltip = proxima ? proxima.texto : resumenHoy;
        }

        return {
            abierta,
            estado,
            fechaISO: p.fechaISO,
            dia,
            textoBarra,
            resumenHoy,
            tituloTooltip,
            proximaAperturaTexto: proxima ? proxima.texto : '',
            proximoAviso
        };
    }

    async function cargarConfiguracion(url) {
        if (cacheConfig) return cacheConfig;
        if (!promesaCarga) {
            const targetUrl = url || DEFAULT_URL;
            promesaCarga = fetch(targetUrl, { cache: 'no-cache' })
                .then(res => (res.ok ? res.json() : CONFIG_DEFECTO))
                .then(data => {
                    cacheConfig = (data && typeof data === 'object') ? data : CONFIG_DEFECTO;
                    return cacheConfig;
                })
                .catch(() => {
                    cacheConfig = CONFIG_DEFECTO;
                    return cacheConfig;
                });
        }
        return promesaCarga;
    }

    async function aplicarEnPagina(opciones = {}) {
        const cfg = await cargarConfiguracion(opciones.url);
        const info = evaluarEstado(opciones.fecha, cfg);

        const dot = document.getElementById('estado-horario-dot');
        const texto = document.getElementById('estado-horario-texto');
        if (dot && texto) {
            if (info.abierta) {
                dot.className = 'w-2 h-2 rounded-full bg-emerald-400 animate-pulse flex-shrink-0';
            } else if (info.estado === 'cerrada_festivo' || info.estado === 'cerrada_excepcion' || info.estado === 'cerrada_periodo') {
                dot.className = 'w-2 h-2 rounded-full bg-rose-400 flex-shrink-0';
            } else {
                dot.className = 'w-2 h-2 rounded-full bg-amber-400 flex-shrink-0';
            }
            texto.textContent = info.textoBarra;
            if (texto.parentElement) {
                texto.parentElement.setAttribute('title', info.tituloTooltip);
            }
        }

        const footerVigente = document.getElementById('horario-footer-vigente');
        if (footerVigente) {
            footerVigente.textContent = info.resumenHoy;
        }

        const footerProximo = document.getElementById('horario-footer-proximo');
        if (footerProximo) {
            const nota = !info.abierta && info.proximaAperturaTexto
                ? info.proximaAperturaTexto
                : info.proximoAviso;
            if (nota) {
                footerProximo.textContent = nota;
                footerProximo.classList.remove('hidden');
            } else {
                footerProximo.textContent = '';
                footerProximo.classList.add('hidden');
            }
        }

        return info;
    }

    global.BUOHorarios = {
        cargarConfiguracion,
        evaluarDia,
        evaluarEstado,
        buscarProximaApertura,
        buscarProximoAviso,
        aplicarEnPagina,
        CONFIG_DEFECTO
    };
})(window);
