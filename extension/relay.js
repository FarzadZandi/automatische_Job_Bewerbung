// Runs on local pages, but only the jobfilter app page (it carries <meta name="jobfilter-app">) is used.
// It passes messages between that page and the extension, so nothing goes over the network.
(function () {
  if (window.__jfRelay || !document.querySelector('meta[name="jobfilter-app"]')) return;
  window.__jfRelay = true;

  // Reloading an unpacked extension invalidates the old content-script context while the local app
  // page can remain open. Never let that stale context surface an error in the page; a page refresh
  // loads this relay again from the new extension context.
  const send = (message) => {
    try {
      const pending = chrome.runtime.sendMessage(message);
      if (pending && typeof pending.catch === 'function') pending.catch(() => {});
    } catch (error) { /* extension was reloaded; reconnect on the next page refresh */ }
  };
  send({ type: 'jf-app-ready' });
  try {
    chrome.runtime.onMessage.addListener((message) => {
      if (message && message.type === 'jf-to-app') window.postMessage({ jfFromExtension: message.payload }, location.origin);
    });
  } catch (error) { /* extension was reloaded */ }
  window.addEventListener('message', (event) => {
    if (event.source !== window || event.origin !== location.origin || !event.data || !event.data.jfToExtension) return;
    send({ type: 'jf-from-app', payload: event.data.jfToExtension });
  });
})();
