/**
 * Settings: page margins and theme colours.
 *
 * Stored in `localStorage` as an app preference rather than in the document,
 * because they describe how this installation is set up, not what the document
 * contains. The page geometry the backend owns is applied separately.
 */

const STORAGE_KEY = 'llex.settings.v1';

const FIELDS = [
  ['margin-top', 'marginTop', 'number'],
  ['margin-right', 'marginRight', 'number'],
  ['margin-bottom', 'marginBottom', 'number'],
  ['theme-bg', 'themeBg', 'color'],
  ['theme-paper', 'themePaper', 'color'],
  ['theme-text', 'themeText', 'color'],
  ['theme-ribbon', 'themeRibbon', 'color'],
  ['theme-border', 'themeBorder', 'color'],
];

const DEFAULTS = {
  marginTop: 1,
  marginRight: 1,
  marginBottom: 1,
  marginLeft: 1,
  themeBg: '#f0f0f0',
  themePaper: '#ffffff',
  themeText: '#000000',
  themeRibbon: '#2b2b2b',
  themeBorder: '#444444',
};

/** Clamp a margin to a range that still leaves a printable area. */
export function clampMargin(value, pageWidthInches = 8.5) {
  const numeric = Number.parseFloat(value);
  if (!Number.isFinite(numeric)) return 1;
  return Math.min(Math.max(numeric, 0.25), Math.max(0.25, pageWidthInches / 2 - 0.25));
}

function readStored() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULTS };
    const parsed = JSON.parse(raw);
    return { ...DEFAULTS, ...(parsed && typeof parsed === 'object' ? parsed : {}) };
  } catch {
    // A corrupt or unavailable store must not stop the editor from loading.
    return { ...DEFAULTS };
  }
}

/**
 * @param {{onMarginsChanged: (margins: object) => void, onZoomChanged?: (percent: number) => void}} hooks
 */
export function createSettings(hooks) {
  const modal = document.getElementById('settings-modal');
  const inputs = Object.fromEntries(
    FIELDS.map(([id, key]) => [key, document.getElementById(id)]),
  );

  let settings = readStored();

  function apply() {
    const root = document.documentElement;
    root.style.setProperty('--bg-dark', settings.themeBg);
    root.style.setProperty('--paper-bg', settings.themePaper);
    root.style.setProperty('--text-color', settings.themeText);
    root.style.setProperty('--ribbon-bg', settings.themeRibbon);
    root.style.setProperty('--border-color', settings.themeBorder);
    hooks.onMarginsChanged(marginsInInches());
  }

  function marginsInInches() {
    return {
      top: clampMargin(settings.marginTop),
      right: clampMargin(settings.marginRight),
      bottom: clampMargin(settings.marginBottom),
      left: clampMargin(settings.marginLeft),
    };
  }

  function fill() {
    for (const [id, key] of FIELDS) {
      const input = document.getElementById(id);
      if (input) input.value = String(settings[key]);
    }
  }

  function open() {
    fill();
    if (modal) {
      modal.hidden = false;
      modal.style.display = 'flex';
      document.getElementById('btn-settings-save')?.focus();
    }
  }

  function close() {
    if (modal) {
      modal.hidden = true;
      modal.style.display = 'none';
    }
  }

  function save() {
    for (const [id, key, type] of FIELDS) {
      const input = document.getElementById(id);
      if (!input) continue;
      const raw = input.value;
      settings[key] = type === 'number' ? clampMargin(raw) : raw;
    }
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch {
      // Private browsing or a full quota: the settings still apply this session.
    }
    apply();
    close();
  }

  document.getElementById('menu-settings')?.addEventListener('click', open);
  document.getElementById('btn-settings-save')?.addEventListener('click', save);
  document.getElementById('btn-settings-cancel')?.addEventListener('click', close);

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && modal && !modal.hidden) close();
  });

  apply();
  return { open, close, save, apply, marginsInInches, get settings() { return settings; } };
}
