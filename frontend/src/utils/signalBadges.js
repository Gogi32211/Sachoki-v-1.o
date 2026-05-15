// Shared signal badge color system.
// Colors derive from SuperchartPanel's existing chipCls mapping — do not invent new colors.

const PREUP_SET = new Set(['P2', 'P3', 'P50', 'P89', 'P55', 'P66'])

const NEUTRAL_CLS = 'bg-white/5 text-md-on-surface-var border-white/10'

export function normalizeSignal(s) {
  if (s == null) return ''
  return String(s).trim()
}

export function getSignalFamily(raw) {
  const s = normalizeSignal(raw)
  if (!s) return 'UNKNOWN'
  const u = s.toUpperCase()

  // ACTION decisions
  if (['BUY_READY','WATCH_CLOSELY','WAIT_CONFIRMATION','TOO_LATE','AVOID'].includes(u)) return 'ACTION'

  // ABR
  if (u.startsWith('ABR ') || u === 'ABR') return 'ABR'

  // EMA / explicit phrases
  if (u === 'EMA OK' || u === 'EMA50 RECLAIM') return 'EMA'

  // BEST/MATCH/STR & dashboard chips
  if (u === 'BEST★' || u === 'BEST↑' || u === 'BEST') return 'I'
  if (u === 'ABCD') return 'SETUP'
  if (u === 'STR' || u === 'STRONG') return 'I'
  if (u === 'MATCH') return 'I'
  if (/^V[×X]\d+/.test(u)) return 'I'
  if (/^Δ+↑/.test(s) || /^Δ+/.test(u)) return 'I'

  // GOG
  if (/^G[1-3][PLC]/.test(u) || u === 'GOG') return 'GOG'

  // FLY
  if (u === 'FLY') return 'FLY'

  // T / Z technical
  if (/^T\d/.test(u)) return 'T'
  if (/^Z\d/.test(u)) return 'Z'

  // PREUP -> Z color row
  if (PREUP_SET.has(u)) return 'Z'

  // F family (F1, F7, F8, F11, 4BF, FB0, FBO)
  if (/^F\d/.test(u) || u === '4BF' || u === 'FB0' || u === 'FBO' || u === 'FBO↑' || u === 'FBO↓') return 'F'

  // G family
  if (/^G\d/.test(u)) return 'G'

  // B family
  if (/^B\d/.test(u)) return 'B'

  // WLNBB / L family
  if (u === 'W' || u === 'L' || u === 'N' || u === 'B' || u === 'VB') return 'WLNBB'
  if (u.startsWith('FRI')) return 'L'
  if (u === 'BL' || u === 'BE' || u.startsWith('BE')) return 'L'
  if (u === 'CCI' || u === 'CCIB' || u === 'CCIOR' || u === 'CCI0R') return 'L'
  if (/^L\d/.test(u) || u === 'L2L4' || u === 'L555' || u === 'L22' || u === 'L88') return 'L'
  if (u === 'RL' || u === 'RH' || u === 'PP') return 'L'

  // Wick
  if (u.startsWith('WP') || u.startsWith('WC')) return 'WICK'

  // Setup tokens
  if (['A','SM','MX'].includes(u)) return 'SETUP'

  // I/Combo setup tokens
  if (['HILO↑','HILO↓','CONSO','SVS','ABS','LOAD','VBO↑','NS','CLM'].includes(u)) return 'I'
  if (u === 'ROCKET' || u === 'BUY') return 'I'

  // CTX tokens
  if (['LD','LDS','LDP','LRP','LDC','LRC','SQB','BCT','WRC','F8C'].includes(u)) return 'CTX'

  return 'UNKNOWN'
}

// Class mapping — must match SuperchartPanel exactly for T/Z/L/F/FLY/G/B/I/WICK/SETUP/GOG/CTX.
export function getSignalBadgeClass(raw) {
  const s = normalizeSignal(raw)
  if (!s) return NEUTRAL_CLS
  const u = s.toUpperCase()
  const fam = getSignalFamily(s)

  switch (fam) {
    case 'T':
      return 'bg-green-900 text-green-300'

    case 'Z':
      return PREUP_SET.has(u)
        ? 'bg-gray-700 text-white'
        : 'bg-red-900 text-red-300'

    case 'L': {
      if (u.startsWith('FRI'))                                   return 'bg-cyan-900 text-cyan-300'
      if (u === 'BL')                                            return 'bg-sky-900 text-sky-300'
      if (u === 'CCI' || u === 'CCI0R' || u === 'CCIOR' || u === 'CCIB')
                                                                 return 'bg-violet-900 text-violet-300'
      if (u === 'RL')                                            return 'bg-fuchsia-900 text-fuchsia-300'
      if (u === 'RH')                                            return 'bg-fuchsia-900 text-fuchsia-400'
      if (u === 'PP')                                            return 'bg-yellow-900 text-yellow-300'
      if (u === 'L555' || u === 'L22')                           return 'bg-rose-900 text-rose-300'
      if (u === 'L2L4')                                          return 'bg-sky-900 text-sky-400'
      if (u === 'L88')                                           return 'bg-violet-900 text-violet-200 font-bold'
      if (u.includes('BE'))                                      return 'bg-emerald-900 text-emerald-300'
      if (s.includes('↑'))                                       return 'bg-lime-900 text-lime-300'
      if (s.includes('↓'))                                       return 'bg-red-900 text-red-400'
      return 'bg-blue-900 text-blue-300'
    }

    case 'F':
      return 'bg-orange-900 text-orange-300'

    case 'FLY':
      return 'bg-purple-900 text-purple-200'

    case 'G':
      return 'bg-violet-900 text-violet-200'

    case 'B':
      return 'bg-amber-900 text-amber-300'

    case 'I': {
      if (u === 'ROCKET' || u === 'BUY')                                       return 'bg-green-900 text-green-200 font-bold'
      if (u === 'BEST★' || u === 'BEST↑' || u === '4BF')                       return 'bg-yellow-800 text-yellow-200 font-bold'
      if (u === 'STRONG' || u === 'STR')                                       return 'bg-emerald-900 text-emerald-200'
      if (u === 'MATCH')                                                       return 'bg-teal-900 text-teal-300'
      if (/^V[×X]\d+/.test(u))                                                 return 'bg-pink-900 text-pink-300 font-bold'
      if (s.includes('↑') || u === '3G' || ['NS','ABS','CLM','LOAD'].includes(u))
                                                                               return 'bg-lime-900 text-lime-300'
      if (s.includes('↓') || u === 'CONS' || u === '↓BIAS')                    return 'bg-red-900 text-red-300'
      return 'bg-teal-900 text-teal-300'
    }

    case 'WICK':
      return s.includes('↑') ? 'bg-sky-900 text-sky-300' : 'bg-red-900/50 text-red-300'

    case 'SETUP': {
      if (u === 'A')   return 'bg-orange-800/80 text-orange-100 ring-1 ring-orange-400 font-bold'
      if (u === 'SM')  return 'bg-lime-800/80 text-lime-100 ring-1 ring-lime-400 font-bold'
      if (u === 'N')   return 'bg-cyan-800/80 text-cyan-100 ring-1 ring-cyan-400 font-bold'
      if (u === 'MX')  return 'bg-pink-800/80 text-pink-100 ring-1 ring-pink-400 font-bold'
      if (u === 'ABCD')return 'bg-amber-800/80 text-amber-100 ring-1 ring-amber-400 font-bold'
      return 'bg-md-surface-high text-md-on-surface'
    }

    case 'GOG': {
      if (u.startsWith('G1P') || u.startsWith('G2P') || u.startsWith('G3P'))
        return 'bg-green-800 text-green-100 ring-1 ring-green-400 font-bold'
      if (u.startsWith('G1L') || u.startsWith('G2L') || u.startsWith('G3L'))
        return 'bg-emerald-800 text-emerald-100 ring-1 ring-emerald-400 font-bold'
      if (u.startsWith('G1C') || u.startsWith('G2C') || u.startsWith('G3C'))
        return 'bg-teal-800 text-teal-100 ring-1 ring-teal-400 font-bold'
      return 'bg-fuchsia-800 text-fuchsia-100 ring-1 ring-fuchsia-400 font-bold'
    }

    case 'CTX': {
      if (u === 'LDP' || u === 'LRP') return 'bg-green-900 text-green-200 font-semibold'
      if (u === 'LDC' || u === 'LRC') return 'bg-teal-900 text-teal-200'
      if (u === 'LDS' || u === 'LD')  return 'bg-cyan-900 text-cyan-300'
      if (u === 'SQB' || u === 'BCT') return 'bg-blue-900 text-blue-200'
      if (u === 'WRC' || u === 'F8C') return 'bg-slate-700 text-slate-200'
      return 'bg-md-surface-high text-md-on-surface'
    }

    case 'WLNBB': {
      if (u === 'W')  return 'bg-slate-700 text-slate-100 font-semibold'
      if (u === 'L')  return 'bg-sky-900 text-sky-200 font-semibold'
      if (u === 'N')  return 'bg-yellow-900 text-yellow-200 font-semibold'
      if (u === 'B')  return 'bg-orange-900 text-orange-200 font-semibold'
      if (u === 'VB') return 'bg-rose-900 text-rose-200 font-semibold'
      return NEUTRAL_CLS
    }

    case 'ACTION': {
      if (u === 'BUY_READY')         return 'bg-emerald-900/60 text-emerald-200 ring-1 ring-emerald-400 font-bold'
      if (u === 'WATCH_CLOSELY')     return 'bg-yellow-900/60 text-yellow-200 ring-1 ring-yellow-400 font-bold'
      if (u === 'WAIT_CONFIRMATION') return 'bg-blue-900/60 text-blue-200 ring-1 ring-blue-400 font-semibold'
      if (u === 'TOO_LATE')          return 'bg-amber-900/60 text-amber-200 ring-1 ring-amber-400'
      if (u === 'AVOID')             return 'bg-rose-900/60 text-rose-200 ring-1 ring-rose-400 font-bold'
      return NEUTRAL_CLS
    }

    case 'ABR': {
      // "ABR A" / "ABR B+" / "ABR B" / "ABR C" / "ABR R"
      const cat = u.replace(/^ABR\s*/, '')
      if (cat === 'A')   return 'bg-emerald-900/50 text-emerald-200'
      if (cat === 'B+')  return 'bg-cyan-900/50 text-cyan-200'
      if (cat === 'B')   return 'bg-blue-900/50 text-blue-200'
      if (cat === 'C')   return 'bg-md-surface-high text-md-on-surface-var'
      if (cat === 'R')   return 'bg-red-900/50 text-red-300'
      return NEUTRAL_CLS
    }

    case 'EMA':
      return 'bg-emerald-900/50 text-emerald-200'

    case 'UNKNOWN':
    default:
      return NEUTRAL_CLS
  }
}

export function getSignalLabel(s) {
  return normalizeSignal(s)
}

export function parseSignals(v) {
  if (v == null) return []
  if (Array.isArray(v)) return v.map(normalizeSignal).filter(Boolean)
  const s = String(v).trim()
  if (!s) return []
  // Comma or space-separated lists
  if (s.includes(',')) return s.split(',').map(normalizeSignal).filter(Boolean)
  return [s]
}

export function splitSignalSequence(s) {
  if (s == null) return []
  if (Array.isArray(s)) return s.map(normalizeSignal).filter(Boolean)
  return String(s)
    .split(/->|→/g)
    .map(normalizeSignal)
    .filter(Boolean)
}

export function parseSignalSequence(v) {
  if (v == null) return []
  if (Array.isArray(v)) return v.map(normalizeSignal).filter(Boolean)
  return splitSignalSequence(v)
}
