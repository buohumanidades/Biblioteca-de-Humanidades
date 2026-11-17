/**
 * ============================================================================
 * GOOGLE APPS SCRIPT — ASISTENTE BIBLIOGRÁFICO RAG + APRENDIZAJE CONTINUO
 * Biblioteca de Humanidades · Universidad de Oviedo
 * ============================================================================
 * Instrucciones rápidas:
 * 1. Abre tu proyecto de Google Apps Script conectado a tu hoja de cálculo.
 * 2. Configura las propiedades del script (Configuración del proyecto -> Propiedades
 *    del script):
 *      - GEMINI_API_KEY (recomendado, gratis/bajo coste en Google AI Studio)
 *        o GROQ_API_KEY (si usas Groq; usa modelo vigente como llama-3.3-70b-versatile).
 *      - SPREADSHEET_ID (opcional; si el script está vinculado al Sheet, usa getActiveSpreadsheet).
 * 3. Crea (o deja que el script cree automáticamente) las pestañas:
 *      - "Feedback" (Fecha | Accion | Keywords | Titulo | Signatura | Delta)
 *      - "Lagunas"  (Fecha | Consulta | Keywords)
 *      - "Sinonimos" (Palabra | Sinonimos_separados_por_coma)
 * 4. Publica como Aplicación Web ("Ejecutar como: Yo", "Quién tiene acceso: Cualquier usuario").
 */

const CONFIG = {
  // Cambia a 'groq' si prefieres Groq en lugar de Gemini
  PROVIDER: 'gemini',
  GEMINI_MODEL: 'gemini-2.5-flash',
  GROQ_MODEL: 'llama-3.3-70b-versatile',
  CACHE_TTL_SECONDS: 600
};

function getSpreadsheet_() {
  const props = PropertiesService.getScriptProperties();
  const id = props.getProperty('SPREADSHEET_ID');
  return id ? SpreadsheetApp.openById(id) : SpreadsheetApp.getActiveSpreadsheet();
}

function getOrCreateSheet_(name, headers) {
  const ss = getSpreadsheet_();
  let sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
    if (headers && headers.length) sheet.appendRow(headers);
  }
  return sheet;
}

function jsonResponse_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * GET ?accion=aprendizaje_leer
 * Devuelve los pesos colectivos calculados a partir de "Feedback" y los sinónimos de "Sinonimos".
 */
function doGet(e) {
  const accion = (e && e.parameter && e.parameter.accion) || '';
  if (accion !== 'aprendizaje_leer') {
    return jsonResponse_({ ok: true, servicio: 'BUO Asistente RAG' });
  }

  const cache = CacheService.getScriptCache();
  const cached = cache.get('aprendizaje_v1');
  if (cached) {
    return ContentService.createTextOutput(cached).setMimeType(ContentService.MimeType.JSON);
  }

  const resultado = { pesos: [], sinonimos: {} };

  try {
    const ss = getSpreadsheet_();
    const fbSheet = ss.getSheetByName('Feedback');
    if (fbSheet && fbSheet.getLastRow() > 1) {
      const rows = fbSheet.getRange(2, 1, fbSheet.getLastRow() - 1, 6).getValues();
      const mapa = {};
      rows.forEach(r => {
        const kws = String(r[2] || '').split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
        const titNorm = String(r[3] || '').trim().toLowerCase();
        const delta = Number(r[5]) || 0;
        if (!titNorm || !delta) return;
        kws.forEach(kw => {
          const clave = kw + '|' + titNorm;
          mapa[clave] = (mapa[clave] || 0) + delta;
        });
      });
      resultado.pesos = Object.keys(mapa)
        .map(clave => ({ clave, score: Math.max(-10, Math.min(20, mapa[clave])) }))
        .sort((a, b) => Math.abs(b.score) - Math.abs(a.score))
        .slice(0, 300);
    }

    const synSheet = ss.getSheetByName('Sinonimos');
    if (synSheet && synSheet.getLastRow() > 1) {
      const rows = synSheet.getRange(2, 1, synSheet.getLastRow() - 1, 2).getValues();
      rows.forEach(r => {
        const pal = String(r[0] || '').trim().toLowerCase();
        const syns = String(r[1] || '').split(',').map(s => s.trim()).filter(Boolean);
        if (pal && syns.length) resultado.sinonimos[pal] = syns;
      });
    }
  } catch (err) {
    // Si no hay hoja configurada, devolver estructura vacía sin fallar
  }

  const serialized = JSON.stringify(resultado);
  cache.put('aprendizaje_v1', serialized, CONFIG.CACHE_TTL_SECONDS);
  return ContentService.createTextOutput(serialized).setMimeType(ContentService.MimeType.JSON);
}

/**
 * POST:
 * - Si body.tipoEvento === 'aprendizaje', registra la señal en Google Sheets.
 * - Si es una consulta conversacional, aplica RAG sobre body.candidatosCatalogo.
 */
function doPost(e) {
  try {
    const body = JSON.parse((e && e.postData && e.postData.contents) || '{}');

    if (body.tipoEvento === 'aprendizaje') {
      return manejarEventoAprendizaje_(body);
    }

    return manejarConsultaRAG_(body);
  } catch (err) {
    return jsonResponse_({ texto: '', libros: [], error: String(err) });
  }
}

function manejarEventoAprendizaje_(body) {
  const fecha = new Date();
  if (body.accion === 'sin_resultados') {
    const sheet = getOrCreateSheet_('Lagunas', ['Fecha', 'Consulta', 'Keywords']);
    sheet.appendRow([fecha, body.consulta || '', (body.keywords || []).join(', ')]);
  } else {
    const sheet = getOrCreateSheet_('Feedback', ['Fecha', 'Accion', 'Keywords', 'TituloNorm', 'Signatura', 'Delta']);
    sheet.appendRow([
      fecha,
      body.accion || 'clic',
      (body.keywords || []).join(', '),
      body.tituloNorm || body.titulo || '',
      body.signatura || '',
      Number(body.delta) || 1
    ]);
    CacheService.getScriptCache().remove('aprendizaje_v1');
  }
  return jsonResponse_({ ok: true });
}

function manejarConsultaRAG_(body) {
  const mensaje = String(body.mensaje || '').trim();
  const historial = Array.isArray(body.historial) ? body.historial : [];
  const candidatos = Array.isArray(body.candidatosCatalogo) ? body.candidatosCatalogo.slice(0, 15) : [];

  const listaReal = candidatos.map((c, i) =>
    `${i + 1}. "${c.titulo}" — ${c.autor} (${c.fecha || 's.f.'}) [Sig: ${(c.signaturas || []).join(' / ')}]`
  ).join('\n');

  const promptSistema = [
    'Eres un bibliotecario académico experto de la Biblioteca de Humanidades (Universidad de Oviedo).',
    'Ayudas a estudiantes (TFG, TFM, trabajos) y profesores.',
    'REGLAS ESTRICTAS:',
    '1. No inventes nunca signaturas ni libros diciendo que están en la biblioteca si no están en CANDIDATOS_CATALOGO.',
    '2. Explica brevemente en español cómo abordar la búsqueda o qué libros reales de CANDIDATOS_CATALOGO destacan (introductorios, fuentes, monografías).',
    '3. Propón entre 2 y 4 términos sinónimos o keywords útiles para ampliar la búsqueda si hace falta.',
    '4. Responde SIEMPRE en JSON válido con esta forma exacta:',
    '{"texto":"explicación breve en markdown","sinonimos":["termino1","termino2"],"keywords_sugeridas":["kw1","kw2"],"libros":[]}'
  ].join('\n');

  const promptUsuario = [
    `HISTORIAL RECIENTE: ${JSON.stringify(historial.slice(-4))}`,
    `CONSULTA DEL USUARIO: ${mensaje}`,
    `CANDIDATOS_CATALOGO:\n${listaReal || '(Sin candidatos locales directos)'}`
  ].join('\n\n');

  const respuestaRaw = llamarLLM_(promptSistema, promptUsuario);
  const parsed = parsearJSONSeguro_(respuestaRaw);
  return jsonResponse_(parsed);
}

function llamarLLM_(systemPrompt, userPrompt) {
  const props = PropertiesService.getScriptProperties();

  if (CONFIG.PROVIDER === 'groq') {
    const key = props.getProperty('GROQ_API_KEY');
    if (!key) throw new Error('Falta GROQ_API_KEY');
    const res = UrlFetchApp.fetch('https://api.groq.com/openai/v1/chat/completions', {
      method: 'post',
      contentType: 'application/json',
      headers: { Authorization: 'Bearer ' + key },
      payload: JSON.stringify({
        model: CONFIG.GROQ_MODEL,
        temperature: 0.3,
        response_format: { type: 'json_object' },
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userPrompt }
        ]
      })
    });
    const data = JSON.parse(res.getContentText());
    return data.choices[0].message.content;
  }

  const apiKey = props.getProperty('GEMINI_API_KEY');
  if (!apiKey) throw new Error('Falta GEMINI_API_KEY');
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${CONFIG.GEMINI_MODEL}:generateContent?key=${encodeURIComponent(apiKey)}`;
  const res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({
      systemInstruction: { parts: [{ text: systemPrompt }] },
      contents: [{ role: 'user', parts: [{ text: userPrompt }] }],
      generationConfig: {
        temperature: 0.3,
        responseMimeType: 'application/json'
      }
    })
  });
  const data = JSON.parse(res.getContentText());
  return data.candidates[0].content.parts[0].text;
}

function parsearJSONSeguro_(texto) {
  try {
    const limpio = String(texto || '').replace(/^```json\s*/i, '').replace(/```\s*$/i, '').trim();
    const obj = JSON.parse(limpio);
    return {
      texto: typeof obj.texto === 'string' ? obj.texto : '',
      sinonimos: Array.isArray(obj.sinonimos) ? obj.sinonimos : [],
      keywords_sugeridas: Array.isArray(obj.keywords_sugeridas) ? obj.keywords_sugeridas : [],
      libros: Array.isArray(obj.libros) ? obj.libros : []
    };
  } catch (e) {
    return { texto: String(texto || ''), sinonimos: [], keywords_sugeridas: [], libros: [] };
  }
}
