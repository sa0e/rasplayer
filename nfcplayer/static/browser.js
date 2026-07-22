// Click-to-navigate file/folder picker for the card form, backed by /api/browse.
(function () {
  const list = document.getElementById('browser-list');
  const pathEl = document.getElementById('browser-path');
  const targetInput = document.getElementById('target');
  const modeSelect = document.getElementById('mode');
  const targetGroup = document.getElementById('target-group');
  if (!list) return;

  function isFolderMode() {
    return modeSelect.value === 'random1' || modeSelect.value === 'random3';
  }

  function updateVisibility() {
    targetGroup.hidden = modeSelect.value === 'stop';
  }

  function load(path) {
    fetch('/api/browse?path=' + encodeURIComponent(path))
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(render)
      .catch(() => { list.innerHTML = '<li>Could not load folder.</li>'; });
  }

  function render(data) {
    pathEl.textContent = '/' + (data.path || '');
    list.innerHTML = '';

    if (data.parent !== null) {
      addItem('⬆️ ..', () => load(data.parent), null);
    }
    data.dirs.forEach(d => {
      addItem('📁 ' + d.name, () => load(d.path),
        isFolderMode() ? () => pick(d.path) : null);
    });
    data.files.forEach(f => {
      if (isFolderMode()) {
        addItem('🎵 ' + f.name, null, null);
      } else {
        addItem('🎵 ' + f.name, () => pick(f.path), null);
      }
    });
    if (!list.children.length) {
      list.innerHTML = '<li>Empty folder.</li>';
    }
  }

  function addItem(text, onNavigate, onPick) {
    const li = document.createElement('li');
    const a = document.createElement('a');
    a.textContent = text;
    if (onNavigate) a.onclick = onNavigate;
    li.appendChild(a);
    if (onPick) {
      const pickA = document.createElement('a');
      pickA.textContent = '✔ use this folder';
      pickA.className = 'pick';
      pickA.onclick = onPick;
      li.appendChild(pickA);
    }
    if (!onNavigate && !onPick) a.style.color = '#aaa';
    list.appendChild(li);
  }

  function pick(path) {
    targetInput.value = path;
  }

  modeSelect.addEventListener('change', () => {
    updateVisibility();
    load(currentDirOfTarget());
  });

  function currentDirOfTarget() {
    const val = targetInput.value;
    if (!val) return '';
    return val.includes('/') ? val.slice(0, val.lastIndexOf('/')) : '';
  }

  updateVisibility();
  load(currentDirOfTarget());
})();
