/* ============================================================================
 * signal-badges.js — single source of truth for signal-label → CSS class.
 *
 * Verbatim port of frontend/src/utils/signalBadges.js (old Ultra). Every branch
 * preserves the exact label set and color decision the old UI made. The only
 * change: the class names returned are design-system classes defined in
 * signal-badges.css (which themselves consume design-tokens.css), so colors
 * are owned in ONE place (design-tokens.css), not embedded as Tailwind strings.
 *
 * Public API:
 *   normalizeSignal(s)        → trimmed string
 *   getSignalFamily(raw)      → 'T'|'Z'|'L'|'F'|'FLY'|'G'|'B'|'I'|'WICK'|'SETUP'|
 *                                'GOG'|'CTX'|'WLNBB'|'ACTION'|'ABR'|'EMA'|'UNKNOWN'
 *   getSignalBadgeClass(raw)  → CSS class string ("sig sig-t" / "sig sig-i-rocket")
 *   parseSignals(v)           → array of normalized labels
 *
 * Adding a new signal family:
 *   1. Add tokens to design-tokens.css (--sig-X-bg / --sig-X-fg [/ --sig-X-ring])
 *   2. Add the family class to signal-badges.css
 *   3. Add the family branch here
 *   Nothing else needs to change. The Super Chart, Ultra table, and any future
 *   consumers all read through this resolver.
 * ============================================================================ */

const PREUP_SET = new Set(['P2', 'P3', 'P50', 'P89', 'P55', 'P66']);
const NEUTRAL_CLS = 'sig sig-neutral';

function normalizeSignal(s) {
  if (s == null) return '';
  return String(s).trim();
}

function getSignalFamily(raw) {
  const s = normalizeSignal(raw);
  if (!s) return 'UNKNOWN';
  const u = s.toUpperCase();

  // ACTION decisions
  if (['BUY_READY', 'WATCH_CLOSELY', 'WAIT_CONFIRMATION', 'TOO_LATE', 'AVOID'].includes(u))
    return 'ACTION';

  // ABR
  if (u.startsWith('ABR ') || u === 'ABR') return 'ABR';

  // EMA / explicit phrases
  if (u === 'EMA OK' || u === 'EMA50 RECLAIM') return 'EMA';

  // BEST/MATCH/STR & dashboard chips
  if (u === 'BEST★' || u === 'BEST↑' || u === 'BEST') return 'I';
  if (u === 'ABCD') return 'SETUP';
  if (u === 'STR' || u === 'STRONG') return 'I';
  if (u === 'MATCH') return 'I';
  if (/^V[×X]\d+/.test(u)) return 'I';
  if (/^Δ+↑/.test(s) || /^Δ+/.test(u)) return 'I';

  // GOG tiers
  if (/^G[1-3][PLC]/.test(u) || u === 'GOG') return 'GOG';

  // FLY
  if (u === 'FLY') return 'FLY';

  // T / Z technical
  if (/^T\d/.test(u)) return 'T';
  if (/^Z\d/.test(u)) return 'Z';

  // PREUP → Z color row
  if (PREUP_SET.has(u)) return 'Z';

  // F family (F1, F7, F8, F11, 4BF, FB0, FBO)
  if (/^F\d/.test(u) || u === '4BF' || u === 'FB0' || u === 'FBO' || u === 'FBO↑' || u === 'FBO↓')
    return 'F';

  // G family
  if (/^G\d/.test(u)) return 'G';

  // B family
  if (/^B\d/.test(u)) return 'B';

  // WLNBB / L family
  if (u === 'W' || u === 'L' || u === 'N' || u === 'B' || u === 'VB') return 'WLNBB';
  if (u.startsWith('FRI')) return 'L';
  if (u === 'BL' || u === 'BE' || u.startsWith('BE')) return 'L';
  if (u === 'CCI' || u === 'CCIB' || u === 'CCIOR' || u === 'CCI0R') return 'L';
  if (/^L\d/.test(u) || u === 'L2L4' || u === 'L555' || u === 'L22' || u === 'L88') return 'L';
  if (u === 'RL' || u === 'RH' || u === 'PP') return 'L';

  // Wick
  if (u.startsWith('WP') || u.startsWith('WC')) return 'WICK';

  // Setup tokens
  if (['A', 'SM', 'MX'].includes(u)) return 'SETUP';

  // I/Combo setup tokens
  if (['HILO↑', 'HILO↓', 'CONSO', 'SVS', 'ABS', 'LOAD', 'VBO↑', 'NS', 'CLM'].includes(u)) return 'I';
  if (u === 'ROCKET' || u === 'BUY' || u === '🚀') return 'I';

  // CTX tokens
  if (['LD', 'LDS', 'LDP', 'LRP', 'LDC', 'LRC', 'SQB', 'BCT', 'WRC', 'F8C'].includes(u)) return 'CTX';

  return 'UNKNOWN';
}

function getSignalBadgeClass(raw) {
  const s = normalizeSignal(raw);
  if (!s) return NEUTRAL_CLS;
  const u = s.toUpperCase();
  const fam = getSignalFamily(s);

  switch (fam) {
    case 'T':
      return 'sig sig-t';

    case 'Z':
      return PREUP_SET.has(u) ? 'sig sig-preup' : 'sig sig-z';

    case 'L': {
      if (u.startsWith('FRI'))                                          return 'sig sig-l-fri';
      if (u === 'BL')                                                   return 'sig sig-l-bl';
      if (u === 'CCI' || u === 'CCI0R' || u === 'CCIOR' || u === 'CCIB')return 'sig sig-l-cci';
      if (u === 'RL')                                                   return 'sig sig-l-rl';
      if (u === 'RH')                                                   return 'sig sig-l-rh';
      if (u === 'PP')                                                   return 'sig sig-l-pp';
      if (u === 'L555' || u === 'L22')                                  return 'sig sig-l-rose';
      if (u === 'L2L4')                                                 return 'sig sig-l-l2l4';
      if (u === 'L88')                                                  return 'sig sig-l-l88';
      if (u.includes('BE'))                                             return 'sig sig-l-be';
      if (s.includes('↑'))                                              return 'sig sig-l-up';
      if (s.includes('↓'))                                              return 'sig sig-l-down';
      return 'sig sig-l';
    }

    case 'F':
      return 'sig sig-f';

    case 'FLY':
      return 'sig sig-fly';

    case 'G':
      return 'sig sig-g';

    case 'B':
      return 'sig sig-b';

    case 'I': {
      if (u === 'ROCKET' || u === 'BUY' || u === '🚀')                  return 'sig sig-i-rocket';
      if (u === 'BEST★' || u === 'BEST↑' || u === '4BF')                return 'sig sig-i-best';
      if (u === 'STRONG' || u === 'STR')                                return 'sig sig-i-strong';
      if (u === 'MATCH')                                                return 'sig sig-i-match';
      if (/^V[×X]\d+/.test(u))                                          return 'sig sig-i-vmul';
      if (s.includes('↑') || u === '3G' || ['NS','ABS','CLM','LOAD'].includes(u))
                                                                        return 'sig sig-i-up';
      if (s.includes('↓') || u === 'CONS' || u === '↓BIAS')             return 'sig sig-i-down';
      return 'sig sig-i';
    }

    case 'WICK':
      return s.includes('↑') ? 'sig sig-wick-up' : 'sig sig-wick-down';

    case 'SETUP': {
      if (u === 'A')    return 'sig sig-setup-a';
      if (u === 'SM')   return 'sig sig-setup-sm';
      if (u === 'N')    return 'sig sig-setup-n';
      if (u === 'MX')   return 'sig sig-setup-mx';
      if (u === 'ABCD') return 'sig sig-setup-abcd';
      return 'sig sig-setup-default';
    }

    case 'GOG': {
      if (u.startsWith('G1P') || u.startsWith('G2P') || u.startsWith('G3P')) return 'sig sig-gog-p';
      if (u.startsWith('G1L') || u.startsWith('G2L') || u.startsWith('G3L')) return 'sig sig-gog-l';
      if (u.startsWith('G1C') || u.startsWith('G2C') || u.startsWith('G3C')) return 'sig sig-gog-c';
      return 'sig sig-gog-base';
    }

    case 'CTX': {
      if (u === 'LDP' || u === 'LRP') return 'sig sig-ctx-ldp';
      if (u === 'LDC' || u === 'LRC') return 'sig sig-ctx-ldc';
      if (u === 'LDS' || u === 'LD')  return 'sig sig-ctx-lds';
      if (u === 'SQB' || u === 'BCT') return 'sig sig-ctx-sqb';
      if (u === 'WRC' || u === 'F8C') return 'sig sig-ctx-wrc';
      return 'sig sig-ctx-default';
    }

    case 'WLNBB': {
      if (u === 'W')  return 'sig sig-wlnbb-w';
      if (u === 'L')  return 'sig sig-wlnbb-l';
      if (u === 'N')  return 'sig sig-wlnbb-n';
      if (u === 'B')  return 'sig sig-wlnbb-b';
      if (u === 'VB') return 'sig sig-wlnbb-vb';
      return NEUTRAL_CLS;
    }

    case 'ACTION': {
      if (u === 'BUY_READY')         return 'sig sig-action-buy';
      if (u === 'WATCH_CLOSELY')     return 'sig sig-action-watch';
      if (u === 'WAIT_CONFIRMATION') return 'sig sig-action-wait';
      if (u === 'TOO_LATE')          return 'sig sig-action-late';
      if (u === 'AVOID')             return 'sig sig-action-avoid';
      return NEUTRAL_CLS;
    }

    case 'ABR': {
      const cat = u.replace(/^ABR\s*/, '');
      if (cat === 'A')  return 'sig sig-abr-a';
      if (cat === 'B+') return 'sig sig-abr-bp';
      if (cat === 'B')  return 'sig sig-abr-b';
      if (cat === 'C')  return 'sig sig-abr-c';
      if (cat === 'R')  return 'sig sig-abr-r';
      return NEUTRAL_CLS;
    }

    case 'EMA':
      return 'sig sig-ema';

    case 'UNKNOWN':
    default:
      return NEUTRAL_CLS;
  }
}

function parseSignals(v) {
  if (v == null) return [];
  if (Array.isArray(v)) return v.map(normalizeSignal).filter(Boolean);
  const s = String(v).trim();
  if (!s) return [];
  if (s.includes(',')) return s.split(',').map(normalizeSignal).filter(Boolean);
  return s.split(/\s+/).map(normalizeSignal).filter(Boolean);
}

// Render helper used across Super Chart + Ultra table — keeps badge rendering
// in one place so tooltip / accessibility additions land everywhere at once.
function renderSignalBadge(label, opts) {
  const text = normalizeSignal(label);
  if (!text) return '';
  const cls = getSignalBadgeClass(text);
  const title = (opts && opts.title) ? ` title="${escapeHtml(opts.title)}"` : '';
  return `<span class="${cls}"${title}>${escapeHtml(text)}</span>`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Expose globals (no module system in this dashboard frontend).
window.SignalBadges = {
  normalizeSignal,
  getSignalFamily,
  getSignalBadgeClass,
  parseSignals,
  renderSignalBadge,
  PREUP_SET,
};
