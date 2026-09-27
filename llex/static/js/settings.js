/**
 * Settings: paper, page margins and theme colours.
 *
 * Stored in `localStorage` as an app preference rather than in the document,
 * because they describe how this installation is set up, not what the document
 * contains. The page geometry the backend owns is applied separately.
 *
 * Paper size is expressed in inches throughout rather than centimetres, because
 * that is what CSS and the backend both speak, and because mixing the two in a
 * settings dialog is how a page ends up 2% wrong. A4 is stored as its exact
 * conversion (210mm / 25.4) rather than as a rounded 8.27, so a document printed
 * on real A4 is the size it claims to be.
 */

/** 96 CSS pixels per inch, matching the browser's definition of 1in. */
export const DPI = 96;

/** Paper sizes in millimetres, matching the backend's registry. */
export const PAPER_MM = {
  a3: [297, 420],
  a4: [210, 297],
  a5: [148, 210],
  b5: [176, 250],
  letter: [215.9, 279.4],
  legal: [215.9, 355.6],
  tabloid: [279.4, 431.8],
};

const MM_PER_INCH = 25.4;

const STORAGE_KEY = 'llex.settings.v1';

const FIELDS = [
  ['margin-top', 'marginTop', 'number'],
  ['margin-right', 'marginRight', 'number'],
  ['margin-bottom', 'marginBottom', 'number'],
  // The left margin used to be absent from this list while being present in the
  // defaults, so the control did nothing and the margin stayed at one inch.
  ['margin-left', 'marginLeft', 'number'],
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
  paper: 'letter',
  orientation: 'portrait',
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

/**
 * The page's width and height in inches, for a named paper and orientation.
 *
 * @param {string} paper Key of {@link PAPER_MM}.
 * @param {string} orientation `'portrait'` or `'landscape'`.
 * @returns {{width: number, height: number}} Falls back to Letter.
 */
export function paperSize(paper, orientation) {
  const millimetres = PAPER_MM[paper] || PAPER_MM.letter;
  const [short, long] = millimetres;
  return orientation === 'landscape'
    ? { width: long / MM_PER_INCH, height: short / MM_PER_INCH }
    : { width: short / MM_PER_INCH, height: long / MM_PER_INCH };
}

/** A short human description of a page, for the note under the controls. */
export function describePaper(paper, orientation) {
  const millimetres = PAPER_MM[paper];
  if (!millimetres) return 'Custom size';
  const label = paper === 'tabloid' ? 'Tabloid / Ledger' : paper.toUpperCase();
  const { width, height } = paperSize(paper, orientation);
  const mm = orientation === 'landscape' ? `${millimetres[1]} × ${millimetres[0]}` : `${millimetres[0]} × ${millimetres[1]}`;
  return `${label}, ${mm} mm (${width.toFixed(2)} × ${height.toFixed(2)} in), ${orientation}`;
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
 * @param {{onPageChanged: (geometry: object) => void, onZoomChanged?: (percent: number) => void}} hooks
 */
export function createSettings(hooks) {
  const modal = document.getElementById('settings-modal');
  const inputs = Object.fromEntries(FIELDS.map(([id, key]) => [key, document.getElementById(id)]));
  const paperSelect = document.getElementById('page-paper');
  const orientationSelect = document.getElementById('page-orientation');
  const sizeNote = document.getElementById('page-size-note');

  let settings = readStored();

  function geometry() {
    const { width, height } = paperSize(settings.paper, settings.orientation);
    return {
      width,
      height,
      paper: settings.paper,
      orientation: settings.orientation,
      margins: marginsInInches(width),
    };
  }

  function apply() {
    const root = document.documentElement;
    root.style.setProperty('--bg-dark', settings.themeBg);
    root.style.setProperty('--paper-bg', settings.themePaper);
    root.style.setProperty('--text-color', settings.themeText);
    root.style.setProperty('--ribbon-bg', settings.themeRibbon);
    root.style.setProperty('--border-color', settings.themeBorder);
    hooks.onPageChanged(geometry());
  }

  function marginsInInches(pageWidthInches = paperSize(settings.paper, settings.orientation).width) {
    return {
      top: clampMargin(settings.marginTop, pageWidthInches),
      right: clampMargin(settings.marginRight, pageWidthInches),
      bottom: clampMargin(settings.marginBottom, pageWidthInches),
      left: clampMargin(settings.marginLeft, pageWidthInches),
    };
  }

  function refreshNote() {
    if (sizeNote) sizeNote.textContent = describePaper(settings.paper, settings.orientation);
  }

  function fill() {
    for (const [id, key] of FIELDS) {
      const input = document.getElementById(id);
      if (input) input.value = String(settings[key]);
    }
    if (paperSelect) paperSelect.value = settings.paper;
    if (orientationSelect) orientationSelect.value = settings.orientation;
    refreshNote();
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
    if (paperSelect && PAPER_MM[paperSelect.value]) settings.paper = paperSelect.value;
    if (orientationSelect) settings.orientation = orientationSelect.value;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch {
      // Private browsing or a full quota: the settings still apply this session.
    }
    apply();
    close();
  }

  // The note updates as the controls change, so the size is visible before
  // committing rather than only after.
  paperSelect?.addEventListener('change', () => {
    settings.paper = paperSelect.value;
    refreshNote();
  });
  orientationSelect?.addEventListener('change', () => {
    settings.orientation = orientationSelect.value;
    refreshNote();
  });

  document.getElementById('menu-settings')?.addEventListener('click', open);
  document.getElementById('btn-settings-save')?.addEventListener('click', save);
  document.getElementById('btn-settings-cancel')?.addEventListener('click', close);

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && modal && !modal.hidden) close();
  });

  apply();
  return {
    open,
    close,
    save,
    apply,
    geometry,
    marginsInInches,
    get settings() {
      return settings;
    },
  };
}
