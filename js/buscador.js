/**
 * Motor compartido de carga de catálogo y búsqueda secuencial en 4 niveles.
 */
(function (global) {
    'use strict';

    const cacheCatalogo = new Map();

    function normalizarRegistroLibro(item) {
        const { limpiarTexto } = global.BUOUtils;
        let autor, titulo, fecha, signatura;
        if (Array.isArray(item)) {
            autor = String(item[0] || '').trim();
            titulo = String(item[1] || '').trim();
            fecha = String(item[2] || '').trim();
            signatura = String(item[3] || '').trim();
        } else {
            autor = String(item.autor || item.Autor || item.AUTOR || '').trim();
            titulo = String(item.titulo || item.Titulo || item.TITULO || '').trim();
            fecha = String(item.fecha || item.Fecha || item.FECHA || '').trim();
            signatura = String(item.signatura || item.Signatura || item.SIGNATURA || '').trim();
        }
        return {
            autor,
            titulo,
            fecha,
            signatura,
            _aNorm: limpiarTexto(autor),
            _tNorm: limpiarTexto(titulo),
            _sNorm: limpiarTexto(signatura)
        };
    }

    async function cargarCatalogoLibros(basePath = '') {
        if (cacheCatalogo.has(basePath)) return cacheCatalogo.get(basePath);

        const promesa = (async () => {
            let rutas = ['libros1.json', 'libros2.json', 'libros3.json'];
            try {
                const manRes = await fetch(`${basePath}manifest.json`);
                if (manRes.ok) {
                    const manifest = await manRes.json();
                    if (Array.isArray(manifest.libros) && manifest.libros.length > 0) {
                        rutas = manifest.libros;
                    }
                }
            } catch (e) {}

            const respuestas = await Promise.all(
                rutas.map(ruta =>
                    fetch(`${basePath}${ruta}`)
                        .then(r => (r.ok ? r.json() : []))
                        .catch(() => [])
                )
            );

            const todos = [];
            for (const datos of respuestas) {
                let lista = [];
                if (Array.isArray(datos)) {
                    lista = datos;
                } else if (typeof datos === 'object' && datos !== null) {
                    const arrayProp = Object.values(datos).find(val => Array.isArray(val));
                    if (arrayProp) lista = arrayProp;
                }
                for (const item of lista) {
                    const reg = normalizarRegistroLibro(item);
                    if (!reg.signatura.toUpperCase().startsWith('EXP')) {
                        todos.push(reg);
                    }
                }
            }
            return todos;
        })();

        cacheCatalogo.set(basePath, promesa);
        return promesa;
    }

    function ejecutarAlgoritmoBusqueda(libros, textoIngresado) {
        const { STOP_WORDS, limpiarTexto } = global.BUOUtils;
        const textoLimpioBusqueda = String(textoIngresado || '').trim();
        if (!textoLimpioBusqueda) return { tipo: 'vacio', resultados: [], total: 0 };

        const librosNormalizados = libros.map(item =>
            item && typeof item._tNorm === 'string' ? item : normalizarRegistroLibro(item)
        ).filter(item => !item.signatura.toUpperCase().startsWith('EXP'));

        const queryNorm = limpiarTexto(textoLimpioBusqueda);
        const palabrasQuery = queryNorm.split(/\s+/).filter(p => p.length > 0);
        const keywords = palabrasQuery.filter(p => !STOP_WORDS.has(p));

        let resultados = [];

        // 0. Búsqueda por Signatura
        const resultadosSignatura = librosNormalizados.filter(libro => libro._sNorm.includes(queryNorm));
        if (resultadosSignatura.length > 0 && (textoLimpioBusqueda.toUpperCase().startsWith('HG') || textoLimpioBusqueda.length <= 6)) {
            return {
                tipo: 'signatura',
                terminoUsado: textoLimpioBusqueda,
                resultados: resultadosSignatura,
                total: resultadosSignatura.length
            };
        }

        // NIVEL 1: Coincidencia de frase completa en Título
        resultados = librosNormalizados.filter(libro => libro._tNorm.includes(queryNorm));

        // NIVEL 2: Todas las palabras clave en Título (cualquier orden)
        if (resultados.length === 0 && keywords.length > 0) {
            resultados = librosNormalizados.filter(libro => keywords.every(kw => libro._tNorm.includes(kw)));
        }

        // NIVEL 3: Primera palabra en Autor y siguientes en Título
        if (resultados.length === 0 && keywords.length > 0) {
            const primeraPalabra = keywords[0];
            const resultadosAutor = librosNormalizados.filter(libro => libro._aNorm.includes(primeraPalabra));
            if (resultadosAutor.length > 0 && keywords.length > 1) {
                const palabrasTitulo = keywords.slice(1, 3);
                resultados = resultadosAutor.filter(libro => palabrasTitulo.every(kw => libro._tNorm.includes(kw)));
            } else {
                resultados = resultadosAutor;
            }
        }

        // NIVEL 4: Última palabra en Autor y dos primeras en Título
        if (resultados.length === 0 && keywords.length > 1) {
            const ultimaPalabra = keywords[keywords.length - 1];
            const dosPrimerasPalabras = keywords.slice(0, 2);
            const resultadosAutor = librosNormalizados.filter(libro => libro._aNorm.includes(ultimaPalabra));
            if (resultadosAutor.length > 0) {
                resultados = resultadosAutor.filter(libro => dosPrimerasPalabras.every(kw => libro._tNorm.includes(kw)));
            }
        }

        resultados.sort((a, b) => a.titulo.localeCompare(b.titulo, 'es'));

        const vistos = new Set();
        const resultadosUnicos = [];
        for (const item of resultados) {
            const idUnico = item.signatura + '|' + item.titulo;
            if (!vistos.has(idUnico)) {
                vistos.add(idUnico);
                resultadosUnicos.push(item);
            }
        }

        return {
            tipo: 'titulo_autor',
            terminoUsado: textoLimpioBusqueda,
            resultados: resultadosUnicos,
            total: resultadosUnicos.length
        };
    }

    global.BUOBuscador = {
        normalizarRegistroLibro,
        cargarCatalogoLibros,
        ejecutarAlgoritmoBusqueda
    };
})(window);
