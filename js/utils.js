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

    global.BUOUtils = {
        STOP_WORDS,
        escapeHTML,
        limpiarTexto,
        normalizarPalabras,
        sanitizarHTML
    };
})(window);
