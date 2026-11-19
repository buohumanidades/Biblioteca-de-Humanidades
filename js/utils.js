/**
 * Utilidades compartidas de normalización, escape HTML y sanitización.
 */
(function (global) {
    'use strict';

    const STOP_WORDS = new Set([
        'el', 'la', 'los', 'las', 'un', 'una', 'unos', 'unas',
        'a', 'ante', 'bajo', 'cabe', 'con', 'contra', 'de', 'del', 'desde',
        'durante', 'en', 'entre', 'hacia', 'hasta', 'mediante', 'para',
        'por', 'segun', 'sin', 'so', 'sobre', 'tras', 'versus', 'via',
        'y', 'e', 'o', 'u', 'que', 'al'
    ]);

    function escapeHTML(str) {
        return String(str ?? '').replace(/[&<>'"]/g, tag => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            "'": '&#39;',
            '"': '&quot;'
        }[tag]));
    }

    function limpiarTexto(str) {
        if (!str) return '';
        return str.toString()
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .replace(/[^a-zA-Z0-9\s]/g, ' ')
            .replace(/\s+/g, ' ')
            .trim()
            .toLowerCase();
    }

    function normalizarPalabras(texto) {
        if (!texto) return [];
        let limpio = String(texto).replace(/<[^>]*>?/gm, '');
        limpio = limpio.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
        limpio = limpio.replace(/[^a-z0-9\s]/g, ' ');
        return limpio.split(' ').filter(p => p.trim().length > 0);
    }

    function sanitizarHTML(html) {
        const raw = String(html ?? '');
        if (global.DOMPurify && typeof global.DOMPurify.sanitize === 'function') {
            return global.DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
        }
        // Fallback conservador si DOMPurify no estuviera disponible
        const temp = document.createElement('div');
        temp.textContent = raw;
        return temp.innerHTML;
    }

    function construirUrlOpac(titulo, signatura) {
        const sigLimpia = String(signatura || '').trim();
        const sigUpper = sigLimpia.toUpperCase();
        let queryParaOpac = sigLimpia;

        if (!sigUpper || sigUpper === 'S/S' || sigUpper.startsWith('HG') || sigLimpia.includes('No disponible')) {
            const base = String(titulo || '')
                .replace(/ñ/g, '__ENYE__').replace(/Ñ/g, '__ENYEMAY__')
                .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
                .replace(/__ENYE__/g, 'ñ').replace(/__ENYEMAY__/g, 'Ñ')
                .replace(/[^a-zA-Z0-9\sñÑ]/g, ' ');
            const stopRegex = /\b(el|la|los|las|un|una|unos|unas|a|ante|bajo|cabe|con|contra|de|desde|durante|en|entre|hacia|hasta|mediante|para|por|segun|sin|so|sobre|tras|y|e|ni|que|o|u|al|del|su|sus|lo|como|mas|pero)\b/gi;
            queryParaOpac = base.replace(stopRegex, ' ').replace(/\s+/g, ' ').trim();
            if (!queryParaOpac) {
                queryParaOpac = String(titulo || sigLimpia || '').trim();
            }
        }
        return 'https://buoabsys.cpd.uniovi.es/cgi-bin/abnetopac?ACC=DOSEARCH&xsqf99=' + encodeURIComponent(queryParaOpac);
    }

    async function copiarTexto(texto) {
        const valor = String(texto ?? '').trim();
        if (!valor) return false;
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            try {
                await navigator.clipboard.writeText(valor);
                return true;
            } catch (e) {}
        }
        try {
            const ta = document.createElement('textarea');
            ta.value = valor;
            ta.setAttribute('readonly', '');
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            const ok = document.execCommand('copy');
            document.body.removeChild(ta);
            return Boolean(ok);
        } catch (e) {
            return false;
        }
    }

    global.BUOUtils = {
        STOP_WORDS,
        escapeHTML,
        limpiarTexto,
        normalizarPalabras,
        sanitizarHTML,
        construirUrlOpac,
        copiarTexto
    };
})(window);
