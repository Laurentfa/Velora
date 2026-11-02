// Liaison Velora ↔ Ventiléa : Velora (par le relais de messagerie native) demande, l'extension exécute dans l'onglet Ventiléa déjà connecté.
// Rien n'est lu ni envoyé de sa propre initiative ; seul le site prescripteurs.ventilea.fr est concerné (voir manifest.json).
const HOST = 'fr.lfauvel.velora';
const ALLOWED = 'https://prescripteurs.ventilea.fr';

let port = null;

function connect() {
  try {
    port = chrome.runtime.connectNative(HOST);
  } catch (e) {
    port = null;
    return;
  }
  port.onMessage.addListener(onRequest);
  port.onDisconnect.addListener(() => { port = null; void chrome.runtime.lastError; setTimeout(connect, 5000); });
}

const isLogin = (url) => /login|connexion/.test(url || '');

async function findTab(base) {
  const tabs = await chrome.tabs.query({ url: base + '/*' });
  return tabs[0] || null;
}

async function waitLoaded(tabId) {
  for (let i = 0; i < 80; i++) {
    const t = await chrome.tabs.get(tabId);
    if (t.status === 'complete') return t;
    await new Promise((r) => setTimeout(r, 500));
  }
  return null;
}

async function state(tab) {
  if (!tab) return 'NOTAB';
  return isLogin(tab.url) ? 'LOGIN' : 'OK';
}

const commands = {
  async find({ base }) {
    return state(await findTab(base));
  },
  async ensure({ base }) {
    let tab = await findTab(base);
    if (!tab) {
      tab = await chrome.tabs.create({ url: base + '/patients', active: false });  // en arrière-plan : l'onglet affiché ne change pas
      tab = await waitLoaded(tab.id);
      if (!tab) return 'FAIL';
    }
    return state(tab);
  },
  async run({ base, code }) {
    const tab = await findTab(base);
    if (!tab) return 'NOTAB';
    const [res] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: 'MAIN',  // la page elle-même (comme l'ancien « execute javascript ») : window.__vq y est visible
      func: (c) => (0, eval)(c),
      args: [code],
    });
    const v = res && res.result;
    return v === undefined || v === null ? '' : String(v);
  },
};

async function onRequest(msg) {
  if (!msg || typeof msg.id !== 'number') return;
  const reply = { id: msg.id };
  try {
    if (msg.base !== ALLOWED) throw new Error('site non autorisé');
    const fn = commands[msg.cmd];
    if (!fn) throw new Error('commande inconnue');
    reply.result = await fn(msg);
  } catch (e) {
    reply.error = String((e && e.message) || e).slice(0, 200);
  }
  if (port) port.postMessage(reply);
}

connect();
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
