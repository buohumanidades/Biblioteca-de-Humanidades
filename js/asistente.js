/**
 * Motor inteligente de asistencia bibliográfica e investigación para Humanidades.
 * Actúa como un bibliotecario experto sobre el catálogo real (libros1..3.json,
 * todassignaturas.json, Grados/grado3.json):
 * - Limpia símbolos especiales, artículos, preposiciones y relleno conversacional.
 * - Calcula especificidad IDF y detecta apellidos/nombres propios reales del catálogo.
 * - Busca por palabra completa y variantes morfológicas o sinónimos (sin doble conteo).
 * - Ordena priorizando mayor cobertura de palabras clave, apellidos/nombres propios,
 *   coincidencias en título, especificidad y acceso en sala.
 */
(function (global) {
    'use strict';

    const STOPWORDS_ESPANOL = new Set([
        // Artículos y contracciones
        'el', 'la', 'los', 'las', 'un', 'una', 'unos', 'unas', 'lo', 'al', 'del',
        // Preposiciones
        'a', 'ante', 'bajo', 'cabe', 'con', 'contra', 'de', 'desde', 'durante',
        'en', 'entre', 'hacia', 'hasta', 'mediante', 'para', 'por', 'segun',
        'sin', 'so', 'sobre', 'tras', 'versus', 'via',
        // Conjunciones, pronombres y determinantes
        'y', 'e', 'ni', 'o', 'u', 'que', 'cual', 'cuales', 'quien', 'quienes',
        'cuyo', 'cuya', 'cuyos', 'cuyas', 'su', 'sus', 'mi', 'mis', 'tu', 'tus',
        'nuestro', 'nuestra', 'nuestros', 'nuestras', 'vuestro', 'vuestra',
        'se', 'le', 'les', 'me', 'te', 'nos', 'os', 'este', 'esta', 'estos',
        'estas', 'esto', 'ese', 'esa', 'esos', 'esas', 'eso', 'aquel', 'aquella',
        'aquellos', 'aquellas', 'aquello', 'como', 'donde', 'cuando', 'cuanto',
        'muy', 'mas', 'menos', 'pero', 'sino', 'aunque', 'porque', 'pues', 'ya',
        'no', 'si', 'tambien', 'tampoco', 'solo', 'solamente', 'unicamente',
        'todo', 'toda', 'todos', 'todas', 'otro', 'otra', 'otros', 'otras',
        'mismo', 'misma', 'mismos', 'mismas', 'cada', 'algun', 'alguna',
        'algunos', 'algunas', 'algo', 'nada', 'nadie',
        // Relleno conversacional de consulta bibliotecaria
        'hola', 'buenas', 'buenos', 'dias', 'tardes', 'noches', 'favor', 'gracias',
        'necesito', 'quiero', 'queria', 'quisiera', 'busco', 'buscar', 'encontrar',
        'localizar', 'dame', 'recomienda', 'recomiendame', 'recomendacion',
        'recomendaciones', 'sugiere', 'sugiereme', 'ayuda', 'ayudame', 'estoy',
        'haciendo', 'realizando', 'preparando', 'trabajo', 'investigacion',
        'investigar', 'tfg', 'tfm', 'tesis', 'ensayo', 'tema', 'tematica',
        'acerca', 'relacionado', 'relacionados', 'relacionada', 'relacionadas',
        'libro', 'libros', 'obra', 'obras', 'manual', 'manuales', 'texto', 'textos',
        'bibliografia', 'fuente', 'fuentes', 'autor', 'autores', 'puedes', 'podrias',
        'hay', 'teneis', 'tienen', 'biblioteca', 'humanidades', 'catalogo',
        'leer', 'lectura', 'lecturas', 'informacion', 'datos', 'mejor', 'mejores',
        'ver', 'mostrar', 'muestra', 'saber', 'conocer'
    ]);

    const PALABRAS_INSTITUCIONALES = new Set([
        'congreso', 'asociacion', 'universidad', 'instituto', 'fundacion',
        'ministerio', 'centro', 'academia', 'departamento', 'colegio', 'sociedad',
        'seminario', 'jornadas', 'coloquio', 'simposio', 'museo', 'ayuntamiento',
        'gobierno', 'biblioteca', 'grupo', 'xunta', 'consejeria', 'servicio',
        'diputacion', 'patronato', 'comision', 'direccion', 'editorial', 'ediciones'
    ]);

    const NO_APELLIDOS = new Set([
        ...PALABRAS_INSTITUCIONALES,
        'espanola', 'espanol', 'espana', 'asturiano', 'asturiana', 'asturias',
        'clasico', 'clasica', 'clasicos', 'clasicas', 'historia', 'historico',
        'historica', 'arte', 'artistico', 'artistica', 'filosofia', 'filosofico',
        'filosofica', 'literatura', 'literario', 'literaria', 'musica', 'musical',
        'geografia', 'geografico', 'medieval', 'moderna', 'moderno',
        'contemporanea', 'contemporaneo', 'antigua', 'antiguo', 'general',
        'nacional', 'internacional', 'estudios', 'estudio', 'politica', 'politico',
        'social', 'cultural', 'cultura', 'mujer', 'mujeres', 'transicion',
        'mitologia', 'griega', 'griego', 'grecia', 'roma', 'romano', 'romana',
        'siglo', 'oro', 'guerra', 'civil', 'republica', 'franquismo', 'derecho',
        'derechos', 'etica', 'moral', 'ciencia', 'ciencias', 'tecnologia',
        'lengua', 'lenguas', 'linguistica', 'traduccion', 'poesia', 'novela',
        'teatro', 'cine', 'fotografia', 'arquitectura', 'escultura', 'pintura',
        'patrimonio', 'religion', 'religiones', 'feminismo', 'genero', 'igualdad',
        'democracia', 'educacion', 'economia', 'sociedad', 'paisaje', 'territorio'
    ]);

    /**
     * Diccionario de variantes morfológicas y sinónimos por palabra clave.
     * En tiempo de búsqueda se filtran aquellos que ya existan como keyword del usuario
     * para evitar doble conteo.
     */
    const SINONIMOS_PALABRA = {
        mujer: ['mujeres', 'femenina', 'femenino', 'feminismo', 'feminista', 'genero'],
        mujeres: ['mujer', 'femenina', 'femenino', 'feminismo', 'feminista', 'genero'],
        feminismo: ['feminista', 'mujer', 'mujeres', 'genero', 'sufragismo', 'igualdad'],
        feminista: ['feminismo', 'mujer', 'mujeres', 'genero'],
        genero: ['feminismo', 'mujer', 'mujeres', 'igualdad'],
        transicion: ['democracia', 'posfranquismo', 'constitucion'],
        franquismo: ['franquista', 'dictadura', 'posguerra'],
        republica: ['republicano', 'republicana'],
        espana: ['espanol', 'espanola', 'espanoles', 'hispanico', 'hispanica'],
        espanol: ['espana', 'espanola', 'espanoles', 'hispanico'],
        espanola: ['espana', 'espanol', 'espanolas', 'hispanica'],
        prerromanico: ['prerromanica', 'monarquia asturiana', 'naranco', 'ramirense', 'camara santa'],
        prerromanica: ['prerromanico', 'monarquia asturiana', 'naranco'],
        asturias: ['asturiano', 'asturiana', 'asturianos', 'oviedo', 'astur'],
        asturiano: ['asturiana', 'asturias', 'oviedo', 'astur'],
        asturiana: ['asturiano', 'asturias', 'oviedo', 'astur'],
        romanico: ['romanica', 'edad media', 'medieval'],
        gotico: ['gotica', 'catedral', 'edad media'],
        renacimiento: ['renacentista', 'humanismo', 'quattrocento'],
        barroco: ['barroca', 'contrarreforma'],
        arte: ['artistico', 'artistica', 'artisticas', 'artisticos', 'bellas artes'],
        arquitectura: ['arquitectonico', 'arquitectonica', 'edificios', 'monumentos'],
        pintura: ['pintores', 'pictorico', 'pictorica', 'lienzo'],
        escultura: ['escultores', 'escultorico', 'imaginería'],
        cine: ['cinematografia', 'cinematografico', 'film', 'peliculas'],
        fotografia: ['fotografico', 'fotografica', 'fotografos'],
        filosofia: ['filosofico', 'filosofica', 'pensamiento', 'metafisica'],
        politica: ['politico', 'politicos', 'politicas', 'estado', 'poder', 'ciudadania'],
        etica: ['etico', 'etica', 'moral', 'valores', 'bioetica'],
        moral: ['etica', 'etico', 'virtud'],
        totalitarismo: ['totalitario', 'totalitarios', 'autoritarismo', 'fascismo', 'nazismo'],
        ciencia: ['cientifico', 'cientifica', 'epistemologia', 'metodo'],
        logica: ['logico', 'argumentacion', 'razonamiento'],
        ia: ['inteligencia artificial', 'algoritmos', 'robotica', 'digital'],
        artificial: ['inteligencia artificial', 'computacion', 'digital'],
        mitologia: ['mito', 'mitos', 'mitologico', 'mitologica', 'leyenda', 'leyendas'],
        mito: ['mitologia', 'mitos', 'mitologico'],
        dioses: ['dios', 'olimpicos', 'divinidad', 'panteon'],
        grecia: ['griego', 'griega', 'griegos', 'griegas', 'helenico', 'atenas'],
        griega: ['griego', 'griegos', 'griegas', 'grecia', 'helenico', 'helenica'],
        griego: ['griega', 'griegos', 'grecia', 'helenico'],
        roma: ['romano', 'romana', 'romanos', 'imperio romano', 'latino'],
        romano: ['roma', 'romana', 'romanos', 'latino'],
        clasicos: ['clasico', 'clasica', 'clasicas', 'grecolatino', 'antiguedad'],
        clasico: ['clasicos', 'clasica', 'grecolatino', 'antiguedad'],
        clasica: ['clasico', 'clasicos', 'grecolatino', 'antiguedad'],
        medieval: ['edad media', 'feudalismo', 'medievo'],
        historia: ['historico', 'historica', 'historiografia', 'pasado'],
        literatura: ['literario', 'literaria', 'letras', 'escritores'],
        poesia: ['poetico', 'poetica', 'poema', 'poemas', 'lirica', 'verso'],
        novela: ['narrativa', 'novelistica', 'ficcion'],
        teatro: ['dramaturgia', 'dramatico', 'escena', 'comedia'],
        quijote: ['cervantino', 'cervantina'],
        oro: ['barroco', 'renacimiento', 'aurea'],
        linguistica: ['lenguaje', 'filologia', 'gramatica', 'idioma'],
        traduccion: ['traductologia', 'interpretar', 'version'],
        geografia: ['geografico', 'territorio', 'espacio', 'cartografia'],
        territorio: ['geografia', 'ordenacion', 'urbanismo', 'comarca'],
        paisaje: ['entorno', 'medio', 'territorio'],
        musica: ['musical', 'musicologia', 'sonido'],
        opera: ['lirico', 'canto', 'libreto'],
        metodologia: ['metodo', 'metodos', 'tecnicas', 'investigacion'],
        bibliografia: ['fuentes', 'referencias', 'repertorio']
    };

    /**
     * Clústeres temáticos para sugerir CDUs, autores de referencia,
     * palabras clave ES/EN y enlaces académicos al usuario.
     */
    const CONCEPTOS_HUMANIDADES = [
        {
            id: 'arte_asturias',
            label: 'Arte y Patrimonio en Asturias',
            triggers: ['arte asturiano', 'prerromanico', 'prerrománico', 'naranco', 'lillo', 'camara santa', 'arte en asturias', 'patrimonio asturiano', 'arquitectura asturiana'],
            sinonimos: ['prerrománico asturiano', 'arte medieval en Asturias', 'arquitectura en Asturias', 'patrimonio artístico asturiano'],
            autores: ['Bango Torviso', 'Arias Páramo', 'García de Castro', 'García Cuetos', 'Cid Priego'],
            cdus: ['HG7.033', 'HG72', 'HG7.03'],
            en: ['Asturian pre-Romanesque art', 'Asturias medieval architecture']
        },
        {
            id: 'historia_asturias',
            label: 'Historia y Cultura de Asturias',
            triggers: ['asturias', 'oviedo', 'gijon', 'aviles', 'asturiano', 'asturiana', 'astur'],
            sinonimos: ['reino de Asturias', 'historia contemporánea de Asturias', 'cultura tradicional asturiana'],
            autores: ['Uría', 'Ruiz de la Peña', 'Girón', 'Tuñón'],
            cdus: ['HG946.12', 'HG946', 'HG39'],
            en: ['History of Asturias', 'Kingdom of Asturias']
        },
        {
            id: 'arte_medieval',
            label: 'Arte Medieval (Románico y Gótico)',
            triggers: ['romanico', 'románico', 'gotico', 'gótico', 'arte medieval', 'catedral', 'iconografia medieval', 'bizantino'],
            sinonimos: ['iconografía medieval', 'arquitectura románica', 'escultura gótica', 'arte bizantino'],
            autores: ['Duby', 'Panofsky', 'Bango Torviso', 'Yarza', 'Mâle'],
            cdus: ['HG7.033', 'HG726', 'HG7.04'],
            en: ['Romanesque art', 'Gothic architecture', 'medieval iconography']
        },
        {
            id: 'renacimiento_barroco',
            label: 'Renacimiento y Barroco',
            triggers: ['renacimiento', 'barroco', 'manierismo', 'quattrocento', 'velazquez', 'goya', 'miguel angel', 'leonardo'],
            sinonimos: ['arte del Renacimiento', 'pintura barroca', 'Siglo de Oro artístico'],
            autores: ['Panofsky', 'Gombrich', 'Burke', 'Checa', 'Marías', 'Wittkower'],
            cdus: ['HG7.034', 'HG75'],
            en: ['Renaissance art', 'Baroque painting', 'iconology']
        },
        {
            id: 'arte_contemporaneo',
            label: 'Arte Contemporáneo, Vanguardias y Cine',
            triggers: ['arte contemporaneo', 'vanguardias', 'surrealismo', 'cubismo', 'pintura moderna', 'cine', 'fotografia'],
            sinonimos: ['vanguardias artísticas', 'teoría del cine', 'historia de la fotografía', 'arte del siglo XX'],
            autores: ['Bozal', 'Calvo Serraller', 'Danto', 'Benjamin', 'Sontag', 'Bazin'],
            cdus: ['HG7.036', 'HG791', 'HG77'],
            en: ['contemporary art', 'avant-garde', 'film studies']
        },
        {
            id: 'filosofia_antigua',
            label: 'Filosofía Antigua',
            triggers: ['filosofia antigua', 'platon', 'platón', 'aristoteles', 'aristóteles', 'socrates', 'sócrates', 'presocraticos', 'estoicismo', 'epicureismo'],
            sinonimos: ['pensamiento griego clásico', 'metafísica de Aristóteles', 'diálogos platónicos'],
            autores: ['Guthrie', 'Reale', 'Jaeger', 'Hadot', 'Lledó', 'García Gual'],
            cdus: ['HG1/09', 'HG1'],
            en: ['Ancient Greek philosophy', 'Plato', 'Aristotle']
        },
        {
            id: 'filosofia_moderna',
            label: 'Filosofía Moderna e Ilustración',
            triggers: ['filosofia moderna', 'ilustracion', 'ilustración', 'descartes', 'kant', 'hume', 'spinoza', 'leibniz', 'locke', 'rousseau', 'racionalismo', 'empirismo'],
            sinonimos: ['crítica kantiana', 'Ilustración europea', 'racionalismo cartesiano', 'teoría del conocimiento'],
            autores: ['Cassirer', 'Hazard', 'Copleston', 'Deleuze', 'Villacañas'],
            cdus: ['HG1', 'HG165'],
            en: ['Early modern philosophy', 'Enlightenment', 'Kant epistemology']
        },
        {
            id: 'filosofia_contemporanea',
            label: 'Filosofía Contemporánea y Hermenéutica',
            triggers: ['existencialismo', 'fenomenologia', 'fenomenología', 'hermeneutica', 'hermenéutica', 'heidegger', 'sartre', 'nietzsche', 'foucault', 'derrida', 'habermas', 'gadamer', 'ortega'],
            sinonimos: ['escuela de Frankfurt', 'postestructuralismo', 'ontología contemporánea'],
            autores: ['Habermas', 'Gadamer', 'Ricoeur', 'Sartre', 'Foucault', 'Ortega y Gasset'],
            cdus: ['HG1', 'HG165'],
            en: ['Continental philosophy', 'phenomenology', 'hermeneutics']
        },
        {
            id: 'etica_politica',
            label: 'Ética, Bioética y Filosofía Política',
            triggers: ['etica', 'ética', 'moral', 'bioetica', 'filosofia politica', 'justicia', 'democracia', 'arendt', 'rawls', 'ciudadania', 'derechos humanos', 'totalitarismo'],
            sinonimos: ['teoría de la justicia', 'filosofía moral', 'pensamiento político contemporáneo'],
            autores: ['Hannah Arendt', 'John Rawls', 'Adela Cortina', 'Camps', 'Nussbaum', 'Habermas'],
            cdus: ['HG17', 'HG321', 'HG32'],
            en: ['political philosophy', 'moral philosophy', 'ethics', 'justice']
        },
        {
            id: 'ciencia_ia',
            label: 'Lógica, Ciencia y Tecnología / IA',
            triggers: ['inteligencia artificial', 'tecnologia', 'tecnología', 'epistemologia', 'epistemología', 'logica', 'lógica', 'algoritmo', 'mente'],
            sinonimos: ['filosofía de la ciencia', 'filosofía de la mente', 'ética de la tecnología'],
            autores: ['Popper', 'Kuhn', 'Chalmers', 'Bunge', 'Searle', 'Dennett', 'Floridi'],
            cdus: ['HG16', 'HG165', 'HG167', 'HG001'],
            en: ['philosophy of science', 'philosophy of mind', 'ethics of AI']
        },
        {
            id: 'genero_feminismo',
            label: 'Estudios de Género y Feminismo',
            triggers: ['feminismo', 'feminista', 'mujer', 'mujeres', 'genero', 'género', 'igualdad', 'sufragismo', 'patriarcado', 'beauvoir', 'butler'],
            sinonimos: ['historia de las mujeres', 'teoría feminista', 'estudios de género', 'movimiento sufragista'],
            autores: ['Simone de Beauvoir', 'Amelia Valcárcel', 'Celia Amorós', 'Judith Butler', 'Mary Nash', 'Perrot'],
            cdus: ['HG396', 'HG305'],
            en: ['feminist theory', 'gender studies', 'women history']
        },
        {
            id: 'historia_antigua',
            label: 'Historia Antigua y Mundo Clásico',
            triggers: ['grecia', 'roma', 'antiguedad', 'antigüedad', 'egipto', 'mesopotamia', 'imperio romano', 'atenas', 'arqueologia', 'arqueología', 'griega', 'griego'],
            sinonimos: ['mundo clásico grecorromano', 'República e Imperio romano', 'polis griega', 'arqueología clásica'],
            autores: ['Finley', 'Beard', 'Rostovtzeff', 'Bravo', 'Vernant', 'Mangas'],
            cdus: ['HG937', 'HG938', 'HG932', 'HG902'],
            en: ['Ancient Greece', 'Roman Empire', 'classical antiquity']
        },
        {
            id: 'historia_medieval',
            label: 'Historia Medieval',
            triggers: ['edad media', 'medieval', 'feudalismo', 'alandalus', 'al-andalus', 'reconquista', 'cruzadas', 'reinos cristianos'],
            sinonimos: ['sociedad feudal', 'Al-Ándalus', 'monarquía medieval hispánica'],
            autores: ['Le Goff', 'Duby', 'Bloch', 'Valdeón', 'García de Cortázar', 'Ladero Quesada'],
            cdus: ['HG940', 'HG946', 'HI9'],
            en: ['Middle Ages', 'feudalism', 'medieval Iberia']
        },
        {
            id: 'historia_espana_contemporanea',
            label: 'Historia Contemporánea de España',
            triggers: ['transicion', 'transición', 'franquismo', 'guerra civil', 'segunda republica', 'república', 'restauracion', 'espana contemporanea'],
            sinonimos: ['Segunda República española', 'Guerra Civil y exilio', 'dictadura franquista', 'transición a la democracia'],
            autores: ['Juliá', 'Preston', 'Fusi', 'Tusell', 'Payne', 'Casanova', 'Moradiellos'],
            cdus: ['HG946', 'HG946G', 'HG32/46'],
            en: ['Spanish Civil War', 'Francoism', 'Spanish transition to democracy']
        },
        {
            id: 'historia_contemporanea_universal',
            label: 'Historia Contemporánea Universal',
            triggers: ['guerra mundial', 'guerra fria', 'guerra fría', 'revolucion industrial', 'revolucion francesa', 'fascismo', 'nazismo', 'comunismo', 'union europea', 'holocausto'],
            sinonimos: ['siglo XX europeo', 'relaciones internacionales', 'totalitarismos del siglo XX'],
            autores: ['Hobsbawm', 'Judt', 'Kershaw', 'Mazower', 'Arostegui'],
            cdus: ['HG94', 'HG940', 'HG327'],
            en: ['20th century Europe', 'Cold War', 'World War']
        },
        {
            id: 'mitologia_religiones',
            label: 'Mitología, Religiones e Iconografía',
            triggers: ['mitologia', 'mitología', 'mito', 'mitos', 'religion', 'religión', 'iconografia', 'dioses', 'homero'],
            sinonimos: ['mitología grecorromana', 'historia de las religiones', 'simbología e iconografía', 'mitos clásicos'],
            autores: ['Mircea Eliade', 'Jean-Pierre Vernant', 'Robert Graves', 'Carlos García Gual', 'Alberto Bernabé'],
            cdus: ['HG29', 'HG292', 'HG2', 'HG7.04'],
            en: ['classical mythology', 'comparative religion', 'iconography']
        },
        {
            id: 'literatura_espanola',
            label: 'Literatura Española y Siglo de Oro',
            triggers: ['literatura espanola', 'siglo de oro', 'cervantes', 'quijote', 'lorca', 'generacion del 27', 'generacion del 98', 'picaresca', 'calderon', 'lope', 'galdos'],
            sinonimos: ['novela española', 'poesía española', 'teatro del Siglo de Oro', 'crítica literaria'],
            autores: ['Rico', 'Pedraza', 'Lázaro Carreter', 'Blecua', 'Mainer'],
            cdus: ['HG860'],
            en: ['Spanish literature', 'Spanish Golden Age']
        },
        {
            id: 'literatura_hispanoamericana',
            label: 'Literatura Hispanoamericana',
            triggers: ['hispanoamericana', 'latinoamericana', 'borges', 'cortazar', 'garcia marquez', 'realismo magico', 'boom', 'neruda', 'vallejo'],
            sinonimos: ['narrativa hispanoamericana', 'boom latinoamericano', 'poesía hispanoamericana'],
            autores: ['Sainz de Medrano', 'Oviedo', 'Shaw', 'Rama'],
            cdus: ['HG860/7', 'HG860/8'],
            en: ['Latin American literature', 'magical realism']
        },
        {
            id: 'literatura_inglesa_extranjera',
            label: 'Literatura Inglesa, Francesa, Alemana y Clásica',
            triggers: ['literatura inglesa', 'shakespeare', 'joyce', 'woolf', 'literatura francesa', 'proust', 'baudelaire', 'literatura alemana', 'goethe', 'kafka', 'literatura clasica', 'virgilio'],
            sinonimos: ['literatura comparada', 'novela inglesa', 'literatura europea', 'filología clásica'],
            autores: ['Bloom', 'Steiner', 'Auerbach', 'Pujals', 'Bowra'],
            cdus: ['HG820', 'HG840', 'HG830', 'HG87'],
            en: ['English literature', 'comparative literature', 'classical philology']
        },
        {
            id: 'linguistica_traduccion',
            label: 'Lingüística, Filología y Traducción',
            triggers: ['linguistica', 'lingüística', 'gramatica', 'gramática', 'sintaxis', 'semantica', 'semántica', 'pragmatica', 'fonetica', 'traduccion', 'traducción', 'lexicografia', 'bable', 'sociolinguistica'],
            sinonimos: ['análisis del discurso', 'teoría de la traducción', 'lingüística aplicada', 'sociolingüística'],
            autores: ['Chomsky', 'Alarcos', 'Bosque', 'Escandell', 'Hurtado Albir', 'Lapesa'],
            cdus: ['HG80', 'HG801', 'HG806'],
            en: ['linguistics', 'translation studies', 'pragmatics', 'discourse analysis']
        },
        {
            id: 'geografia_territorio',
            label: 'Geografía y Ordenación del Territorio',
            triggers: ['geografia', 'geografía', 'territorio', 'paisaje', 'urbanismo', 'demografia', 'poblacion', 'clima', 'geomorfologia', 'cartografia'],
            sinonimos: ['ordenación del territorio', 'geografía humana', 'geografía física', 'estudios urbanos y rurales'],
            autores: ['Méndez', 'Puyol', 'Capel', 'Ortega Valcárcel', 'Harvey'],
            cdus: ['HG91', 'HG911', 'HG71'],
            en: ['human geography', 'urban studies', 'landscape', 'regional planning']
        },
        {
            id: 'musica_musicologia',
            label: 'Historia y Ciencias de la Música',
            triggers: ['musica', 'música', 'musicologia', 'musicología', 'opera', 'ópera', 'sinfonia', 'compositor', 'beethoven', 'mozart', 'bach', 'flamenco'],
            sinonimos: ['historia de la música', 'análisis musical', 'etnomusicología', 'estética musical'],
            autores: ['Grout', 'Taruskin', 'Salazar', 'Casares', 'Fubini'],
            cdus: ['HG78'],
            en: ['musicology', 'music history', 'ethnomusicology']
        },
        {
            id: 'investigacion_tfg',
            label: 'Metodología de Investigación, TFG y Tesis',
            triggers: ['como hacer un tfg', 'metodologia', 'metodología', 'citar', 'apa', 'mla', 'chicago', 'estado de la cuestion', 'fuentes documentales'],
            sinonimos: ['técnicas de investigación en humanidades', 'redacción académica', 'crítica textual y fuentes'],
            autores: ['Umberto Eco', 'Sierra Bravo', 'Alonso', 'Botta'],
            cdus: ['HG001', 'HG01', 'HG02'],
            en: ['academic research methods', 'humanities thesis writing']
        }
    ];

    let promesaBaseDatos = null;
    const estado = {
        temaBase: '',
        keywords: [],
        sinonimos: [],
        autores: [],
        cdus: [],
        terminosEN: [],
        conceptosDetectados: [],
        filtros: {
            soloHG: false,
            anioDesde: null,
            anioHasta: null,
            modo: 'general'
        },
        excluidos: new Set(),
        turnos: 0
    };

    function crearEstadoInicial() {
        estado.temaBase = '';
        estado.keywords = [];
        estado.sinonimos = [];
        estado.autores = [];
        estado.cdus = [];
        estado.terminosEN = [];
        estado.conceptosDetectados = [];
        estado.filtros = { soloHG: false, anioDesde: null, anioHasta: null, modo: 'general' };
        estado.excluidos.clear();
        estado.turnos = 0;
    }

    /**
     * Comprueba si un término aparece en un texto ya normalizado a nivel de palabra
     * completa (o con flexión regular española para términos de >= 5 letras).
     */
    function contienePalabra(textoNorm, termino) {
        if (!textoNorm || !termino) return false;
        const len = termino.length;
        let idx = 0;
        while (true) {
            const pos = textoNorm.indexOf(termino, idx);
            if (pos === -1) return false;
            const prevOk = pos === 0 || textoNorm.charCodeAt(pos - 1) === 32;
            if (prevOk) {
                const end = pos + len;
                if (end === textoNorm.length || textoNorm.charCodeAt(end) === 32) {
                    return true;
                }
                if (len >= 5) {
                    const nextSpace = textoNorm.indexOf(' ', end);
                    const tail = nextSpace === -1 ? textoNorm.slice(end) : textoNorm.slice(end, nextSpace);
                    if (/^(s|es|a|o|as|os|ico|ica|icos|icas|ero|era)$/.test(tail)) {
                        return true;
                    }
                }
            }
            idx = pos + 1;
        }
    }

    let conocimientoColectivoPedido = false;

    function precargarBaseDatos(gasUrl) {
        if (gasUrl && !conocimientoColectivoPedido && global.BUOAprendizaje) {
            conocimientoColectivoPedido = true;
            global.BUOAprendizaje.cargarConocimientoColectivo(gasUrl).catch(() => {});
        }
        if (promesaBaseDatos) return promesaBaseDatos;

        promesaBaseDatos = (async () => {
            const { limpiarTexto } = global.BUOUtils;
            const [libros, todasCdu, grado3, sinonimosJson] = await Promise.all([
                global.BUOBuscador.cargarCatalogoLibros(''),
                fetch('todassignaturas.json').then(r => (r.ok ? r.json() : [])).catch(() => []),
                fetch('Grados/grado3.json?v=2').then(r => (r.ok ? r.json() : [])).catch(() => []),
                fetch('data/sinonimos.json').then(r => (r.ok ? r.json() : {})).catch(() => ({}))
            ]);

            const sinonimosExternos = {};
            const fuenteSyns = (sinonimosJson && sinonimosJson.sinonimosPalabra && typeof sinonimosJson.sinonimosPalabra === 'object')
                ? sinonimosJson.sinonimosPalabra
                : {};
            Object.entries(fuenteSyns).forEach(([clave, lista]) => {
                const claveNorm = limpiarTexto(clave);
                if (!claveNorm || !Array.isArray(lista)) return;
                sinonimosExternos[claveNorm] = lista.map(s => limpiarTexto(s)).filter(Boolean);
            });

            const docFreq = new Map();
            const apellidosCatalogo = new Set();

            for (const libro of libros) {
                const matchAnio = String(libro.fecha || '').match(/\b(1[5-9]\d{2}|20[0-2]\d)\b/);
                libro._anio = matchAnio ? parseInt(matchAnio[1], 10) : null;
                libro._sigUpper = String(libro.signatura || '').toUpperCase();
                libro._esHG = libro._sigUpper.startsWith('HG');
                libro._esHGCompleto = libro._esHG && libro._sigUpper.length > 2;

                // Contar frecuencia de documentos (para IDF) sin duplicar tokens dentro del mismo libro
                const vistosLibro = new Set();
                const tParts = libro._tNorm ? libro._tNorm.split(' ') : [];
                const aParts = libro._aNorm ? libro._aNorm.split(' ') : [];
                for (let i = 0; i < tParts.length; i++) {
                    const tok = tParts[i];
                    if (tok.length >= 3 && !vistosLibro.has(tok)) {
                        vistosLibro.add(tok);
                        docFreq.set(tok, (docFreq.get(tok) || 0) + 1);
                    }
                }
                for (let i = 0; i < aParts.length; i++) {
                    const tok = aParts[i];
                    if (tok.length >= 3 && !vistosLibro.has(tok)) {
                        vistosLibro.add(tok);
                        docFreq.set(tok, (docFreq.get(tok) || 0) + 1);
                    }
                }

                // Indexar apellidos reales de personas (antes de la primera coma)
                const rawAutor = String(libro.autor || '');
                const commaIdx = rawAutor.indexOf(',');
                if (commaIdx > 0) {
                    const apNorm = limpiarTexto(rawAutor.slice(0, commaIdx));
                    const apTokens = apNorm.split(' ');
                    const esCorporativo = apTokens.some(w => PALABRAS_INSTITUCIONALES.has(w));
                    if (!esCorporativo) {
                        for (const apTok of apTokens) {
                            if (apTok.length >= 4 && !NO_APELLIDOS.has(apTok) && !STOPWORDS_ESPANOL.has(apTok)) {
                                apellidosCatalogo.add(apTok);
                            }
                        }
                    }
                }
            }

            const mapaCdu = (Array.isArray(todasCdu) ? todasCdu : [])
                .filter(item => item && item.signaturas && item.descripcion)
                .map(item => {
                    const descLimpia = String(item.descripcion).replace(/\s*-{2,}.*$/g, '').trim();
                    return {
                        signaturas: String(item.signaturas).trim(),
                        signaturasUpper: String(item.signaturas).trim().toUpperCase(),
                        descripcion: descLimpia,
                        _descNorm: limpiarTexto(descLimpia)
                    };
                })
                .filter(item => item.descripcion.length > 0);

            const indiceGrado = new Map();
            for (const g of (Array.isArray(grado3) ? grado3 : [])) {
                const tit = limpiarTexto(g['Obra/Título'] || '');
                const asig = String(g.ASIGNATURA || '').trim();
                if (!tit || !asig) continue;
                if (!indiceGrado.has(tit)) indiceGrado.set(tit, new Set());
                indiceGrado.get(tit).add(asig);
            }

            return {
                libros,
                totalLibros: libros.length,
                docFreq,
                apellidosCatalogo,
                mapaCdu,
                indiceGrado,
                sinonimosExternos
            };
        })();

        return promesaBaseDatos;
    }

    function extraerNombresPropiosExplicitos(textoOriginal) {
        const candidatos = new Set();
        const palabras = String(textoOriginal || '').trim().split(/\s+/);
        for (let i = 0; i < palabras.length; i++) {
            const raw = palabras[i].replace(/^[^\p{L}]+|[^\p{L}]+$/gu, '');
            if (!raw || raw.length < 3) continue;
            // Palabra capitalizada que no sea la primera palabra de la frase
            if (i > 0 && /^[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+$/.test(raw)) {
                const norm = global.BUOUtils.limpiarTexto(raw);
                if (norm && !STOPWORDS_ESPANOL.has(norm) && !NO_APELLIDOS.has(norm)) {
                    candidatos.add(norm);
                }
            }
        }
        return candidatos;
    }

    function detectarAniosYFiltros(textoNorm) {
        const anioActual = new Date().getFullYear();
        let anioDesde = null;
        let anioHasta = null;
        let soloHG = null;
        let modo = null;

        const mDesde = textoNorm.match(/(?:desde|despues de|posteriores? a|a partir de)\s+(\d{4})/);
        if (mDesde) anioDesde = parseInt(mDesde[1], 10);

        const mHasta = textoNorm.match(/(?:antes de|anteriores? a|hasta)\s+(\d{4})/);
        if (mHasta) anioHasta = parseInt(mHasta[1], 10);

        if (/\b(recientes?|actuales?|ultimos? anos?|ultimos? diez|novedades?|modernos?)\b/.test(textoNorm) && !anioDesde) {
            anioDesde = anioActual - 15;
        }

        if (/\b(libre acceso|en sala|en las salas|solo sala|solo hg|fisico en sala|estanteria)\b/.test(textoNorm)) {
            soloHG = true;
        } else if (/\b(todos los fondos|incluir deposito|tambien deposito|todo el catalogo)\b/.test(textoNorm)) {
            soloHG = false;
        }

        if (/\b(tfg|tfm|tesis|trabajo|investigacion|investigar|ensayo|estado de la cuestion|marco teorico|estrategia|palabras clave|como buscar|bases de datos)\b/.test(textoNorm)) {
            modo = 'investigacion';
        }

        return { anioDesde, anioHasta, soloHG, modo };
    }

    function esConsultaDeRefinamiento(textoNorm) {
        if (estado.turnos === 0 || !estado.temaBase) return false;

        const patronesRefinamiento = [
            /^(y\s+|pero\s+|ahora\s+|ademas\s+|tambien\s+)/,
            /\b(mas libros|mas resultados|otros libros|dame mas|dame otros|muestra mas|ver mas|siguientes|otros distintos|alguna otra|otro enfoque|enfocalo|centrate|refina|filtra|filtrar)\b/,
            /\b(mas recientes?|anteriores? a|despues de|desde \d{4}|antes de \d{4}|solo en sala|libre acceso|en deposito|todo el catalogo|sobre el mismo tema|de ese autor|palabras clave|estrategia|como citar|bases de datos)\b/
        ];

        if (patronesRefinamiento.some(re => re.test(textoNorm))) return true;
        const tokens = textoNorm.split(/\s+/).filter(Boolean);
        return tokens.length <= 2 && /^(en |de |con |sin |solo |y )/.test(textoNorm);
    }

    function detectarConceptosYCDU(textoNorm, mapaCdu) {
        const { limpiarTexto } = global.BUOUtils;
        const conceptos = [];
        const sinonimosSet = new Set();
        const autoresSugeridos = new Set();
        const cdusSet = new Set();
        const terminosEN = new Set();

        for (const c of CONCEPTOS_HUMANIDADES) {
            if (c.triggers.some(tr => textoNorm.includes(limpiarTexto(tr)))) {
                conceptos.push(c);
                c.sinonimos.forEach(s => sinonimosSet.add(s));
                c.autores.forEach(a => autoresSugeridos.add(a));
                c.cdus.forEach(code => cdusSet.add(code.toUpperCase()));
                (c.en || []).forEach(e => terminosEN.add(e));
            }
        }

        const palabrasBusq = textoNorm.split(/\s+/).filter(p => p.length >= 4 && !STOPWORDS_ESPANOL.has(p));
        const cdusConceptos = Array.from(cdusSet);
        const cdusDescriptores = [];
        if (palabrasBusq.length > 0 && Array.isArray(mapaCdu)) {
            for (const entry of mapaCdu) {
                let hits = 0;
                for (const pal of palabrasBusq) {
                    if (contienePalabra(entry._descNorm, pal)) hits++;
                }
                if (hits > 0 && entry.signaturasUpper.startsWith('HG')) {
                    const coincideConcepto = cdusConceptos.some(c => entry.signaturasUpper.startsWith(c));
                    if (cdusConceptos.length > 0 && !coincideConcepto && hits < 2) continue;
                    cdusDescriptores.push({ ...entry, hits: hits + (coincideConcepto ? 2 : 0) });
                }
            }
            cdusDescriptores.sort((a, b) => b.hits - a.hits || a.signaturas.length - b.signaturas.length);
            cdusDescriptores.slice(0, 4).forEach(d => cdusSet.add(d.signaturasUpper));
        }

        return {
            conceptos,
            sinonimos: Array.from(sinonimosSet),
            autoresSugeridos: Array.from(autoresSugeridos),
            cdus: Array.from(cdusSet),
            cdusDescriptores: cdusDescriptores.slice(0, 3),
            terminosEN: Array.from(terminosEN)
        };
    }

    function construirGruposConcepto(keywords, nombresPropiosSet, db) {
        const { limpiarTexto } = global.BUOUtils;
        const kwSet = new Set(keywords);
        const N = db.totalLibros || 1;
        const synsExternos = (db && db.sinonimosExternos) ? db.sinonimosExternos : {};
        const synsColectivos = (global.BUOAprendizaje && global.BUOAprendizaje.getSinonimosColectivos)
            ? global.BUOAprendizaje.getSinonimosColectivos()
            : {};

        return keywords.map(kw => {
            const esApellidoCatalogo = db.apellidosCatalogo.has(kw) && !NO_APELLIDOS.has(kw);
            const esNombrePropio = nombresPropiosSet.has(kw) || esApellidoCatalogo;
            const freq = db.docFreq.get(kw) || 40;
            const idf = Math.max(0.8, Math.log((N + 1) / (freq + 1)));

            // Sinónimos propios de esta keyword combinando base interna, data/sinonimos.json y hoja colectiva
            const rawSyns = [
                ...(SINONIMOS_PALABRA[kw] || []),
                ...(synsExternos[kw] || []),
                ...(synsColectivos[kw] || [])
            ];
            const sinonimos = [];
            for (const s of rawSyns) {
                const sn = limpiarTexto(s);
                if (sn && !kwSet.has(sn) && !sinonimos.includes(sn)) {
                    sinonimos.push(sn);
                }
            }

            return {
                kw,
                esNombrePropio,
                idf,
                sinonimos
            };
        });
    }

    function evaluarLibroComoBibliotecario(libro, grupos, ctx, db) {
        const tituloNorm = libro._tNorm;
        const autorNorm = libro._aNorm;

        let cubiertos = 0;
        let aciertosDirectos = 0;
        let aciertosPersonaAutor = 0;
        let puntos = 0;

        for (let i = 0; i < grupos.length; i++) {
            const g = grupos[i];
            const enTitulo = contienePalabra(tituloNorm, g.kw);
            const enAutor = contienePalabra(autorNorm, g.kw);

            let synCoincide = false;
            if (!enTitulo && !enAutor && g.sinonimos.length > 0) {
                for (let j = 0; j < g.sinonimos.length; j++) {
                    const syn = g.sinonimos[j];
                    if (contienePalabra(tituloNorm, syn) || contienePalabra(autorNorm, syn)) {
                        synCoincide = true;
                        break;
                    }
                }
            }

            if (enTitulo || enAutor || synCoincide) {
                cubiertos++;
                const peso = g.idf * (g.esNombrePropio ? 1.85 : 1.0);

                if (enTitulo || enAutor) {
                    aciertosDirectos++;
                }
                if (enAutor && g.esNombrePropio) {
                    aciertosPersonaAutor++;
                    puntos += peso * 3.4;
                } else if (enAutor) {
                    puntos += peso * 1.1;
                }
                if (enTitulo) {
                    puntos += peso * (g.esNombrePropio ? 2.8 : 2.4);
                }
                if (synCoincide) {
                    puntos += peso * 1.15;
                }
            }
        }

        if (cubiertos === 0) return null;

        // Si el usuario dio >= 3 palabras clave, exigir al menos 2 conceptos cubiertos o un autor propio directo
        if (grupos.length >= 3 && cubiertos < 2 && aciertosPersonaAutor === 0) {
            return null;
        }

        // Bonus por frase exacta en título o autor
        if (ctx.fraseLimpia.length >= 5) {
            if (tituloNorm.includes(ctx.fraseLimpia)) puntos += 20;
            if (autorNorm.includes(ctx.fraseLimpia)) puntos += 18;
        }

        // Bonus moderado por estantería CDU coincidente (no altera la prioridad de palabras clave)
        for (let i = 0; i < ctx.cdus.length; i++) {
            if (libro._sigUpper.startsWith(ctx.cdus[i])) {
                puntos += 3;
                break;
            }
        }

        const enGrado = db.indiceGrado && db.indiceGrado.has(tituloNorm) ? 1 : 0;
        if (enGrado) puntos += 4;

        // Re-ranking adaptativo según utilidad aprendida en consultas previas
        if (global.BUOAprendizaje && global.BUOAprendizaje.obtenerBonusAprendido) {
            puntos += global.BUOAprendizaje.obtenerBonusAprendido(tituloNorm, ctx.keywords);
        }

        return {
            cubiertos,
            aciertosDirectos,
            aciertosPersonaAutor,
            puntos,
            enGrado,
            esHGCompleto: libro._esHGCompleto ? 1 : 0,
            anio: libro._anio || 0
        };
    }

    function describirPlantaSignatura(sig) {
        const sigUpper = String(sig || '').toUpperCase();
        const primer = sigUpper.charAt(0);
        const segundo = sigUpper.charAt(1);
        const tercero = sigUpper.charAt(2);
        const esHLDeposito = (primer === 'H' || primer === 'L') && /[0-9]/.test(segundo);

        if (sigUpper.startsWith('HG')) {
            if (['0', '1', '2', '3', '5', '6'].includes(tercero)) return '📍 Libre Acceso · Filosofía y Ciencias (1ª Planta)';
            if (['7', '9'].includes(tercero)) return '📍 Libre Acceso · Arte e Historia (2ª Planta)';
            if (tercero === '8') return '📍 Libre Acceso · Literatura y Lingüística (3ª Planta)';
            return '📍 Libre Acceso · Salas de Lectura';
        }
        if (sigUpper.startsWith('HR')) return '📍 Sala de Referencia · Planta Baja';
        if (sigUpper.startsWith('HI')) return '📍 Fondo de Estudios Árabes e Islámicos';
        if (sigUpper.startsWith('HT') || sigUpper.includes('TESIS')) return '📍 Sección de Tesis Doctorales';
        if (sigUpper.startsWith('HE') || sigUpper.startsWith('F') || sigUpper.includes('GF') || esHLDeposito) {
            return '📍 Depósito general de la biblioteca';
        }
        if (sigUpper.startsWith('HMONT') || sigUpper.startsWith('HMSR')) {
            return '📍 Planta 1ª · Sala Especial / Fondos Particulares';
        }
        return 'Biblioteca de Humanidades (Campus de El Milán)';
    }

    function construirGuiasInvestigacion(ctx, totalHallados) {
        const { escapeHTML } = global.BUOUtils;
        const queryPrincipal = ctx.fraseLimpia || ctx.temaBase || ctx.keywords.join(' ');
        const qEnc = encodeURIComponent(queryPrincipal);
        const qEn = encodeURIComponent((ctx.terminosEN && ctx.terminosEN[0]) || queryPrincipal);

        const palabrasClave = Array.from(new Set([
            ...ctx.keywords,
            ...(ctx.sinonimos || []).slice(0, 4),
            ...(ctx.terminosEN || []).slice(0, 3)
        ])).filter(Boolean);

        const cduBadges = (ctx.cdusDescriptores || []).length > 0
            ? ctx.cdusDescriptores.map(d => `<span class="bg-stone-100 border border-stone-300 px-2 py-0.5 font-mono text-[10px] text-stone-800">${escapeHTML(d.signaturas)} · ${escapeHTML(d.descripcion)}</span>`).join(' ')
            : (ctx.cdus || []).slice(0, 3).map(c => `<span class="bg-stone-100 border border-stone-300 px-2 py-0.5 font-mono text-[10px] text-stone-800">${escapeHTML(c)}</span>`).join(' ');

        const autoresLista = (ctx.autoresSugeridos || []).slice(0, 5);

        return {
            palabrasClave,
            cduBadges,
            autoresLista,
            enlaces: [
                { nombre: 'OPAC BUO (Todo el catálogo)', url: `https://absysweb.cpd.uniovi.es/cgi-bin/abnetopac?ACC=DOSEARCH&xsqf99=${qEnc}` },
                { nombre: 'RUO (Tesis y TFG UniOvi)', url: `https://digibuo.uniovi.es/dspace/discover?query=${qEnc}` },
                { nombre: 'Dialnet (Artículos en español)', url: `https://dialnet.unirioja.es/buscar/documentos?querysDismax.DOCUMENTAL_TODO=${qEnc}` },
                { nombre: 'BNE (Biblioteca Nacional)', url: `https://catalogo.bne.es/discovery/search?query=any,contains,${qEnc}&vid=34BNE_INST:CATALOGO&lang=es` },
                { nombre: 'Google Scholar (Internacional)', url: `https://scholar.google.com/scholar?q=${qEn}` }
            ],
            totalHallados
        };
    }

    async function procesarConsultaLocal(textoUsuario, opciones = {}) {
        const { limpiarTexto } = global.BUOUtils;
        const db = await precargarBaseDatos(opciones.gasUrl || '');

        const textoOriginal = String(textoUsuario || '').trim();
        const textoNorm = limpiarTexto(textoOriginal);
        const esSoloPeticionMas = /\b(mas libros|mas resultados|otros libros|dame mas|dame otros|muestra mas|ver mas|siguientes|otros distintos)\b/.test(textoNorm);
        const esRefinamiento = opciones.forzarRefinamiento || esConsultaDeRefinamiento(textoNorm);

        const filtrosDetectados = detectarAniosYFiltros(textoNorm);
        const nombresPropiosExplicitos = extraerNombresPropiosExplicitos(textoOriginal);

        if (!esRefinamiento) {
            crearEstadoInicial();
            estado.temaBase = textoOriginal;
        }
        estado.turnos += 1;

        if (filtrosDetectados.anioDesde !== null) estado.filtros.anioDesde = filtrosDetectados.anioDesde;
        if (filtrosDetectados.anioHasta !== null) estado.filtros.anioHasta = filtrosDetectados.anioHasta;
        if (filtrosDetectados.soloHG !== null) estado.filtros.soloHG = filtrosDetectados.soloHG;
        if (filtrosDetectados.modo) estado.filtros.modo = filtrosDetectados.modo;

        if (typeof opciones.soloHG === 'boolean') estado.filtros.soloHG = opciones.soloHG;
        if (opciones.anioDesde !== undefined) estado.filtros.anioDesde = opciones.anioDesde;
        if (opciones.modo) estado.filtros.modo = opciones.modo;

        const tokensLimpios = textoNorm
            .split(/\s+/)
            .filter(p => p.length >= 2 && !STOPWORDS_ESPANOL.has(p) && !/^(desde|despues|antes|hasta|solo|mas|otros|distintos|recientes|sala|deposito)$/.test(p));

        const nuevosKeywords = esSoloPeticionMas ? [] : tokensLimpios.filter(t => {
            if (/^\d{4}$/.test(t) && (filtrosDetectados.anioDesde === Number(t) || filtrosDetectados.anioHasta === Number(t))) {
                return false;
            }
            return true;
        });

        if (!esRefinamiento) {
            estado.keywords = Array.from(new Set(nuevosKeywords));
            estado.autores = Array.from(nombresPropiosExplicitos);
        } else if (nuevosKeywords.length > 0) {
            estado.keywords = Array.from(new Set([...estado.keywords, ...nuevosKeywords]));
            estado.autores = Array.from(new Set([...estado.autores, ...nombresPropiosExplicitos]));
        }

        const contextoSemantico = detectarConceptosYCDU(
            limpiarTexto([estado.temaBase, ...estado.keywords, textoOriginal].join(' ')),
            db.mapaCdu
        );

        estado.conceptosDetectados = contextoSemantico.conceptos;
        estado.sinonimos = contextoSemantico.sinonimos;
        estado.cdus = contextoSemantico.cdus;
        estado.terminosEN = contextoSemantico.terminosEN;

        const nombresPropiosSet = new Set(estado.autores);
        const grupos = construirGruposConcepto(estado.keywords, nombresPropiosSet, db);
        const fraseLimpia = limpiarTexto(estado.keywords.join(' '));

        const ctxBusqueda = {
            temaBase: estado.temaBase,
            fraseLimpia,
            keywords: estado.keywords,
            sinonimos: estado.sinonimos,
            autoresSugeridos: contextoSemantico.autoresSugeridos,
            cdus: estado.cdus,
            cdusDescriptores: contextoSemantico.cdusDescriptores,
            terminosEN: estado.terminosEN
        };

        // Evaluar cada libro del catálogo con criterio bibliotecario
        const candidatos = [];
        if (grupos.length > 0) {
            for (let i = 0; i < db.libros.length; i++) {
                const libro = db.libros[i];
                if (estado.filtros.soloHG && !libro._esHG) continue;
                if (estado.filtros.anioDesde && (!libro._anio || libro._anio < estado.filtros.anioDesde)) continue;
                if (estado.filtros.anioHasta && (!libro._anio || libro._anio > estado.filtros.anioHasta)) continue;

                const evalRes = evaluarLibroComoBibliotecario(libro, grupos, ctxBusqueda, db);
                if (evalRes) {
                    candidatos.push({ libro, evalRes });
                }
            }
        }

        // Ordenación jerárquica: cobertura > aciertos directos > autor propio > puntuación IDF > grado > HG completo > año
        candidatos.sort((a, b) => {
            const ea = a.evalRes;
            const eb = b.evalRes;
            if (eb.cubiertos !== ea.cubiertos) return eb.cubiertos - ea.cubiertos;
            if (eb.aciertosDirectos !== ea.aciertosDirectos) return eb.aciertosDirectos - ea.aciertosDirectos;
            if (eb.aciertosPersonaAutor !== ea.aciertosPersonaAutor) return eb.aciertosPersonaAutor - ea.aciertosPersonaAutor;
            if (Math.abs(eb.puntos - ea.puntos) > 0.01) return eb.puntos - ea.puntos;
            if (eb.enGrado !== ea.enGrado) return eb.enGrado - ea.enGrado;
            if (eb.esHGCompleto !== ea.esHGCompleto) return eb.esHGCompleto - ea.esHGCompleto;
            return eb.anio - ea.anio;
        });

        const agrupados = new Map();
        for (const { libro, evalRes } of candidatos) {
            const clave = global.normalizarClave ? global.normalizarClave(libro.titulo) : libro._tNorm;
            if (estado.excluidos.has(clave)) continue;

            if (!agrupados.has(clave)) {
                const asigs = db.indiceGrado.get(libro._tNorm);
                agrupados.set(clave, {
                    titulo: libro.titulo || 'Sin título',
                    autor: libro.autor || 'Sin autor',
                    fecha: libro.fecha || '',
                    materia: asigs
                        ? `Bibliografía de Grado (${Array.from(asigs)[0]})`
                        : (libro._esHG ? 'Libre Acceso en Sala' : 'Catálogo Humanidades'),
                    signaturas: [libro.signatura],
                    coincidenciasKeywords: `${evalRes.cubiertos}/${grupos.length}`,
                    enGrado: Boolean(asigs)
                });
            } else {
                const existente = agrupados.get(clave);
                if (libro.signatura && !existente.signaturas.some(s => s.toLowerCase() === libro.signatura.toLowerCase())) {
                    existente.signaturas.push(libro.signatura);
                }
            }

            if (agrupados.size >= 40) break;
        }

        const agrupadosTodos = Array.from(agrupados.values());
        const seleccionados = agrupadosTodos.slice(0, 8);
        seleccionados.forEach(l => {
            const clave = global.normalizarClave ? global.normalizarClave(l.titulo) : limpiarTexto(l.titulo);
            estado.excluidos.add(clave);
        });

        const candidatosParaRAG = agrupadosTodos.slice(0, 15).map(l => ({
            titulo: l.titulo,
            autor: l.autor,
            fecha: l.fecha,
            signaturas: l.signaturas.slice(0, 3),
            enGrado: l.enGrado
        }));

        if (candidatos.length === 0 && !esSoloPeticionMas && textoOriginal && global.BUOAprendizaje) {
            global.BUOAprendizaje.registrarLagunaBusqueda({
                consulta: textoOriginal,
                keywords: estado.keywords,
                gasUrl: opciones.gasUrl || ''
            });
        }

        const guiasInvestigacion = construirGuiasInvestigacion(ctxBusqueda, candidatos.length);

        return {
            librosLocales: seleccionados,
            candidatosParaRAG,
            totalCoincidencias: candidatos.length,
            esRefinamiento,
            esSoloPeticionMas,
            filtrosActivos: { ...estado.filtros },
            keywordsUsadas: [...estado.keywords],
            conceptos: contextoSemantico.conceptos.map(c => c.label),
            sinonimos: estado.sinonimos.slice(0, 4),
            cdusDescriptores: contextoSemantico.cdusDescriptores,
            guiasInvestigacion
        };
    }

    async function ampliarBusquedaConSugerenciasIA(sugerencias = {}, opciones = {}) {
        const { limpiarTexto } = global.BUOUtils;
        const db = await precargarBaseDatos(opciones.gasUrl || '');

        const crudos = [
            ...(Array.isArray(sugerencias.keywords_sugeridas) ? sugerencias.keywords_sugeridas : []),
            ...(Array.isArray(sugerencias.sinonimos) ? sugerencias.sinonimos : [])
        ];

        const baseSet = new Set(estado.keywords);
        const terminosAmpliados = [];
        for (const item of crudos) {
            const palabras = limpiarTexto(item).split(/\s+/);
            for (const p of palabras) {
                if (p.length >= 3 && !STOPWORDS_ESPANOL.has(p) && !baseSet.has(p) && !terminosAmpliados.includes(p)) {
                    terminosAmpliados.push(p);
                }
                if (terminosAmpliados.length >= 6) break;
            }
            if (terminosAmpliados.length >= 6) break;
        }

        if (terminosAmpliados.length === 0) {
            return { librosLocales: [], terminosAmpliados: [] };
        }

        const gruposExtra = construirGruposConcepto(terminosAmpliados, new Set(), db);
        const ctxExtra = {
            fraseLimpia: limpiarTexto(terminosAmpliados.join(' ')),
            keywords: terminosAmpliados,
            cdus: estado.cdus || []
        };

        const candidatos = [];
        for (let i = 0; i < db.libros.length; i++) {
            const libro = db.libros[i];
            if (estado.filtros.soloHG && !libro._esHG) continue;
            if (estado.filtros.anioDesde && (!libro._anio || libro._anio < estado.filtros.anioDesde)) continue;
            if (estado.filtros.anioHasta && (!libro._anio || libro._anio > estado.filtros.anioHasta)) continue;

            const evalRes = evaluarLibroComoBibliotecario(libro, gruposExtra, ctxExtra, db);
            if (evalRes) {
                candidatos.push({ libro, evalRes });
            }
        }

        candidatos.sort((a, b) => {
            const ea = a.evalRes;
            const eb = b.evalRes;
            if (eb.cubiertos !== ea.cubiertos) return eb.cubiertos - ea.cubiertos;
            if (Math.abs(eb.puntos - ea.puntos) > 0.01) return eb.puntos - ea.puntos;
            return eb.anio - ea.anio;
        });

        const agrupados = new Map();
        for (const { libro, evalRes } of candidatos) {
            const clave = global.normalizarClave ? global.normalizarClave(libro.titulo) : libro._tNorm;
            if (estado.excluidos.has(clave)) continue;

            if (!agrupados.has(clave)) {
                const asigs = db.indiceGrado.get(libro._tNorm);
                agrupados.set(clave, {
                    titulo: libro.titulo || 'Sin título',
                    autor: libro.autor || 'Sin autor',
                    fecha: libro.fecha || '',
                    materia: asigs
                        ? `Bibliografía de Grado (${Array.from(asigs)[0]})`
                        : (libro._esHG ? 'Libre Acceso en Sala' : 'Catálogo Humanidades'),
                    signaturas: [libro.signatura],
                    coincidenciasKeywords: `${evalRes.cubiertos}/${gruposExtra.length} (ampliado)`,
                    enGrado: Boolean(asigs)
                });
            } else {
                const existente = agrupados.get(clave);
                if (libro.signatura && !existente.signaturas.some(s => s.toLowerCase() === libro.signatura.toLowerCase())) {
                    existente.signaturas.push(libro.signatura);
                }
            }
            if (agrupados.size >= 6) break;
        }

        const extraSeleccionados = Array.from(agrupados.values());
        extraSeleccionados.forEach(l => {
            const clave = global.normalizarClave ? global.normalizarClave(l.titulo) : limpiarTexto(l.titulo);
            estado.excluidos.add(clave);
        });

        return {
            librosLocales: extraSeleccionados,
            terminosAmpliados
        };
    }

    function generarTextoRespuestaLocal(resLocal, textoUsuario) {
        const filtrosTxt = [];
        if (resLocal.filtrosActivos.soloHG) filtrosTxt.push('solo Libre Acceso (HG)');
        if (resLocal.filtrosActivos.anioDesde) filtrosTxt.push(`desde ${resLocal.filtrosActivos.anioDesde}`);
        if (resLocal.filtrosActivos.anioHasta) filtrosTxt.push(`hasta ${resLocal.filtrosActivos.anioHasta}`);

        const filtroResumen = filtrosTxt.length > 0 ? ` aplicando filtro (${filtrosTxt.join(', ')})` : '';
        const kwsResumen = (resLocal.keywordsUsadas || []).length > 0
            ? ` por **${resLocal.keywordsUsadas.join(', ')}**`
            : '';

        if (resLocal.librosLocales.length > 0) {
            let msg = `He buscado en el catálogo de Humanidades${kwsResumen}${filtroResumen} y he encontrado **${resLocal.totalCoincidencias} obras** relevantes (ordenadas por mayor coincidencia de palabras clave, autores y sinónimos):`;
            if (resLocal.cdusDescriptores.length > 0) {
                const secciones = resLocal.cdusDescriptores.slice(0, 2).map(d => `\`${d.signaturas}\` (${d.descripcion})`).join(' y ');
                msg += `\n\n- **Estanterías recomendadas en sala:** ${secciones}.`;
            }
            if (resLocal.filtrosActivos.modo === 'investigacion') {
                msg += `\n- **Para tu trabajo/investigación:** abajo tienes palabras clave en español e inglés y enlaces directos a **OPAC BUO**, **RUO**, **Dialnet**, **BNE** y **Google Scholar**.`;
            }
            return msg;
        }

        return `No he encontrado coincidencias directas en el catálogo local para **"${textoUsuario}"**${filtroResumen}. Prueba a quitar filtros o ampliar con los términos relacionados y portales académicos de abajo.`;
    }

    function getEstadoConversacion() {
        return {
            temaBase: estado.temaBase,
            keywords: [...estado.keywords],
            filtros: { ...estado.filtros },
            turnos: estado.turnos
        };
    }

    global.BUOAsistente = {
        precargarBaseDatos,
        procesarConsultaLocal,
        ampliarBusquedaConSugerenciasIA,
        generarTextoRespuestaLocal,
        describirPlantaSignatura,
        reiniciarEstado: crearEstadoInicial,
        getEstadoConversacion
    };
})(window);
