// Bluetooth speaker section on the Settings page.
(function () {
  const current = document.getElementById('bt-current');
  if (!current) return;

  const scanBtn = document.getElementById('bt-scan-btn');
  const forgetBtn = document.getElementById('bt-forget-btn');
  const results = document.getElementById('bt-results');
  const backendWarn = document.getElementById('bt-backend-warn');

  function refresh() {
    fetch('/api/bt/status')
      .then(r => r.json())
      .then(s => {
        backendWarn.hidden = !!s.error || s.backend !== null;
        if (s.error) {
          current.textContent = '⚠ ' + s.error;
          forgetBtn.hidden = true;
          scanBtn.disabled = true;
          return;
        }
        if (!s.mac) {
          current.textContent = 'No speaker configured — the system default output is used.';
          forgetBtn.hidden = true;
          return;
        }
        current.textContent =
          (s.connected ? '🔊 Connected: ' : '🔇 Saved, not connected: ') +
          (s.name || s.mac) + '  [' + s.mac + ']';
        forgetBtn.hidden = false;
      })
      .catch(() => {});
  }

  scanBtn.addEventListener('click', () => {
    scanBtn.disabled = true;
    scanBtn.textContent = 'Scanning… (~10 s)';
    results.innerHTML = '';
    fetch('/api/bt/scan', {method: 'POST'})
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          results.innerHTML = '<li>⚠ ' + data.error + '</li>';
          return;
        }
        if (!data.devices.length) {
          results.innerHTML = '<li>No devices found. Put the speaker in pairing mode and scan again.</li>';
          if (data.diagnostics) {
            const d = data.diagnostics;
            const diag = document.createElement('li');
            const pre = document.createElement('div');
            pre.className = 'bt-diag';
            pre.textContent =
              'app v' + d.app_version +
              ' · ' + d.bluetoothctl_version +
              ' (' + d.bluetoothctl + ')' +
              '\n' + d.controller +
              '\nrfkill: ' + d.rfkill +
              '\naudio backend: ' + (d.backend || 'none detected');
            diag.appendChild(pre);
            results.appendChild(diag);
          }
          return;
        }
        data.devices.forEach(d => {
          const li = document.createElement('li');
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.textContent = d.connected ? 'Reconnect' : 'Connect';
          btn.onclick = () => connect(d.mac, btn);
          const icon = d.icon && d.icon.indexOf('audio') === 0 ? '🔊 ' : '📶 ';
          li.append(icon + d.name + ' [' + d.mac + '] ', btn);
          results.appendChild(li);
        });
      })
      .catch(() => { results.innerHTML = '<li>Scan failed.</li>'; })
      .finally(() => {
        scanBtn.disabled = false;
        scanBtn.textContent = 'Scan for speakers';
      });
  });

  function connect(mac, btn) {
    btn.disabled = true;
    btn.textContent = 'Connecting…';
    fetch('/api/bt/connect', {method: 'POST', body: new URLSearchParams({mac})})
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          btn.disabled = false;
          btn.textContent = 'Connect';
          alert(data.error);
        } else {
          results.innerHTML = '';
        }
        refresh();
      });
  }

  forgetBtn.addEventListener('click', () => {
    if (!confirm('Forget this speaker?')) return;
    fetch('/api/bt/forget', {method: 'POST'}).then(refresh);
  });

  refresh();
  setInterval(refresh, 5000);
})();
