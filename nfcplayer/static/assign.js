// Polls /api/last_scan and surfaces freshly tapped cards in the banner.
(function () {
  const banner = document.getElementById('scan-banner');
  if (!banner) return;

  const idEl = document.getElementById('scan-id');
  const mapLink = document.getElementById('scan-map-link');   // index page
  const fillBtn = document.getElementById('scan-fill-btn');   // new-card form
  const cardInput = document.getElementById('card_id');
  let since = 0;
  let firstPoll = true;

  function poll() {
    fetch('/api/last_scan?since=' + since)
      .then(r => r.json())
      .then(data => {
        if (data.card_id !== undefined) {
          // Ignore scans that happened before the page was opened.
          if (!firstPoll || since === 0) {
            show(data);
          }
          since = data.seq;
        } else {
          since = data.seq;
        }
        firstPoll = false;
      })
      .catch(() => {});
  }

  function show(scan) {
    // On the index page only surface unknown cards; on the form, any card.
    if (mapLink && scan.known) return;
    idEl.textContent = scan.card_id;
    if (mapLink) mapLink.href = '/cards/new?card_id=' + encodeURIComponent(scan.card_id);
    if (fillBtn && cardInput) {
      fillBtn.onclick = function () { cardInput.value = scan.card_id; };
    }
    banner.hidden = false;
  }

  // Prime `since` with the current seq so old scans don't pop up, except the
  // server-rendered unknown-scan banner on the index page which is intended.
  fetch('/api/last_scan?since=0')
    .then(r => r.json())
    .then(data => { since = data.seq || 0; })
    .catch(() => {})
    .finally(() => setInterval(poll, 1000));
})();
