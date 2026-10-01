// The viewer page's own language layer (ZST-SPEC-VAP-001 §6.4).
//
// Scope, deliberately narrow: the viewer STATE messages, which are the instructions a person
// must understand to watch (pre-event, paused, reconnecting, replay, capacity, errors), and
// the controls that read them aloud. Each string is also its audio-assist text, so every
// supported language has matching spoken prompts by construction. The rest of the dashboard
// (chat, Q&A, polls, settings) keeps its English copy. Translating that is a separate
// localisation job, not something to fake here.
//
//   * initial language = the browser's (navigator.languages), when supported
//   * a manual choice is remembered on this device (localStorage) and wins from then on
//   * each language is named in its own script in the picker
//   * <html lang> follows the choice while the page is open, so screen readers and the
//     speech engine pronounce it correctly
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

// eslint-disable-next-line react-refresh/only-export-components
export const LANGUAGES = [
  { code: "en", name: "English" },
  { code: "es", name: "Español" },
  { code: "fr", name: "Français" },
  { code: "de", name: "Deutsch" },
  { code: "pt", name: "Português" },
  { code: "hi", name: "हिन्दी" },
];
const SUPPORTED = new Set(LANGUAGES.map((l) => l.code));
export const LANGUAGE_STORAGE_KEY = "zk_viewer_lang";

const STRINGS = {
  en: {
    language: "Language",
    readAloud: "Read aloud",
    stopReading: "Stop reading",
    live: "Live",
    registerPrompt: "Enter your name to start watching.",
    livePlaying: "The broadcast is live.",
    preEventTitle: "The service hasn't started yet",
    preEventBody: "Service will begin here automatically.",
    yourTime: "Your time",
    windowClosedTitle: "This event has ended",
    windowClosedBody: "The scheduled viewing time for this event has closed.",
    reconnecting: "Reconnecting… The video will continue automatically.",
    paused: "The service is paused. Please stay on this page.",
    endedTitle: "This event has ended",
    watchReplay: "Watch the replay",
    replayAvailable: "The replay is available.",
    availableUntil: "Available until {date}",
    replayProcessing: "The replay is being prepared. This page will update automatically.",
    replayExpired: "The replay is no longer available.",
    replayUnavailable: "No recording is available for this event.",
    capacityTitle: "This event is very busy right now",
    capacityBody: "You'll join automatically as soon as there's room. Please keep this page open.",
    capacityRetry: "Trying again in {seconds} s",
    tryNow: "Try now",
    inAppTitle: "For the best experience, open this page in your browser",
    inAppIos: "Tap the menu (⋯ or the share button) and choose “Open in browser”.",
    inAppAndroid: "Tap the menu (⋮) and choose “Open in browser”, or use the button below.",
    inAppInvite: "If you were sent a private invitation, open the original invitation link in your browser.",
    openInBrowser: "Open in browser",
    copyLink: "Copy link",
    linkCopied: "Link copied",
    dismiss: "Dismiss",
    errorTitle: "We couldn't load this event",
    errorBody: "Please check your connection and try again.",
    retry: "Try again",
    help: "Contact support",
    status: "Service status",
    invitationInvalid: "This invitation link isn't valid. You can still continue below.",
    captionsOn: "Turn captions on",
    captionsOff: "Turn captions off",
  },
  es: {
    language: "Idioma",
    readAloud: "Leer en voz alta",
    stopReading: "Dejar de leer",
    live: "En directo",
    registerPrompt: "Escriba su nombre para empezar a ver.",
    livePlaying: "La transmisión está en directo.",
    preEventTitle: "El servicio aún no ha comenzado",
    preEventBody: "El servicio comenzará aquí automáticamente.",
    yourTime: "Su hora",
    windowClosedTitle: "Este evento ha terminado",
    windowClosedBody: "El horario previsto para ver este evento ha finalizado.",
    reconnecting: "Reconectando… El video continuará automáticamente.",
    paused: "El servicio está en pausa. Por favor, permanezca en esta página.",
    endedTitle: "Este evento ha terminado",
    watchReplay: "Ver la grabación",
    replayAvailable: "La grabación está disponible.",
    availableUntil: "Disponible hasta el {date}",
    replayProcessing: "La grabación se está preparando. Esta página se actualizará automáticamente.",
    replayExpired: "La grabación ya no está disponible.",
    replayUnavailable: "No hay ninguna grabación disponible para este evento.",
    capacityTitle: "Este evento está muy concurrido en este momento",
    capacityBody: "Entrará automáticamente en cuanto haya espacio. Mantenga esta página abierta.",
    capacityRetry: "Volviendo a intentarlo en {seconds} s",
    tryNow: "Intentar ahora",
    inAppTitle: "Para una mejor experiencia, abra esta página en su navegador",
    inAppIos: "Toque el menú (⋯ o el botón de compartir) y elija «Abrir en el navegador».",
    inAppAndroid: "Toque el menú (⋮) y elija «Abrir en el navegador», o use el botón de abajo.",
    inAppInvite: "Si recibió una invitación privada, abra el enlace original de la invitación en su navegador.",
    openInBrowser: "Abrir en el navegador",
    copyLink: "Copiar enlace",
    linkCopied: "Enlace copiado",
    dismiss: "Cerrar",
    errorTitle: "No pudimos cargar este evento",
    errorBody: "Compruebe su conexión e inténtelo de nuevo.",
    retry: "Intentar de nuevo",
    help: "Contactar con soporte",
    status: "Estado del servicio",
    invitationInvalid: "Este enlace de invitación no es válido. Aún puede continuar abajo.",
    captionsOn: "Activar subtítulos",
    captionsOff: "Desactivar subtítulos",
  },
  fr: {
    language: "Langue",
    readAloud: "Lire à voix haute",
    stopReading: "Arrêter la lecture",
    live: "En direct",
    registerPrompt: "Saisissez votre nom pour commencer à regarder.",
    livePlaying: "La diffusion est en direct.",
    preEventTitle: "Le service n'a pas encore commencé",
    preEventBody: "Le service commencera ici automatiquement.",
    yourTime: "Votre heure",
    windowClosedTitle: "Cet événement est terminé",
    windowClosedBody: "La période de visionnage prévue pour cet événement est terminée.",
    reconnecting: "Reconnexion… La vidéo reprendra automatiquement.",
    paused: "Le service est en pause. Veuillez rester sur cette page.",
    endedTitle: "Cet événement est terminé",
    watchReplay: "Regarder la rediffusion",
    replayAvailable: "La rediffusion est disponible.",
    availableUntil: "Disponible jusqu'au {date}",
    replayProcessing: "La rediffusion est en cours de préparation. Cette page se mettra à jour automatiquement.",
    replayExpired: "La rediffusion n'est plus disponible.",
    replayUnavailable: "Aucun enregistrement n'est disponible pour cet événement.",
    capacityTitle: "Cet événement est très fréquenté en ce moment",
    capacityBody: "Vous rejoindrez automatiquement dès qu'une place se libère. Veuillez garder cette page ouverte.",
    capacityRetry: "Nouvel essai dans {seconds} s",
    tryNow: "Réessayer maintenant",
    inAppTitle: "Pour une meilleure expérience, ouvrez cette page dans votre navigateur",
    inAppIos: "Touchez le menu (⋯ ou le bouton de partage) puis choisissez « Ouvrir dans le navigateur ».",
    inAppAndroid: "Touchez le menu (⋮) puis choisissez « Ouvrir dans le navigateur », ou utilisez le bouton ci-dessous.",
    inAppInvite: "Si vous avez reçu une invitation privée, ouvrez le lien d'invitation d'origine dans votre navigateur.",
    openInBrowser: "Ouvrir dans le navigateur",
    copyLink: "Copier le lien",
    linkCopied: "Lien copié",
    dismiss: "Fermer",
    errorTitle: "Impossible de charger cet événement",
    errorBody: "Vérifiez votre connexion et réessayez.",
    retry: "Réessayer",
    help: "Contacter l'assistance",
    status: "État du service",
    invitationInvalid: "Ce lien d'invitation n'est pas valide. Vous pouvez tout de même continuer ci-dessous.",
    captionsOn: "Activer les sous-titres",
    captionsOff: "Désactiver les sous-titres",
  },
  de: {
    language: "Sprache",
    readAloud: "Vorlesen",
    stopReading: "Vorlesen beenden",
    live: "Live",
    registerPrompt: "Geben Sie Ihren Namen ein, um zuzusehen.",
    livePlaying: "Die Übertragung läuft live.",
    preEventTitle: "Die Übertragung hat noch nicht begonnen",
    preEventBody: "Die Übertragung beginnt hier automatisch.",
    yourTime: "Ihre Uhrzeit",
    windowClosedTitle: "Diese Veranstaltung ist beendet",
    windowClosedBody: "Der geplante Zeitraum zum Ansehen dieser Veranstaltung ist vorbei.",
    reconnecting: "Verbindung wird wiederhergestellt … Das Video läuft automatisch weiter.",
    paused: "Die Übertragung ist pausiert. Bitte bleiben Sie auf dieser Seite.",
    endedTitle: "Diese Veranstaltung ist beendet",
    watchReplay: "Aufzeichnung ansehen",
    replayAvailable: "Die Aufzeichnung ist verfügbar.",
    availableUntil: "Verfügbar bis {date}",
    replayProcessing: "Die Aufzeichnung wird vorbereitet. Diese Seite aktualisiert sich automatisch.",
    replayExpired: "Die Aufzeichnung ist nicht mehr verfügbar.",
    replayUnavailable: "Für diese Veranstaltung ist keine Aufzeichnung verfügbar.",
    capacityTitle: "Diese Veranstaltung ist gerade sehr gefragt",
    capacityBody: "Sie werden automatisch verbunden, sobald Platz frei ist. Bitte lassen Sie diese Seite geöffnet.",
    capacityRetry: "Neuer Versuch in {seconds} s",
    tryNow: "Jetzt versuchen",
    inAppTitle: "Öffnen Sie diese Seite für die beste Darstellung in Ihrem Browser",
    inAppIos: "Tippen Sie auf das Menü (⋯ oder die Teilen-Taste) und wählen Sie „Im Browser öffnen“.",
    inAppAndroid: "Tippen Sie auf das Menü (⋮) und wählen Sie „Im Browser öffnen“, oder nutzen Sie die Schaltfläche unten.",
    inAppInvite: "Wenn Sie eine private Einladung erhalten haben, öffnen Sie den ursprünglichen Einladungslink in Ihrem Browser.",
    openInBrowser: "Im Browser öffnen",
    copyLink: "Link kopieren",
    linkCopied: "Link kopiert",
    dismiss: "Schließen",
    errorTitle: "Diese Veranstaltung konnte nicht geladen werden",
    errorBody: "Bitte prüfen Sie Ihre Verbindung und versuchen Sie es erneut.",
    retry: "Erneut versuchen",
    help: "Support kontaktieren",
    status: "Dienststatus",
    invitationInvalid: "Dieser Einladungslink ist ungültig. Sie können unten trotzdem fortfahren.",
    captionsOn: "Untertitel einschalten",
    captionsOff: "Untertitel ausschalten",
  },
  pt: {
    language: "Idioma",
    readAloud: "Ler em voz alta",
    stopReading: "Parar leitura",
    live: "Ao vivo",
    registerPrompt: "Digite seu nome para começar a assistir.",
    livePlaying: "A transmissão está ao vivo.",
    preEventTitle: "O serviço ainda não começou",
    preEventBody: "O serviço começará aqui automaticamente.",
    yourTime: "Seu horário",
    windowClosedTitle: "Este evento terminou",
    windowClosedBody: "O horário previsto para assistir a este evento terminou.",
    reconnecting: "Reconectando… O vídeo continuará automaticamente.",
    paused: "O serviço está pausado. Por favor, permaneça nesta página.",
    endedTitle: "Este evento terminou",
    watchReplay: "Assistir à gravação",
    replayAvailable: "A gravação está disponível.",
    availableUntil: "Disponível até {date}",
    replayProcessing: "A gravação está sendo preparada. Esta página será atualizada automaticamente.",
    replayExpired: "A gravação não está mais disponível.",
    replayUnavailable: "Nenhuma gravação está disponível para este evento.",
    capacityTitle: "Este evento está muito movimentado no momento",
    capacityBody: "Você entrará automaticamente assim que houver espaço. Mantenha esta página aberta.",
    capacityRetry: "Tentando novamente em {seconds} s",
    tryNow: "Tentar agora",
    inAppTitle: "Para uma melhor experiência, abra esta página no seu navegador",
    inAppIos: "Toque no menu (⋯ ou no botão de compartilhar) e escolha “Abrir no navegador”.",
    inAppAndroid: "Toque no menu (⋮) e escolha “Abrir no navegador”, ou use o botão abaixo.",
    inAppInvite: "Se você recebeu um convite privado, abra o link original do convite no seu navegador.",
    openInBrowser: "Abrir no navegador",
    copyLink: "Copiar link",
    linkCopied: "Link copiado",
    dismiss: "Fechar",
    errorTitle: "Não foi possível carregar este evento",
    errorBody: "Verifique sua conexão e tente novamente.",
    retry: "Tentar novamente",
    help: "Falar com o suporte",
    status: "Status do serviço",
    invitationInvalid: "Este link de convite não é válido. Você ainda pode continuar abaixo.",
    captionsOn: "Ativar legendas",
    captionsOff: "Desativar legendas",
  },
  hi: {
    language: "भाषा",
    readAloud: "पढ़कर सुनाएं",
    stopReading: "पढ़ना बंद करें",
    live: "लाइव",
    registerPrompt: "देखना शुरू करने के लिए अपना नाम दर्ज करें।",
    livePlaying: "प्रसारण लाइव है।",
    preEventTitle: "सेवा अभी शुरू नहीं हुई है",
    preEventBody: "सेवा यहीं अपने आप शुरू हो जाएगी।",
    yourTime: "आपका समय",
    windowClosedTitle: "यह कार्यक्रम समाप्त हो गया है",
    windowClosedBody: "इस कार्यक्रम को देखने का निर्धारित समय समाप्त हो गया है।",
    reconnecting: "फिर से कनेक्ट हो रहा है… वीडियो अपने आप जारी रहेगा।",
    paused: "सेवा रुकी हुई है। कृपया इसी पेज पर बने रहें।",
    endedTitle: "यह कार्यक्रम समाप्त हो गया है",
    watchReplay: "रिकॉर्डिंग देखें",
    replayAvailable: "रिकॉर्डिंग उपलब्ध है।",
    availableUntil: "{date} तक उपलब्ध",
    replayProcessing: "रिकॉर्डिंग तैयार की जा रही है। यह पेज अपने आप अपडेट हो जाएगा।",
    replayExpired: "रिकॉर्डिंग अब उपलब्ध नहीं है।",
    replayUnavailable: "इस कार्यक्रम की कोई रिकॉर्डिंग उपलब्ध नहीं है।",
    capacityTitle: "इस समय इस कार्यक्रम में बहुत भीड़ है",
    capacityBody: "जगह मिलते ही आप अपने आप जुड़ जाएंगे। कृपया यह पेज खुला रखें।",
    capacityRetry: "{seconds} सेकंड में फिर से कोशिश",
    tryNow: "अभी कोशिश करें",
    inAppTitle: "बेहतर अनुभव के लिए, यह पेज अपने ब्राउज़र में खोलें",
    inAppIos: "मेनू (⋯ या शेयर बटन) पर टैप करें और “ब्राउज़र में खोलें” चुनें।",
    inAppAndroid: "मेनू (⋮) पर टैप करें और “ब्राउज़र में खोलें” चुनें, या नीचे दिया बटन इस्तेमाल करें।",
    inAppInvite: "अगर आपको निजी आमंत्रण भेजा गया है, तो मूल आमंत्रण लिंक अपने ब्राउज़र में खोलें।",
    openInBrowser: "ब्राउज़र में खोलें",
    copyLink: "लिंक कॉपी करें",
    linkCopied: "लिंक कॉपी हो गया",
    dismiss: "बंद करें",
    errorTitle: "हम यह कार्यक्रम लोड नहीं कर सके",
    errorBody: "कृपया अपना कनेक्शन जांचें और फिर से कोशिश करें।",
    retry: "फिर से कोशिश करें",
    help: "सहायता से संपर्क करें",
    status: "सेवा की स्थिति",
    invitationInvalid: "यह आमंत्रण लिंक मान्य नहीं है। आप फिर भी नीचे जारी रख सकते हैं।",
    captionsOn: "कैप्शन चालू करें",
    captionsOff: "कैप्शन बंद करें",
  },
};

/** The first supported language among the browser's preferences, else English. */
// eslint-disable-next-line react-refresh/only-export-components
export function detectLanguage(preferences) {
  const prefs = preferences
    || (typeof navigator !== "undefined" ? (navigator.languages?.length ? navigator.languages : [navigator.language]) : []);
  for (const tag of prefs) {
    const base = String(tag || "").toLowerCase().split("-")[0];
    if (SUPPORTED.has(base)) return base;
  }
  return "en";
}

function initialLanguage() {
  try {
    const saved = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (saved && SUPPORTED.has(saved)) return saved;
  } catch { /* storage blocked: fall through to the browser's language */ }
  return detectLanguage();
}

const format = (text, vars) =>
  vars ? text.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? `{${k}}`)) : text;

// eslint-disable-next-line react-refresh/only-export-components
export function translate(lang, key, vars) {
  const table = STRINGS[lang] || STRINGS.en;
  return format(table[key] ?? STRINGS.en[key] ?? key, vars);
}

const ViewerLanguageContext = createContext({
  lang: "en",
  setLang: () => {},
  t: (key, vars) => translate("en", key, vars),
});

export function ViewerLanguageProvider({ children }) {
  const [lang, setLangState] = useState(initialLanguage);
  const setLang = useCallback((code) => {
    if (!SUPPORTED.has(code)) return;
    try {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, code);
    } catch { /* the choice still applies to this visit */ }
    setLangState(code);
  }, []);

  // <html lang> follows the choice while the viewer page is open, then is put back.
  useEffect(() => {
    const root = document.documentElement;
    const previous = root.getAttribute("lang");
    root.setAttribute("lang", lang);
    return () => {
      if (previous == null) root.removeAttribute("lang");
      else root.setAttribute("lang", previous);
    };
  }, [lang]);

  const value = useMemo(() => ({ lang, setLang, t: (key, vars) => translate(lang, key, vars) }), [lang, setLang]);
  return <ViewerLanguageContext.Provider value={value}>{children}</ViewerLanguageContext.Provider>;
}

/** { lang, setLang, t }. Outside a provider (e.g. the player rendered on its own) it is
 *  English, so every consumer works unchanged anywhere it is used. */
// eslint-disable-next-line react-refresh/only-export-components
export function useViewerLanguage() {
  return useContext(ViewerLanguageContext);
}

/** "Saturday, 3 October 2026 at 14:00 GMT+5:30" in the viewer's own zone, and the same
 *  instant in UTC — §6.3's "local event time + persistent UTC". */
// eslint-disable-next-line react-refresh/only-export-components
export function formatEventTimes(iso, lang) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const local = new Intl.DateTimeFormat(lang, {
    weekday: "long", year: "numeric", month: "long", day: "numeric",
    hour: "2-digit", minute: "2-digit", timeZoneName: "short",
  }).format(d);
  const utc = new Intl.DateTimeFormat(lang, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    hour12: false, timeZone: "UTC",
  }).format(d);
  return { local, utc: `${utc} UTC` };
}
