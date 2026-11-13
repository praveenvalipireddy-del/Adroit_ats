// Clicking the toolbar icon opens the Job Hunt side panel.
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});
chrome.runtime.onStartup.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});

// LinkedIn message helper. Job Hunt's "Message on LinkedIn" opens the candidate's profile with #jh-li=<draft>;
// the draft is kept for that tab (this browser session only) and linkedin.js shows the Job Hunt box on the page.
const LI_PROFILE = /^https:\/\/([a-z]+\.)?linkedin\.com\/in\//;
const LI_ANY = /^https:\/\/([a-z]+\.)?linkedin\.com\//;
const liKey = (tabId) => 'jhLi' + tabId;

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  const url = info.url || '';
  const at = url.indexOf('#jh-li=');
  if (at >= 0 && LI_PROFILE.test(url)) {
    let d = null;
    try { d = JSON.parse(decodeURIComponent(url.slice(at + 7))); } catch (e) { d = null; }
    const p = d && parseInt(d.p, 10);
    if (p > 0 && typeof d.m === 'string' && d.m.trim()) {
      chrome.storage.session.set({ [liKey(tabId)]: { p, k: d.k === 'message' ? 'message' : 'note', m: d.m.slice(0, 2000), n: String(d.n || '').slice(0, 120) } });
    }
  }
  if (info.status === 'complete' && LI_ANY.test(tab.url || '')) {
    chrome.storage.session.get(liKey(tabId)).then((got) => {
      if (got[liKey(tabId)]) chrome.scripting.executeScript({ target: { tabId }, files: ['linkedin.js'] }).catch(() => {});
    });
  }
});
chrome.tabs.onRemoved.addListener((tabId) => { chrome.storage.session.remove(liKey(tabId)); });

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const tabId = sender.tab && sender.tab.id;
  if (!msg || tabId == null) return false;
  if (msg.type === 'jh-li-get') {
    chrome.storage.session.get(liKey(tabId)).then((got) => reply(got[liKey(tabId)] || null));
    return true;
  }
  if (msg.type === 'jh-li-done') {
    chrome.storage.session.remove(liKey(tabId)).then(() => reply(true));
    return true;
  }
  if (msg.type === 'jh-li-sent') {
    (async () => {
      const p = parseInt(msg.p, 10);
      const { server, key } = await chrome.storage.local.get(['server', 'key']);
      if (!server || !key) return reply({ error: 'Open the Job Hunt extension and connect it to Job Hunt first.' });
      if (!(p > 0)) return reply({ error: 'Unknown candidate.' });
      try {
        const res = await fetch(`${server}/api/ext/sourcing/${p}/linkedin-sent`, {
          method: 'POST', headers: { Authorization: 'Bearer ' + key, 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: msg.k === 'message' ? 'message' : 'note' }),
        });
        const data = await res.json().catch(() => ({}));
        reply(res.ok ? { ok: true } : { error: data.error || `Job Hunt error ${res.status}` });
      } catch (e) {
        reply({ error: 'Could not reach Job Hunt - check your internet and try again.' });
      }
    })();
    return true;
  }
  return false;
});
