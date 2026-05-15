import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { createChart } from 'lightweight-charts'
import { api } from '../api'
import { pwlAdd, pwlHas, pwlRemove } from './PersonalWatchlistPanel'
import { idbGet, idbSet, getCacheBackend } from '../turboCache'
import ScannerDataGrid from './ScannerDataGrid'

// ── Universes ─────────────────────────────────────────────────────────────────
const UNIVERSES = [
  { key: 'sp500',     label: 'S&P 500',      desc: '~500 large-caps',     cls: 'text-blue-300'   },
  { key: 'nasdaq',    label: 'NASDAQ',        desc: '~4K NASDAQ stocks',   cls: 'text-cyan-300'   },
  { key: 'russell2k', label: 'Russell 2K',   desc: 'All US small-caps',   cls: 'text-orange-300' },
  { key: 'all_us',    label: '🌐 All US',    desc: '~8K tickers (Massive)',cls: 'text-violet-300' },
  { key: 'split',     label: '✂️ SPLIT',     desc: 'Reverse splits: −7d / +5d window',
                                                                            cls: 'text-amber-300'  },
]

// ── Timeframes ────────────────────────────────────────────────────────────────
const TF_OPTS = ['1wk', '1d', '4h', '1h']

// ── Score bands (multi-select) ────────────────────────────────────────────────
const SCORE_BANDS = [
  { key: 'all',   label: 'All'    },
  { key: '0-20',  label: '0–20',  min: 0,  max: 20  },
  { key: '21-40', label: '21–40', min: 21, max: 40  },
  { key: '41-60', label: '41–60', min: 41, max: 60  },
  { key: '61-80', label: '61–80', min: 61, max: 80  },
  { key: '81+',   label: '81–100',min: 81, max: 1e9 },
]

// ── Direction filter ──────────────────────────────────────────────────────────
const DIR_OPTS = [
  { key: 'all',  label: 'ALL'  },
  { key: 'bull', label: 'BULL' },
  { key: 'bear', label: 'BEAR' },
]

// ── All signal filters — grouped by engine family ─────────────────────────────
// divider:true = thin separator between groups
// custom: fn(row)→bool for computed filters
const SIG_GROUPS = [
  // ── VABS ──────────────────────────────────────────────────────────────
  { key: 'best_sig',   label: 'BEST★',  cls: 'text-lime-300'    },
  { key: 'strong_sig', label: 'STRONG', cls: 'text-emerald-300' },
  { key: 'vol_spike_20x', label: 'V×20', cls: 'text-red-300 font-bold' },
  { key: 'vol_spike_10x', label: 'V×10', cls: 'text-orange-300'  },
  { key: 'vol_spike_5x',  label: 'V×5',  cls: 'text-yellow-300'  },
  { key: 'vbo_up',     label: 'VBO↑',  cls: 'text-green-300'   },
  { key: 'abs_sig',    label: 'ABS',    cls: 'text-teal-300'    },
  { key: 'climb_sig',  label: 'CLB',    cls: 'text-cyan-300'    },
  { key: 'load_sig',   label: 'LD',     cls: 'text-blue-300'    },
  { divider: true },
  // ── Wyckoff (VABS legacy) ─────────────────────────────────────────────
  { key: 'ns',         label: 'NS',     cls: 'text-teal-300'    },
  { key: 'sq',         label: 'SQ',     cls: 'text-cyan-400'    },
  { key: 'sc',         label: 'SC',     cls: 'text-orange-300'  },
  { key: 'nd',         label: 'ND',     cls: 'text-pink-300'    },
  { divider: true },
  // ── Combo ─────────────────────────────────────────────────────────────
  { key: 'buy_2809',   label: 'BUY',    cls: 'text-lime-400'    },
  { key: 'rocket',     label: '🚀',     cls: 'text-red-300'     },
  { key: 'sig3g',      label: '3G',     cls: 'text-cyan-300'    },
  { key: 'rtv',        label: 'RTV',    cls: 'text-blue-300'    },
  { key: 'hilo_buy',   label: 'HILO↑', cls: 'text-green-300'   },
  { key: 'atr_brk',    label: 'ATR↑',  cls: 'text-emerald-300' },
  { key: 'bb_brk',     label: 'BB↑',   cls: 'text-teal-300'    },
  { key: 'va',         label: 'VA',    cls: 'text-lime-300'    },
  { key: 'bias_up',    label: '↑BIAS', cls: 'text-green-400'   },
  { key: 'um_2809',    label: 'UM',     cls: 'text-teal-300'    },
  { key: 'svs_2809',   label: 'SVS',    cls: 'text-orange-300'  },
  { key: 'conso_2809', label: 'CON',    cls: 'text-yellow-300'  },
  { divider: true },
  // ── F / G signals ─────────────────────────────────────────────────────
  { key: 'cd',  label: 'CD',  cls: 'text-lime-300'    },
  { key: 'ca',  label: 'CA',  cls: 'text-cyan-300'    },
  { key: 'cw',  label: 'CW',  cls: 'text-yellow-300'  },
  { key: 'any_f', label: 'ANY F', cls: 'text-amber-300'   },
  { key: 'f1',   label: 'F1',   cls: 'text-orange-400'  },
  { key: 'f2',   label: 'F2',   cls: 'text-md-on-surface'    },
  { key: 'f3',   label: 'F3',   cls: 'text-sky-300'     },
  { key: 'f4',   label: 'F4',   cls: 'text-md-on-surface'    },
  { key: 'f5',   label: 'F5',   cls: 'text-cyan-400'    },
  { key: 'f6',   label: 'F6',   cls: 'text-md-on-surface'    },
  { key: 'f7',   label: 'F7',   cls: 'text-green-400'   },
  { key: 'f8',   label: 'F8',   cls: 'text-blue-400'    },
  { key: 'f9',   label: 'F9',   cls: 'text-md-on-surface'    },
  { key: 'f10',  label: 'F10',  cls: 'text-lime-400'    },
  { key: 'f11',  label: 'F11',  cls: 'text-fuchsia-400' },
  { key: 'g1',  label: 'G1',  cls: 'text-lime-300'    },
  { key: 'g2',  label: 'G2',  cls: 'text-cyan-300'    },
  { key: 'g4',  label: 'G4',  cls: 'text-fuchsia-300' },
  { key: 'g6',  label: 'G6',  cls: 'text-orange-300'  },
  { key: 'g11',       label: 'G11',  cls: 'text-yellow-300'  },
  { key: 'seq_bcont', label: 'SBC',  cls: 'text-violet-300'  },
  { divider: true },
  // ── B signals (260321_B_BUILDER) — T/Z multi-bar sequences ───────────
  { key: '_any_b', label: 'ANY B', cls: 'text-amber-200 font-semibold',
    custom: r => !!(r.b1||r.b2||r.b3||r.b4||r.b5||r.b6||r.b7||r.b8||r.b9||r.b10||r.b11) },
  { key: 'b1',  label: 'B1',  cls: 'text-lime-300'    },
  { key: 'b2',  label: 'B2',  cls: 'text-cyan-300'    },
  { key: 'b3',  label: 'B3',  cls: 'text-teal-300'    },
  { key: 'b4',  label: 'B4',  cls: 'text-blue-300'    },
  { key: 'b5',  label: 'B5',  cls: 'text-green-300'   },
  { key: 'b6',  label: 'B6',  cls: 'text-emerald-300' },
  { key: 'b7',  label: 'B7',  cls: 'text-sky-300'     },
  { key: 'b8',  label: 'B8',  cls: 'text-indigo-300'  },
  { key: 'b9',  label: 'B9',  cls: 'text-violet-300'  },
  { key: 'b10', label: 'B10', cls: 'text-purple-300'  },
  { key: 'b11', label: 'B11', cls: 'text-fuchsia-300' },
  { divider: true },
  // ── T/Z ───────────────────────────────────────────────────────────────
  { key: '_tz_bull',   label: 'T/Z↑',   cls: 'text-violet-300',
    custom: r => !!r.tz_sig },
  { key: '_tz_strong', label: 'T4/T6',  cls: 'text-violet-200',
    custom: r => ['T4','T6','T1G','T2G'].includes(r.tz_sig) },
  { key: 'tz_bull_flip', label: 'TZ→3', cls: 'text-lime-300'    },
  { key: 'tz_attempt',   label: 'TZ→2', cls: 'text-cyan-300'    },
  { key: '_tz_weak', label: 'W', cls: 'text-yellow-300',
    custom: r => r.tz_weak_bull || r.tz_weak_bear },
  { divider: true },
  // ── WLNBB / L-signals ─────────────────────────────────────────────────
  { key: '_l_any',  label: 'L∗',  cls: 'text-blue-200',
    custom: r => r.l34 || r.l43 || r.be_up },
  { key: '_be_any', label: 'BE',  cls: 'text-emerald-200',
    custom: r => r.be_up || r.be_dn },
  { divider: true },
  { key: 'fri34',         label: 'FRI34',    cls: 'text-cyan-400'     },
  { key: 'fri43',         label: 'FRI43',    cls: 'text-sky-300'      },
  { key: 'fri64',         label: 'FRI64',    cls: 'text-indigo-300'   },
  { key: 'l34',           label: 'L34',      cls: 'text-blue-300'     },
  { key: 'l43',           label: 'L43',      cls: 'text-teal-300'     },
  { key: 'l64',           label: 'L64',      cls: 'text-orange-300'   },
  { key: 'l22',           label: 'L22',      cls: 'text-red-300'      },
  { key: 'l555',          label: 'L555',     cls: 'text-rose-400'     },
  { key: 'only_l2l4',     label: 'L2L4',     cls: 'text-sky-400'      },
  { key: 'blue',          label: 'BL',       cls: 'text-sky-300'      },
  { key: 'cci_ready',     label: 'CCI',      cls: 'text-violet-300'   },
  { key: 'cci_0_retest',  label: 'CCI0R',    cls: 'text-violet-400'   },
  { key: 'cci_blue_turn', label: 'CCIB',     cls: 'text-purple-300'   },
  { key: 'bo_up',         label: 'BO↑',      cls: 'text-lime-300'     },
  { key: 'bo_dn',         label: 'BO↓',      cls: 'text-red-400'      },
  { key: 'bx_up',         label: 'BX↑',      cls: 'text-lime-400'     },
  { key: 'bx_dn',         label: 'BX↓',      cls: 'text-red-400'      },
  { key: 'be_up',         label: 'BE↑',      cls: 'text-emerald-300'  },
  { key: 'be_dn',         label: 'BE↓',      cls: 'text-red-300'      },
  { key: 'fuchsia_rh',    label: 'RH',       cls: 'text-fuchsia-400'  },
  { key: 'fuchsia_rl',    label: 'RL',       cls: 'text-fuchsia-300'  },
  { key: 'pre_pump',      label: 'PP',       cls: 'text-yellow-400'   },
  { divider: true },
  // ── Wick / CISD ───────────────────────────────────────────────────────
  { key: 'x2g_wick',  label: 'X2G',  cls: 'text-cyan-300'    },
  { key: 'x2_wick',   label: 'X2',   cls: 'text-sky-300'     },
  { key: 'x1g_wick',  label: 'X1G',  cls: 'text-lime-300'    },
  { key: 'x1_wick',   label: 'X1',   cls: 'text-green-300'   },
  { key: 'x3_wick',   label: 'X3',   cls: 'text-yellow-300'  },
  { key: 'wick_bull', label: 'WK↑',  cls: 'text-emerald-400' },
  { divider: true },
  // ── ULTRA v2 ──────────────────────────────────────────────────────────
  { key: 'best_long',  label: 'BEST↑', cls: 'text-yellow-300'  },
  { key: 'fbo_bull',   label: 'FBO↑',  cls: 'text-sky-300'     },
  { key: 'fbo_bear',   label: 'FBO↓',  cls: 'text-red-400'     },
  { key: 'eb_bull',    label: 'EB↑',   cls: 'text-amber-300'   },
  { key: 'eb_bear',    label: 'EB↓',   cls: 'text-red-400'     },
  { key: 'bf_buy',     label: '4BF',    cls: 'text-pink-300'    },
  { key: 'bf_sell',    label: '4BF↓',   cls: 'text-red-400'    },
  { key: 'ultra_3up',  label: '3↑',     cls: 'text-lime-300'    },
  { divider: true },
  // ── 260308 + L88 ──────────────────────────────────────────────────────
  { key: 'sig_l88',    label: 'L88',    cls: 'text-violet-200'  },
  { key: 'sig_260308', label: '260308', cls: 'text-purple-300'  },
  { divider: true },
  // ── Delta / order-flow (260403) ────────────────────────────────────────
  { key: 'd_blast_bull',  label: 'ΔΔ↑',  cls: 'text-yellow-300'  },
  { key: 'd_surge_bull',  label: 'Δ↑',   cls: 'text-teal-300'    },
  { key: 'd_strong_bull', label: 'B/S↑', cls: 'text-lime-300'    },
  { key: 'd_absorb_bull', label: 'Ab↑',  cls: 'text-yellow-400'  },
  { key: 'd_spring',      label: 'dSPR', cls: 'text-lime-300'    },
  { key: 'd_div_bull',    label: 'T↓',   cls: 'text-cyan-300'    },
  { key: 'd_vd_div_bull', label: 'NS',   cls: 'text-teal-400'    },
  { key: 'd_cd_bull',     label: 'cd↑',  cls: 'text-sky-300'     },
  { key: 'd_flip_bull',       label: 'FLP↑',  cls: 'text-orange-300'  },
  { key: 'd_orange_bull',     label: 'ORG↑',  cls: 'text-orange-400'  },
  { key: 'd_blast_bull_red',  label: 'ΔΔ↑R',  cls: 'text-rose-300'    },
  { key: 'd_surge_bull_red',  label: 'Δ↑R',   cls: 'text-pink-300'    },
  { key: 'd_surge_bear_grn',  label: 'Δ↓G',   cls: 'text-red-400'     },
  { key: 'd_blast_bear_grn',  label: 'ΔΔ↓G',  cls: 'text-red-500'     },
  { key: 'd_vd_div_bear',     label: 'ND',     cls: 'text-red-400'     },
  { divider: true },
  // ── PREUP — EMA cross ↑ ──────────────────────────────────────────────
  { key: '_any_p', label: 'ANY P', cls: 'text-cyan-200 font-semibold',
    custom: r => !!(r.preup66 || r.preup55 || r.preup89 || r.preup3 || r.preup2 || r.preup50) },
  { key: 'preup66', label: 'P66', cls: 'text-lime-300'    },
  { key: 'preup55', label: 'P55', cls: 'text-emerald-300' },
  { key: 'preup89', label: 'P89', cls: 'text-teal-300'    },
  { key: 'preup3',  label: 'P3',  cls: 'text-cyan-300'    },
  { key: 'preup2',  label: 'P2',  cls: 'text-cyan-400'    },
  { key: 'preup50', label: 'P50', cls: 'text-sky-300'     },
  { divider: true },
  // ── PREDN — EMA drop ↓ ───────────────────────────────────────────────
  { key: '_any_d', label: 'ANY D', cls: 'text-red-300 font-semibold',
    custom: r => !!(r.predn66 || r.predn55 || r.predn89 || r.predn3 || r.predn2 || r.predn50) },
  { key: 'predn66', label: 'D66', cls: 'text-red-300'     },
  { key: 'predn55', label: 'D55', cls: 'text-red-400'     },
  { key: 'predn89', label: 'D89', cls: 'text-orange-400'  },
  { key: 'predn3',  label: 'D3',  cls: 'text-orange-300'  },
  { key: 'predn2',  label: 'D2',  cls: 'text-red-300'     },
  { key: 'predn50', label: 'D50', cls: 'text-orange-300'  },
  { divider: true },
  // ── Price vs EMA ─────────────────────────────────────────────────────
  { key: '_gt_ema200', label: 'P>200', cls: 'text-lime-300',
    custom: r => r.ema200 > 0 && r.last_price > r.ema200 },
  { key: '_gt_ema89',  label: 'P>89',  cls: 'text-emerald-300',
    custom: r => r.ema89  > 0 && r.last_price > r.ema89  },
  { key: '_gt_ema50',  label: 'P>50',  cls: 'text-teal-300',
    custom: r => r.ema50  > 0 && r.last_price > r.ema50  },
  { key: '_gt_ema20',  label: 'P>20',  cls: 'text-cyan-300',
    custom: r => r.ema20  > 0 && r.last_price > r.ema20  },
  { key: '_lt_ema20',  label: 'P<20',  cls: 'text-red-400',
    custom: r => r.ema20  > 0 && r.last_price < r.ema20  },
  { key: '_lt_ema50',  label: 'P<50',  cls: 'text-orange-400',
    custom: r => r.ema50  > 0 && r.last_price < r.ema50  },
  { key: '_lt_ema89',  label: 'P<89',  cls: 'text-orange-300',
    custom: r => r.ema89  > 0 && r.last_price < r.ema89  },
  { key: '_lt_ema200', label: 'P<200', cls: 'text-red-300',
    custom: r => r.ema200 > 0 && r.last_price < r.ema200 },
  { divider: true },
  // ── RS / Relative Strength ────────────────────────────────────────────
  { key: 'rs_strong',  label: 'RS+',    cls: 'text-lime-300'    },
  { key: 'rs',         label: 'RS',     cls: 'text-green-400'   },
  { divider: true },
  // ── PARA 260420 — Parabola Start Detector ─────────────────────────────
  { key: 'para_prep',   label: 'PREP',   cls: 'text-green-300'   },
  { key: 'para_start',  label: 'PARA',   cls: 'text-lime-300'    },
  { key: 'para_plus',   label: 'PARA+',  cls: 'text-cyan-300 font-semibold' },
  { key: 'para_retest', label: 'RETEST', cls: 'text-emerald-300' },
  { divider: true },
  // ── RGTI 260404 + SMX 260402 — multi-TF EMA ───────────────────────────
  { key: 'rgti_ll',       label: 'LL',   cls: 'text-purple-300'   },
  { key: 'rgti_up',       label: 'UP',   cls: 'text-blue-300'     },
  { key: 'rgti_upup',     label: '↑↑',   cls: 'text-fuchsia-300'  },
  { key: 'rgti_upupup',   label: '↑↑↑',  cls: 'text-sky-300'      },
  { key: 'rgti_orange',   label: 'ORG',  cls: 'text-orange-300'   },
  { key: 'rgti_green',    label: 'GRN',  cls: 'text-green-300'    },
  { key: 'rgti_greencirc',label: 'GC',   cls: 'text-emerald-300'  },
  { key: 'smx',           label: 'SMX',  cls: 'text-lime-300'     },
  { divider: true },
  // ── GOG Priority Engine (260501 FULL) ────────────────────────────────
  { key: 'akan_sig', label: 'A',    cls: 'text-orange-300 font-semibold'  },
  { key: 'smx_sig',  label: 'SM',   cls: 'text-lime-300 font-semibold'    },
  { key: 'nnn_sig',  label: 'N',    cls: 'text-cyan-300 font-semibold'    },
  { key: 'mx_sig',   label: 'MX',   cls: 'text-pink-300 font-semibold'    },
  { key: 'gog_sig',  label: 'GOG',  cls: 'text-fuchsia-300 font-semibold' },
  { key: 'gog_g1p',  label: 'G1P',  cls: 'text-green-300 font-bold'       },
  { key: 'gog_g2p',  label: 'G2P',  cls: 'text-green-400 font-bold'       },
  { key: 'gog_g3p',  label: 'G3P',  cls: 'text-green-500 font-bold'       },
  { key: 'gog_g1l',  label: 'G1L',  cls: 'text-emerald-300 font-semibold' },
  { key: 'gog_g2l',  label: 'G2L',  cls: 'text-emerald-400 font-semibold' },
  { key: 'gog_g3l',  label: 'G3L',  cls: 'text-emerald-500 font-semibold' },
  { key: 'gog_g1c',  label: 'G1C',  cls: 'text-teal-300 font-semibold'    },
  { key: 'gog_g2c',  label: 'G2C',  cls: 'text-teal-400 font-semibold'    },
  { key: 'gog_g3c',  label: 'G3C',  cls: 'text-teal-500 font-semibold'    },
  { key: '_gog_any_p', label: '★GOG+', cls: 'text-lime-300 font-bold',
    custom: r => !!(r.gog_g1p || r.gog_g2p || r.gog_g3p) },
  { key: '_not_ext', label: '!EXT', cls: 'text-sky-300',
    custom: r => !r.already_extended },
  { divider: true },
  // ── GOG Context Signals ───────────────────────────────────────────────
  { key: 'ctx_lds',  label: 'LDS',  cls: 'text-blue-300'    },
  { key: 'ctx_ldc',  label: 'LDC',  cls: 'text-blue-400'    },
  { key: 'ctx_ldp',  label: 'LDP',  cls: 'text-blue-200 font-semibold' },
  { key: 'ctx_lrc',  label: 'LRC',  cls: 'text-violet-300'  },
  { key: 'ctx_lrp',  label: 'LRP',  cls: 'text-violet-200 font-semibold' },
  { key: 'ctx_wrc',  label: 'WRC',  cls: 'text-indigo-300'  },
  { key: 'ctx_sqb',  label: 'SQB',  cls: 'text-cyan-300'    },
  { key: 'ctx_bct',  label: 'BCT',  cls: 'text-cyan-200 font-semibold' },
  { divider: true },
  // ── FLY 260424 — ABCD EMA DP ──────────────────────────────────────────
  { key: 'fly_abcd', label: 'ABCD', cls: 'text-lime-300 font-semibold' },
  { key: 'fly_cd',   label: 'CD',   cls: 'text-cyan-300'   },
  { key: 'fly_bd',   label: 'BD',   cls: 'text-blue-300'   },
  { key: 'fly_ad',   label: 'AD',   cls: 'text-violet-300' },
  { divider: true },
  // ── Context ───────────────────────────────────────────────────────────
  { key: '_rsi_os',    label: 'RSI≤35', cls: 'text-lime-300',
    custom: r => (r.rsi || 100) <= 35 },
  { key: '_rsi_ob',    label: 'RSI≥70', cls: 'text-red-400',
    custom: r => (r.rsi || 0) >= 70 },
  { key: '_yf_only',   label: 'yf',     cls: 'text-orange-400',
    custom: r => r.data_source === 'yfinance' },
  { divider: true },
  // ── Cross-engine diversity filters ────────────────────────────────────────
  { key: '_cross2',  label: '⚡×2+', cls: 'text-yellow-300',
    custom: r => engineFamilies(r).size >= 2 },
  { key: '_cross3',  label: '⚡×3+', cls: 'text-lime-300',
    custom: r => engineFamilies(r).size >= 3 },
  { key: '_cross4',  label: '⚡×4+', cls: 'text-lime-400',
    custom: r => engineFamilies(r).size >= 4 },
  { key: '_early',   label: '[E]',   cls: 'text-sky-300',
    custom: r => setupPhase(r) === 'Early' },
]

// ── T/Z weight map (for display colour) ──────────────────────────────────────
const TZ_STRONG = new Set(['T4','T6','T1G','T2G'])
const TZ_BEAR   = new Set(['Z4','Z6','Z1G','Z2G','Z1','Z2','Z3','Z5','Z7','Z9','Z10','Z11','Z12'])

// ── Colour helpers ────────────────────────────────────────────────────────────
function scoreColor(s) {
  if (s >= 65) return 'text-lime-300 font-bold'
  if (s >= 50) return 'text-yellow-300 font-semibold'
  if (s >= 35) return 'text-blue-300'
  if (s >= 20) return 'text-md-on-surface'
  return 'text-md-on-surface-var/70'
}

function scoreBg(s) {
  if (s >= 65) return 'bg-lime-900/25'
  if (s >= 50) return 'bg-yellow-900/15'
  if (s >= 35) return 'bg-blue-900/10'
  return ''
}

function betaZoneCls(zone) {
  switch (zone) {
    case 'ELITE':       return 'text-amber-200 font-bold'
    case 'OPTIMAL':     return 'text-emerald-300 font-bold'
    case 'BUY':         return 'text-blue-300 font-semibold'
    case 'WATCH':       return 'text-violet-300'
    case 'BUILDING':    return 'text-yellow-400'
    case 'EXTENDED':    return 'text-amber-400'
    case 'SHORT_WATCH': return 'text-red-400'
    default:            return 'text-md-on-surface-var/70'
  }
}

// ── Badge component ───────────────────────────────────────────────────────────
function Badge({ label, cls }) {
  return <span className={`px-1 rounded text-[10px] leading-tight ${cls}`}>{label}</span>
}

// ── fmt helper ────────────────────────────────────────────────────────────────
const fmt = (v, d = 2) => v == null ? '—' : Number(v).toFixed(d)

// ── GOG tier badge colour ─────────────────────────────────────────────────────
function gogTierCls(tier) {
  if (!tier) return ''
  if (tier === 'G1P' || tier === 'G2P' || tier === 'G3P')
    return 'bg-green-800 text-green-100 ring-1 ring-green-400 font-bold'
  if (tier === 'G1L' || tier === 'G2L' || tier === 'G3L')
    return 'bg-emerald-800 text-emerald-100 ring-1 ring-emerald-400'
  if (tier === 'G1C' || tier === 'G2C' || tier === 'G3C')
    return 'bg-teal-800 text-teal-100 ring-1 ring-teal-400'
  return 'bg-fuchsia-800 text-fuchsia-100 ring-1 ring-fuchsia-400'
}

// ── Context token colour ───────────────────────────────────────────────────────
function ctxTokCls(tok) {
  if (tok === 'LDP' || tok === 'LRP') return 'bg-green-900 text-green-200 font-semibold'
  if (tok === 'LDC' || tok === 'LRC') return 'bg-teal-900 text-teal-200'
  if (tok === 'LDS' || tok === 'LD')  return 'bg-cyan-900 text-cyan-300'
  if (tok === 'BCT')                  return 'bg-blue-900 text-blue-200 font-semibold'
  if (tok === 'SQB')                  return 'bg-blue-900 text-blue-300'
  if (tok === 'WRC' || tok === 'F8C') return 'bg-slate-700 text-slate-200'
  return 'bg-md-surface-high text-md-on-surface-var'
}

// ── Active context tokens from a turbo row (priority order) ───────────────────
const CTX_PRIO = [
  ['ctx_ldp','LDP'],['ctx_lrp','LRP'],
  ['ctx_ldc','LDC'],['ctx_lrc','LRC'],
  ['ctx_lds','LDS'],['ctx_ld','LD'],
  ['ctx_bct','BCT'],['ctx_sqb','SQB'],
  ['ctx_wrc','WRC'],['ctx_f8c','F8C'],['ctx_svs','SVS'],
]
function ctxTokens(r) {
  return CTX_PRIO.filter(([k]) => r[k]).map(([, t]) => t)
}

// ── SIGNAL_SCORE chip colour ───────────────────────────────────────────────────
function scoreCls(n) {
  if (n >= 120) return 'text-yellow-300 font-bold'
  if (n >= 100) return 'text-lime-300 font-bold'
  if (n >= 80)  return 'text-green-300 font-semibold'
  if (n >= 60)  return 'text-teal-300'
  return 'text-md-on-surface-var'
}

// ── Engine family membership (for cross-engine diversity scoring) ─────────────
// Each set represents a truly independent engine/observation model.
// Signals from different sets = orthogonal evidence.
// Signals from the same set = same-theme cluster (high conviction, lower diversity).
function engineFamilies(r) {
  const fams = new Set()
  // VABS (volume/accumulation state machine)
  if (r.best_sig || r.strong_sig || r.vbo_up || r.abs_sig || r.ns || r.sq || r.load_sig || r.va)
    fams.add('Vol')
  // Delta / order-flow (260403)
  if (r.d_spring || r.d_strong_bull || r.d_absorb_bull || r.d_blast_bull || r.d_surge_bull || r.d_flip_bull || r.d_orange_bull)
    fams.add('Δ')
  // T/Z candlestick state engine
  if (r.tz_sig || r.tz_bull_flip || r.tz_attempt)
    fams.add('T/Z')
  // WLNBB / L-structure / EMA-cross
  if (r.fri34 || r.fri43 || r.l34 || r.preup66 || r.preup55 || r.preup3 || r.preup2 || r.preup50)
    fams.add('L')
  // Combo/2809 (multi-condition composites)
  if (r.rocket || r.buy_2809 || r.seq_bcont)
    fams.add('Cmb')
  // Breakout / ULTRA / RS
  if (r.fbo_bull || r.eb_bull || r.rs_strong || r.ultra_3up)
    fams.add('Brk')
  return fams
}

// ── Setup timing phase ────────────────────────────────────────────────────────
// Early = phase C / fresh regime flip  (best risk/reward, needs more confirmation)
// Mid   = LPS / T4 / EMA cross        (structure confirmed, still actionable)
// Late  = confirmed breakout combo     (high conviction but entry may be extended)
function setupPhase(r) {
  const early = r.d_spring || r.tz_bull_flip
  const late  = (r.rocket || r.buy_2809) && (r.fbo_bull || r.eb_bull || r.vbo_up)
  if (early && !late) return 'Early'
  if (late)           return 'Late'
  return null  // mid / neutral — don't label
}

// ── Score reason — grouped by family, shown as tooltip + inline line ─────────
function scoreReason(r) {
  const parts = []

  // Vol/VABS family (VABS + Wyckoff share Vol cap but are sub-families)
  const vol = []
  if (r.best_sig)  vol.push('BEST★')
  else if (r.strong_sig) vol.push('STR')
  if (r.vbo_up)    vol.push('VBO↑')
  if (r.ns)        vol.push('NS')
  else if (r.sq)   vol.push('SQ')
  if (r.load_sig)  vol.push('LD')
  if (r.va)        vol.push('VA')
  if (r.sig_l88)   vol.push('L88')
  if (vol.length)  parts.push(`Vol:${vol.join('+')}`)

  // Breakout family
  const brk = []
  if (r.fbo_bull)   brk.push('FBO↑')
  if (r.eb_bull)    brk.push('EB↑')
  if (r.rs_strong)  brk.push('RS+')
  else if (r.rs)    brk.push('RS')
  if (r.ultra_3up)  brk.push('3↑')
  if (brk.length)   parts.push(`Brk:${brk.join('+')}`)

  // Combo family
  const cmb = []
  if (r.rocket)    cmb.push('🚀')
  else if (r.buy_2809) cmb.push('BUY')
  if (r.cd)        cmb.push('CD')
  else if (r.ca)   cmb.push('CA')
  else if (r.cw)   cmb.push('CW')
  if (r.seq_bcont) cmb.push('SBC')
  if (cmb.length)  parts.push(`Cmb:${cmb.join('+')}`)

  // Trend/TZ family
  const trd = []
  if (r.tz_sig)        trd.push(r.tz_sig)
  if (r.tz_bull_flip)  trd.push('TZ→3')
  else if (r.tz_attempt) trd.push('TZ→2')
  if (r.fri34)         trd.push('FRI34')
  else if (r.fri43)    trd.push('FRI43')
  else if (r.l34)      trd.push('L34')
  if (r.preup66)        trd.push('P66')
  else if (r.preup55)   trd.push('P55')
  else if (r.preup89)   trd.push('P89')
  else if (r.preup3)    trd.push('P3')
  else if (r.preup2)    trd.push('P2')
  else if (r.preup50)   trd.push('P50')
  if (trd.length)      parts.push(`Trd:${trd.join('+')}`)

  // Delta family
  const dlt = []
  if (r.d_spring)      dlt.push('dSPR')
  if (r.d_blast_bull)  dlt.push('ΔΔ↑')
  else if (r.d_surge_bull) dlt.push('Δ↑')
  if (r.d_strong_bull) dlt.push('B/S↑')
  if (r.d_absorb_bull) dlt.push('Ab↑')
  if (dlt.length)      parts.push(`Δ:${dlt.join('+')}`)

  // B/G signals (show first 4)
  const bg = []
  for (let i = 1; i <= 11; i++) { if (r[`b${i}`]) bg.push(`B${i}`) }
  for (const k of ['g1','g2','g4','g6','g11']) { if (r[k]) bg.push(k.toUpperCase()) }
  if (bg.length) parts.push(bg.slice(0, 4).join('+'))

  const sc = r.turbo_score ?? 0
  const tier = sc >= 65 ? '🔥' : sc >= 50 ? '★' : sc >= 35 ? '▲' : ''

  // Cross-engine diversity indicator — how many independent engine families fired
  const n = engineFamilies(r).size
  const cross = n >= 4 ? '⚡×4' : n === 3 ? '⚡×3' : n === 2 ? '⚡×2' : ''

  // Timing phase (Early setup vs Late/confirmed)
  const phase = setupPhase(r)
  const phaseTag = phase === 'Early' ? ' [E]' : phase === 'Late' ? ' [L]' : ''

  return parts.length
    ? `${tier}${cross ? ' ' + cross : ''} ${parts.join(' | ')}${phaseTag} → ${sc.toFixed(1)}`
    : `score ${sc.toFixed(1)}`
}

// ── Mini chart popup ──────────────────────────────────────────────────────────
function MiniChartPopup({ row, tf, pos, onClose }) {
  const containerRef = useRef(null)
  const chartRef     = useRef(null)
  const [loading, setLoading] = useState(true)
  const [info, setInfo] = useState(null)

  useEffect(() => {
    api.tickerInfo(row.ticker).then(setInfo).catch(() => {})
  }, [row.ticker])

  const CHART_W = 780
  const CHART_H = 380

  useEffect(() => {
    if (!containerRef.current) return
    const isIntraday = ['30m', '15m', '1h', '4h'].includes(tf)
    const chart = createChart(containerRef.current, {
      layout: { background: { color: '#030712' }, textColor: '#9ca3af' },
      grid: { vertLines: { color: '#1f2937' }, horzLines: { color: '#1f2937' } },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#374151' },
      timeScale: { borderColor: '#374151', timeVisible: isIntraday },
      width: CHART_W,
      height: CHART_H,
      handleScroll: false,
      handleScale: false,
    })
    const series = chart.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    })
    const volSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'vol',
      color: '#374151',
    })
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } })
    chartRef.current = chart

    api.signals(row.ticker, tf, 80)
      .then(rows => {
        const toTime = r => {
          const d = r.date ?? r.Datetime ?? r.Date
          if (!d) return null
          if (isIntraday) {
            const ms = new Date(String(d).replace(' ', 'T')).getTime()
            return isNaN(ms) ? null : Math.floor(ms / 1000)
          }
          return String(d).slice(0, 10)
        }
        const bars = rows
          .filter(r => r.close != null && toTime(r))
          .map(r => ({ time: toTime(r), open: Number(r.open), high: Number(r.high), low: Number(r.low), close: Number(r.close) }))
          .sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))
        const volumes = rows
          .filter(r => r.volume != null && toTime(r))
          .map(r => ({ time: toTime(r), value: Number(r.volume), color: '#374151' }))
          .sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))
        if (bars.length) {
          series.setData(bars)
          volSeries.setData(volumes)
          chart.timeScale().fitContent()
        }
        setLoading(false)
      })
      .catch(() => setLoading(false))

    return () => { try { chart.remove() } catch {} }
  }, [row.ticker, tf])

  // position: below the row, right side; flip left if too close to edge
  const POPUP_W = 820
  const POPUP_H = 520
  const vw = window.innerWidth
  const vh = window.innerHeight
  let left = pos.x + 16
  if (left + POPUP_W > vw - 8) left = pos.x - POPUP_W - 8
  let top = pos.y + 20   // start below the row center (≈ row bottom + gap)
  if (top + POPUP_H > vh - 8) top = vh - POPUP_H - 8
  if (top < 8) top = 8

  const chg = row.change_pct ?? 0

  return (
    <div
      className="fixed z-50 bg-md-surface-con border border-md-outline-var rounded-lg shadow-2xl text-xs text-md-on-surface pointer-events-none"
      style={{ left, top, width: POPUP_W }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/[0.07]">
        <div className="flex items-center gap-3 min-w-0">
          <span className="font-mono font-bold text-blue-300 text-base shrink-0">{row.ticker}</span>
          {row.vol_bucket && <span className="text-md-on-surface-var text-sm shrink-0">{row.vol_bucket}</span>}
          {row.tz_sig && (
            <span className={`font-mono font-semibold text-sm shrink-0 ${TZ_STRONG.has(row.tz_sig) ? 'text-lime-300' : TZ_BEAR.has(row.tz_sig) ? 'text-red-400' : 'text-blue-300'}`}>
              {row.tz_sig}
            </span>
          )}
          {info?.name && info.name !== row.ticker && (
            <span className="text-md-on-surface text-sm truncate">{info.name}</span>
          )}
          {info?.sector && (
            <span className="text-md-on-surface-var text-xs shrink-0 bg-md-surface-high px-1.5 py-0.5 rounded">{info.sector}</span>
          )}
        </div>
        <div className="text-right shrink-0 ml-3">
          <span className="font-mono text-md-on-surface text-base">${fmt(row.last_price)}</span>
          <span className={`ml-2 font-mono text-sm ${chg >= 0 ? 'text-lime-400' : 'text-red-400'}`}>
            {chg >= 0 ? '+' : ''}{fmt(chg)}%
          </span>
        </div>
      </div>

      {/* Stats row */}
      <div className="flex items-center gap-4 px-4 py-2 border-b border-white/[0.07] text-md-on-surface-var">
        <span>RSI <span className={row.rsi <= 35 ? 'text-lime-400' : row.rsi >= 70 ? 'text-red-400' : 'text-md-on-surface'}>{fmt(row.rsi, 0)}</span></span>
        <span>CCI <span className={row.cci >= 100 ? 'text-lime-400' : row.cci <= -100 ? 'text-red-400' : 'text-md-on-surface'}>{fmt(row.cci, 0)}</span></span>
        <span>Score <span className={`font-semibold ${scoreColor(row.turbo_score ?? 0)}`}>{fmt(row.turbo_score, 1)}</span></span>
        {row.avg_vol > 0 && (
          <span className="ml-auto">
            {row.avg_vol >= 1_000_000 ? `${(row.avg_vol/1_000_000).toFixed(1)}M` : row.avg_vol >= 1_000 ? `${Math.round(row.avg_vol/1_000)}K` : Math.round(row.avg_vol)}
          </span>
        )}
      </div>

      {/* Chart */}
      <div className="relative">
        <div ref={containerRef} style={{ width: CHART_W, height: CHART_H }} />
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-md-surface-con/70 text-md-on-surface-var">
            Loading…
          </div>
        )}
      </div>

      {/* Signal summary */}
      <div className="px-4 py-2 text-md-on-surface-var text-xs truncate border-t border-md-outline-var">
        {scoreReason(row)}
      </div>
    </div>
  )
}

// ── Turbo scan localStorage cache ─────────────────────────────────────────────
const _tsKey  = (tf, uni) => `sachoki_turbo_${tf}_${uni}`
const _tsGet  = (tf, uni) => { try { return JSON.parse(localStorage.getItem(_tsKey(tf, uni)) || 'null') } catch { return null } }

const _ALL_TF  = ['1d', '4h', '1h', '30m', '15m', '1wk']
const _ALL_UNI = ['sp500', 'nasdaq', 'russell2k', 'all_us', 'split']

// Evict ALL cached entries except the one being written
function _evictAll(exceptKey) {
  for (const tf of _ALL_TF) for (const uni of _ALL_UNI) {
    const key = _tsKey(tf, uni)
    if (key === exceptKey) continue
    try { localStorage.removeItem(key) } catch {}
  }
}

// Keep only fields needed for display — omit 0-valued booleans to save space
// sig_ages handled separately: only keep entries with age < 15 to cut size
const KEEP_ALWAYS = new Set([
  'ticker','turbo_score','turbo_score_n3','turbo_score_n5','turbo_score_n10',
  'tz_sig','tz_bull','last_price','change_pct','rsi','cci','avg_vol',
  'vol_bucket','data_source',
  'ema20','ema50','ema89','ema200',
  'sector',
  // RTB v4
  'rtb_build','rtb_turn','rtb_ready','rtb_bonus3',
  'rtb_late','rtb_total','rtb_phase','rtb_transition','rtb_phase_age',
  // BETA Score
  'beta_score','beta_raw','beta_setup','beta_momentum','beta_excess',
  'beta_zone','beta_auto_buy',
])
function _slimRow(r) {
  const out = {}
  for (const [k, v] of Object.entries(r)) {
    if (k.startsWith('_') || k === 'scan_id') continue
    if (k === 'sig_ages') {
      if (v) {
        try {
          const ages = JSON.parse(v)
          const compact = {}
          for (const [sk, sv] of Object.entries(ages)) {
            if (sv < 15) compact[sk] = sv
          }
          if (Object.keys(compact).length > 0) out[k] = JSON.stringify(compact)
        } catch {}
      }
      continue
    }
    if (KEEP_ALWAYS.has(k)) { if (v != null) out[k] = v; continue }
    if (v === 1) out[k] = 1  // only store truthy signal flags
  }
  return out
}

export function turboCacheSet(tf, uni, results, lastScan) {
  if (getCacheBackend() === 'idb') {
    // IndexedDB: no size limit, store full results without slimming
    idbSet(tf, uni, results, lastScan)
    return
  }
  // localStorage path (D+A): slim rows + truncation fallback
  const key = _tsKey(tf, uni)
  const payload = { results: results.map(_slimRow), lastScan }
  const write = (p) => localStorage.setItem(key, JSON.stringify(p))
  try {
    write(payload)
  } catch {
    _evictAll(key)  // free ALL space, retry
    try {
      write(payload)
    } catch {
      // last resort: top 1000 by score
      try {
        const top = [...results].sort((a,b) => (b.turbo_score??0)-(a.turbo_score??0)).slice(0,1000)
        write({ results: top.map(_slimRow), lastScan, truncated: true })
      } catch {}
    }
  }
}

const _tsSet = turboCacheSet

// ─────────────────────────────────────────────────────────────────────────────
const _initTf  = () => { try { return localStorage.getItem('sachoki_turbo_tf')  || '1d'    } catch { return '1d'    } }
const _initUni = () => { try { return localStorage.getItem('sachoki_turbo_uni') || 'sp500' } catch { return 'sp500' } }

function StarBtn({ ticker, tf, onToggle }) {
  const [saved, setSaved] = useState(() => pwlHas(ticker, tf))
  return (
    <button
      title={saved ? 'Remove from watchlist' : 'Save to watchlist'}
      className={`text-sm transition-colors ${saved ? 'text-yellow-400' : 'text-gray-700 hover:text-yellow-400'}`}
      onClick={e => {
        e.stopPropagation()
        onToggle?.()
        setSaved(s => !s)
      }}>
      ★
    </button>
  )
}

export default function TurboScanPanel({ onSelectTicker }) {
  const [localTf,    setLocalTf]    = useState(_initTf)
  const [universe,   setUniverse]   = useState(_initUni)
  const [allResults, setAllResults] = useState(() => { const tf = _initTf(); const uni = _initUni(); return _tsGet(tf, uni)?.results || [] })
  const [lastScan,   setLastScan]   = useState(() => { const tf = _initTf(); const uni = _initUni(); return _tsGet(tf, uni)?.lastScan || null })
  const [scanning,   setScanning]   = useState(false)
  const [error,      setError]      = useState(null)
  const pollIvRef   = useRef(null)   // interval handle — prevents duplicate polls
  const fetchSeqRef = useRef(0)      // monotonic counter — discard stale fetches
  const [massiveReady, setMassiveReady] = useState(null)
  const [scoreBands, setScoreBands] = useState(new Set(['all']))
  const [direction,  setDirection]  = useState('bull')
  const [secFilter,  setSecFilter]  = useState('')    // '' = all sectors
  const [sectorMap,  setSectorMap]  = useState({})    // { TICKER: sector_string }
  const [selSigs,    setSelSigs]    = useState(new Set())   // AND filter
  const [rtbPhase,    setRtbPhase]    = useState('')      // '' = all phases
  const [exported,   setExported]   = useState(false)
  const [sortBy,     setSortBy]     = useState('turbo_score')
  const [sortDir,    setSortDir]    = useState('desc')
  const [lookbackN,  setLookbackN]  = useState(1)
  const [pickedTickers, setPickedTickers] = useState(new Set())  // individually selected rows

  const _pwlToggle = (row) => {
    const r = { ...row, _tf: localTf }
    if (pwlHas(row.ticker, localTf)) { pwlRemove(row.ticker, localTf) }
    else { pwlAdd(r) }
  }
  const [sweetSpotFilter, setSweetSpotFilter] = useState(false)
  const [buildingFilter,  setBuildingFilter]  = useState(false)
  const [watchFilter,     setWatchFilter]     = useState(false)
  const [partialDay,  setPartialDay]  = useState(false)  // include today's in-progress bar
  const [volMin,      setVolMin]      = useState(100_000) // min avg daily volume filter
  const [volMax,      setVolMax]      = useState(0)       // max avg daily volume (0 = no cap)
  const [hoverPopup,  setHoverPopup]  = useState(null)   // { row, pos }
  const hoverTimer = useRef(null)
  const [showAdvanced, setShowAdvanced] = useState(false)

  // which TFs have a cache entry for current universe
  const tfCached = useMemo(
    () => Object.fromEntries(TF_OPTS.map(t => [t, !!_tsGet(t, universe)?.results?.length])),
    [universe, allResults]  // re-check when results change (after scan saves cache)
  )

  // Fetch fresh results from server after a scan completes, then cache.
  // Uses fetchSeqRef to discard responses from stale (superseded) requests.
  const fetchFreshResults = useCallback((tf, uni) => {
    const seq = ++fetchSeqRef.current
    api.turboScan(10000, 0, 'all', tf, uni, {})
      .then(d => {
        if (seq !== fetchSeqRef.current) return  // superseded by a newer fetch
        const results = d.results || []
        const ls = d.last_scan
        if (results.length > 0) {
          turboCacheSet(tf, uni, results, ls)
          setAllResults(results)
          setLastScan(ls || null)
        }
      })
      .catch(e => { if (seq === fetchSeqRef.current) setError(e.message) })
  }, [])

  // Read from cache (IDB or localStorage) and populate allResults if empty
  const loadFromCache = useCallback(async (tf, uni) => {
    let cached
    if (getCacheBackend() === 'idb') {
      cached = await idbGet(tf, uni)
    } else {
      cached = _tsGet(tf, uni)
      if (!cached?.results?.length && uni !== 'all_us') {
        cached = _tsGet(tf, 'all_us')
      }
      if (cached?.truncated) {
        fetchFreshResults(tf, uni)
      }
    }
    if (cached?.results?.length) {
      setAllResults(cached.results)
      setLastScan(cached.lastScan || null)
    } else {
      setAllResults([])
      setLastScan(null)
    }
  }, [fetchFreshResults])

  useEffect(() => {
    loadFromCache(localTf, universe)       // instant from cache (may be stale)
    fetchFreshResults(localTf, universe)   // always refresh from server in background
  }, [localTf, universe]) // eslint-disable-line react-hooks/exhaustive-deps

  // Lazy-batch sector fetch: after results load, fetch sectors for tickers missing them
  useEffect(() => {
    if (!allResults.length) return
    const sorted = [...allResults].sort((a, b) => (b.turbo_score ?? 0) - (a.turbo_score ?? 0))
    const missing = sorted
      .filter(r => !r.sector && !sectorMap[r.ticker])
      .slice(0, 200)
      .map(r => r.ticker)
    if (!missing.length) return
    api.tickerInfoBatch(missing)
      .then(data => {
        setSectorMap(prev => {
          const next = { ...prev }
          for (const [ticker, info] of Object.entries(data)) {
            if (info.sector) next[ticker] = info.sector
          }
          return next
        })
      })
      .catch(() => {})
  }, [allResults]) // eslint-disable-line react-hooks/exhaustive-deps

  // Listen for Admin-triggered scan completing — switch universe/tf and reload cache
  useEffect(() => {
    const onCached = (e) => {
      const { tf: newTf, uni: newUni, results, lastScan } = e.detail
      if (newTf !== localTf) { setLocalTf(newTf); try { localStorage.setItem('sachoki_turbo_tf', newTf) } catch {} }
      if (newUni !== universe) { setUniverse(newUni); try { localStorage.setItem('sachoki_turbo_uni', newUni) } catch {} }
      // Use results from event directly — avoids stale localStorage read
      if (results?.length > 0 && newTf === localTf && newUni === universe) {
        setAllResults(results)
        setLastScan(lastScan || null)
      } else if (newTf === localTf && newUni === universe) {
        loadFromCache(newTf, newUni)
      }
    }
    window.addEventListener('sachoki:scan-cached', onCached)
    return () => window.removeEventListener('sachoki:scan-cached', onCached)
  }, [localTf, universe, loadFromCache])

  useEffect(() => { api.getConfig().then(c => setMassiveReady(c.massive_api_ready)).catch(() => {}) }, [])

  // ── Effective score column based on selected N ────────────────────────────
  const effectiveScoreCol = lookbackN >= 10 ? 'turbo_score_n10'
                          : lookbackN >= 5  ? 'turbo_score_n5'
                          : lookbackN >= 3  ? 'turbo_score_n3'
                          : 'turbo_score'

  // ── Client-side filter + sort ──────────────────────────────────────────────
  const results = useMemo(() => {
    const filtered = allResults.filter(r => {
      // score band filter (multi-select)
      const score = r[effectiveScoreCol] ?? r.turbo_score ?? 0
      if (!scoreBands.has('all') && scoreBands.size > 0) {
        const inBand = SCORE_BANDS.some(b =>
          b.key !== 'all' && scoreBands.has(b.key) && score >= b.min && score <= b.max
        )
        if (!inBand) return false
      }
      if (volMin > 0 && r.avg_vol > 0 && r.avg_vol < volMin) return false
      if (volMax > 0 && r.avg_vol > 0 && r.avg_vol > volMax) return false
      if (secFilter && !(sectorMap[r.ticker] || r.sector || '').toLowerCase().includes(secFilter)) return false
      if (rtbPhase && (r.rtb_phase || '0') !== rtbPhase) return false
      if (direction === 'bull' && !r.tz_bull) return false
      if (direction === 'bear' && r.tz_bull)  return false
      if (sweetSpotFilter && !(r.sweet_spot_active && !r.late_warning)) return false
      if (buildingFilter && r.profile_category !== 'BUILDING') return false
      if (watchFilter    && r.profile_category !== 'WATCH')    return false
      if (selSigs.size > 0) {
        // parse ages once per row (cached on the object)
        if (!r._ages && r.sig_ages) {
          try { r._ages = JSON.parse(r.sig_ages) } catch { r._ages = {} }
        }
        const ages = r._ages || {}
        const ok = [...selSigs].every(k => {
          const sig = SIG_GROUPS.find(s => !s.divider && s.key === k)
          if (sig?.custom) return sig.custom(r)
          if (lookbackN > 1 && k in ages) return ages[k] < lookbackN
          return !!r[k]
        })
        if (!ok) return false
      }
      return true
    })
    // sort: when any profile filter active, sort by profile_score then turbo_score
    const mul = sortDir === 'asc' ? 1 : -1
    if (sweetSpotFilter || buildingFilter || watchFilter) {
      filtered.sort((a, b) =>
        (b.profile_score ?? 0) - (a.profile_score ?? 0) ||
        (b[effectiveScoreCol] ?? b.turbo_score ?? 0) - (a[effectiveScoreCol] ?? a.turbo_score ?? 0)
      )
    } else {
      filtered.sort((a, b) => {
        const col = sortBy === 'turbo_score' ? effectiveScoreCol : sortBy
        const av = a[col] ?? 0
        const bv = b[col] ?? 0
        if (typeof av === 'string') return mul * av.localeCompare(bv)
        return mul * (av - bv)
      })
    }
    return filtered
  }, [allResults, scoreBands, direction, selSigs, lookbackN, sortBy, sortDir, effectiveScoreCol, volMin, volMax, secFilter, sectorMap, rtbPhase, sweetSpotFilter, buildingFilter, watchFilter])

  const toggleSort = (col) => {
    if (sortBy === col) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortBy(col); setSortDir('desc') }
  }

  const SortTh = ({ col, children, cls = '' }) => (
    <th
      className={`px-2 py-1.5 font-medium cursor-pointer select-none hover:text-white transition-colors ${cls}`}
      onClick={() => toggleSort(col)}>
      {children}{sortBy === col ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
    </th>
  )

  const toggleSig = key => setSelSigs(prev => {
    const n = new Set(prev)
    n.has(key) ? n.delete(key) : n.add(key)
    return n
  })

  const togglePicked = (ticker, e) => {
    e.stopPropagation()
    setPickedTickers(prev => {
      const n = new Set(prev)
      n.has(ticker) ? n.delete(ticker) : n.add(ticker)
      return n
    })
  }

  const exportTickers = () => {
    const src = pickedTickers.size > 0
      ? results.filter(r => pickedTickers.has(r.ticker))
      : results
    const text = src.map(r => r.ticker).join('\n')
    const blob = new Blob([text], { type: 'text/plain' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    const date = new Date().toISOString().slice(0, 10)
    a.href     = url

    // Build descriptive filename from active filters
    const _sigLabelMap = Object.fromEntries(
      SIG_GROUPS.filter(s => s.key).map(s => [s.key, s.label.replace(/[^a-zA-Z0-9]/g, '')])
    )
    const parts = [universe, localTf.toUpperCase()]
    if (direction !== 'all') parts.push(direction.toUpperCase())
    if (!scoreBands.has('all') && scoreBands.size > 0) {
      parts.push([...scoreBands].join('+'))
    }
    if (selSigs.size > 0) {
      const sigKeys = [...selSigs]
      const sigLabels = sigKeys.slice(0, 3).map(k => _sigLabelMap[k] || k.toUpperCase())
      const sigPart = sigLabels.join('+') + (sigKeys.length > 3 ? `+${sigKeys.length - 3}more` : '')
      parts.push(sigPart)
    }
    if (secFilter) parts.push(secFilter.toUpperCase())
    if (rtbPhase) parts.push(`RTB${rtbPhase}`)
    if (sweetSpotFilter) parts.push('SWEET')
    if (buildingFilter)  parts.push('BUILDING')
    if (watchFilter)     parts.push('WATCH')
    if (pickedTickers.size > 0) parts.push(`picked${pickedTickers.size}`)
    parts.push(date)
    a.download = `sachoki_${parts.join('_')}.txt`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    setExported(true)
    setTimeout(() => setExported(false), 2000)
  }

  const handleRowEnter = useCallback((e, row) => {
    clearTimeout(hoverTimer.current)
    const rect = e.currentTarget.getBoundingClientRect()
    hoverTimer.current = setTimeout(() => {
      setHoverPopup({ row, pos: { x: rect.right, y: rect.top + rect.height / 2 } })
    }, 350)
  }, [])

  const handleRowLeave = useCallback(() => {
    clearTimeout(hoverTimer.current)
    setHoverPopup(null)
  }, [])

  // cleanup on unmount
  useEffect(() => () => {
    if (pollIvRef.current) clearInterval(pollIvRef.current)
    clearTimeout(hoverTimer.current)
  }, [])

  // ── Poll until done ────────────────────────────────────────────────────────
  const _stopPoll = () => {
    if (pollIvRef.current) { clearInterval(pollIvRef.current); pollIvRef.current = null }
  }

  const _poll = () => {
    _stopPoll()  // kill any previous poll before starting a new one
    const activeTf = localTf
    const uni      = universe
    pollIvRef.current = setInterval(() => {
      api.turboScanStatus()
        .then(s => {
          if (!s.running) {
            _stopPoll(); setScanning(false)
            if (s.error) setError(s.error)
            else fetchFreshResults(activeTf, uni)
          }
        })
        .catch(() => { _stopPoll(); setScanning(false) })
    }, 2000)
    setTimeout(() => { _stopPoll(); setScanning(false); fetchFreshResults(activeTf, uni) }, 360_000)
  }

  const scan = () => {
    if (scanning) return  // guard against double-trigger
    setScanning(true); setError(null)
    api.turboScanTrigger(localTf, universe, lookbackN, partialDay, volMin,
                         getCacheBackend() === 'idb' ? 0 : 5)
      .then(() => _poll())
      .catch(e => {
        setScanning(false)
        const msg = e?.detail || e?.message || String(e)
        if (msg.includes('409') || msg.toLowerCase().includes('already running')) {
          setError('__stuck__')
        } else {
          setError(msg)
        }
      })
  }

  // When N>1, override per-signal booleans with age-window check so badges
  // show signals that fired within the last N bars, not just the last bar.
  const withAges = (raw) => {
    if (lookbackN === 1) return raw
    if (!raw._ages && raw.sig_ages) {
      try { raw._ages = JSON.parse(raw.sig_ages) } catch { raw._ages = {} }
    }
    const ov = {}
    for (const [k, a] of Object.entries(raw._ages || {})) ov[k] = a < lookbackN ? 1 : 0
    return { ...raw, ...ov }
  }

  return (
    <div className="flex flex-col h-full bg-md-surface text-md-on-surface text-xs" onMouseLeave={handleRowLeave}>
      {hoverPopup && (
        <MiniChartPopup row={hoverPopup.row} tf={localTf} pos={hoverPopup.pos} onClose={() => setHoverPopup(null)} />
      )}

      {/* ── Row 0: Universe selector ── */}
      <div className="flex flex-wrap items-center gap-1.5 px-3 py-2 border-b border-white/[0.07] bg-md-surface-con/50">
        <span className="text-md-on-surface-var text-xs w-16 shrink-0">Universe</span>
        {UNIVERSES.map(u => (
          <button key={u.key}
            onClick={() => { setUniverse(u.key); setAllResults([]); setLastScan(null); try { localStorage.setItem('sachoki_turbo_uni', u.key) } catch {} }}
            title={u.desc}
            className={`px-2.5 py-1 rounded text-xs font-medium transition-colors border
              ${universe === u.key
                ? `${u.cls} border-current bg-md-surface-high`
                : 'text-md-on-surface-var border-md-outline-var hover:text-md-on-surface hover:border-gray-500'}`}>
            {u.label}
          </button>
        ))}
        <span className="text-md-on-surface-var/70 text-xs ml-1">
          {(universe === 'nasdaq' || universe === 'russell2k' || universe === 'all_us') && massiveReady === false && <span className="text-red-400">· MASSIVE_API_KEY not set (will use fallback list)</span>}
          {(universe === 'nasdaq' || universe === 'russell2k' || universe === 'all_us') && massiveReady === true  && <span className="text-green-500">· Massive API ready</span>}
        </span>
      </div>

      {/* ── Row 1: TF + Scan + Direction + Score ── */}
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 border-b border-white/[0.07]">

        {/* TF selector — cached TFs show a green dot */}
        <div className="flex gap-0.5 border border-md-outline-var rounded p-0.5">
          {TF_OPTS.map(t => (
            <button key={t} onClick={() => { setLocalTf(t); setLastScan(null); try { localStorage.setItem('sachoki_turbo_tf', t) } catch {} }}
              title={tfCached[t] ? `${t.toUpperCase()} — cached (instant)` : `${t.toUpperCase()} — no cache, scan first`}
              className={`relative px-2 py-0.5 rounded text-xs font-medium transition-colors
                ${localTf === t ? 'bg-blue-600 text-white' : 'text-md-on-surface-var hover:text-white'}`}>
              {t.toUpperCase()}
              {tfCached[t] && (
                <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-green-400" />
              )}
            </button>
          ))}
        </div>

        {/* Scan button */}
        <button onClick={scan} disabled={scanning}
          className={`px-3 py-1 rounded text-xs font-semibold transition-colors
            ${scanning ? 'bg-gray-700 text-md-on-surface-var cursor-not-allowed'
                       : 'bg-violet-600 hover:bg-violet-500 text-white'}`}>
          {scanning
            ? <span className="animate-pulse">⚡ Scanning…</span>
            : '⚡ TURBO'}
        </button>

        {/* Partial-day preview toggle — include today's open bar */}
        <button onClick={() => setPartialDay(p => !p)}
          title="Include today's in-progress daily bar (scan during market hours for an early read)"
          className={`px-2.5 py-1 rounded text-xs font-medium transition-colors border
            ${partialDay
              ? 'border-amber-400 text-amber-300 bg-amber-900/30'
              : 'border-md-outline-var text-md-on-surface-var hover:text-md-on-surface hover:border-gray-500'}`}>
          ~Preview
        </button>

        {/* Export button */}
        <button onClick={exportTickers} disabled={results.length === 0}
          title={pickedTickers.size > 0 ? `Copy ${pickedTickers.size} selected tickers` : 'Copy all visible tickers (TradingView watchlist)'}
          className={`px-2.5 py-1 rounded text-xs font-medium transition-colors border
            ${exported
              ? 'border-lime-500 text-lime-300 bg-lime-900/30'
              : results.length === 0
                ? 'border-md-outline-var text-md-on-surface-var/70 cursor-not-allowed'
                : pickedTickers.size > 0
                  ? 'border-yellow-500 text-yellow-300 bg-yellow-900/20 hover:border-yellow-400'
                  : 'border-gray-600 text-md-on-surface hover:border-gray-400 hover:text-white'}`}>
          {exported
            ? '✓ Copied'
            : pickedTickers.size > 0
              ? `⬇ Export (${pickedTickers.size})`
              : '⬇ Export'}
        </button>
        {/* Clear selection */}
        {pickedTickers.size > 0 && (
          <button onClick={() => setPickedTickers(new Set())}
            className="px-2 py-0.5 rounded text-xs text-md-on-surface-var hover:text-red-400 transition-colors"
            title="Clear row selection">
            ✕ deselect
          </button>
        )}

        {/* Direction */}
        <div className="flex gap-0.5">
          {DIR_OPTS.map(d => (
            <button key={d.key} onClick={() => setDirection(d.key)}
              className={`px-2 py-0.5 rounded text-xs transition-colors
                ${direction === d.key ? 'bg-indigo-600 text-white' : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}>
              {d.label}
            </button>
          ))}
        </div>

        {/* Score bands — multi-select */}
        <div className="flex gap-0.5 ml-1">
          {SCORE_BANDS.map(b => {
            const active = scoreBands.has(b.key)
            return (
              <button key={b.key}
                onClick={() => {
                  setScoreBands(prev => {
                    const next = new Set(prev)
                    if (b.key === 'all') {
                      return new Set(['all'])
                    }
                    next.delete('all')
                    if (next.has(b.key)) {
                      next.delete(b.key)
                      if (next.size === 0) next.add('all')
                    } else {
                      next.add(b.key)
                    }
                    return next
                  })
                }}
                className={`px-2 py-0.5 rounded text-xs transition-colors
                  ${active ? 'bg-amber-600 text-black font-semibold' : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}>
                {b.label}
              </button>
            )
          })}
        </div>

        {/* N= lookback selector — client-side, no rescan needed */}
        <div className="flex items-center gap-0.5 ml-1" title="Signal lookback window — no rescan needed">
          <span className="text-md-on-surface-var text-xs mr-0.5">N=</span>
          {[1, 3, 5, 10].map(n => (
            <button key={n} onClick={() => setLookbackN(n)}
              className={`px-2 py-0.5 rounded text-xs transition-colors
                ${lookbackN === n ? 'bg-indigo-700 text-white font-semibold' : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}
              title={n === 1 ? 'Current bar only' : `Signal fired in last ${n} bars`}>
              {n}d
            </button>
          ))}
        </div>

        {/* Volume filter */}
        <div className="flex items-center gap-0.5 ml-1" title="Avg daily volume filter">
          <span className="text-md-on-surface-var text-xs mr-0.5">Vol</span>
          {[
            { label: 'All',   min: 0,         max: 0         },
            { label: '<100K', min: 0,         max: 100_000   },
            { label: '100K+', min: 100_000,   max: 0         },
            { label: '500K+', min: 500_000,   max: 0         },
            { label: '1M+',   min: 1_000_000, max: 0         },
            { label: '5M+',   min: 5_000_000, max: 0         },
          ].map(({ label, min, max }) => {
            const active = volMin === min && volMax === max
            return (
              <button key={label}
                onClick={() => { setVolMin(min); setVolMax(max) }}
                className={`px-2 py-0.5 rounded text-xs transition-colors
                  ${active ? 'bg-cyan-700 text-white font-semibold' : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}
                title={label === 'All' ? 'No volume filter' : label === '<100K' ? 'Avg volume < 100K' : `Avg volume ≥ ${min.toLocaleString()}`}>
                {label}
              </button>
            )
          })}
        </div>

        {/* Stats + stale warning */}
        <span className="ml-auto text-md-on-surface-var/70 shrink-0 flex items-center gap-1.5">
          {partialDay && <span className="text-amber-400 font-medium">~preview</span>}
          {results.length} / {allResults.length}
          {lastScan && (() => {
            const ageH = (Date.now() - new Date(lastScan).getTime()) / 3_600_000
            return (
              <span className={ageH > 2 ? 'text-yellow-500' : 'text-md-on-surface-var/70'}>
                {ageH > 2 ? '⚠ ' : ''}{lastScan.slice(0,16).replace('T',' ')}
                {ageH > 2 && ` (${Math.floor(ageH)}h ago)`}
              </span>
            )
          })()}
        </span>
      </div>

      {/* ── Row 2: Primary quick-filters + Advanced toggle ── */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-1.5 border-b border-white/[0.07] bg-md-surface-con/20">
        {/* Profile quick-filters */}
        <button
          onClick={() => setSweetSpotFilter(f => !f)}
          className={`px-2.5 py-0.5 rounded text-xs font-semibold shrink-0 transition-colors border ${
            sweetSpotFilter
              ? 'bg-emerald-900/60 text-emerald-300 border-emerald-600'
              : 'bg-md-surface-high text-md-on-surface-var border-md-outline-var hover:text-white'
          }`}>
          SWEET_SPOT
        </button>
        <button
          onClick={() => setWatchFilter(f => !f)}
          className={`px-2.5 py-0.5 rounded text-xs font-semibold shrink-0 transition-colors border ${
            watchFilter
              ? 'bg-yellow-900/60 text-yellow-300 border-yellow-600'
              : 'bg-md-surface-high text-md-on-surface-var border-md-outline-var hover:text-white'
          }`}>
          WATCH
        </button>
        <button
          onClick={() => setBuildingFilter(f => !f)}
          className={`px-2.5 py-0.5 rounded text-xs font-semibold shrink-0 transition-colors border ${
            buildingFilter
              ? 'bg-blue-900/60 text-blue-300 border-blue-600'
              : 'bg-md-surface-high text-md-on-surface-var border-md-outline-var hover:text-white'
          }`}>
          BUILDING
        </button>
        {(sweetSpotFilter || buildingFilter || watchFilter) && (
          <button onClick={() => { setSweetSpotFilter(false); setBuildingFilter(false); setWatchFilter(false) }}
            className="px-2 py-0.5 rounded text-xs shrink-0 bg-red-900/40 text-red-400 hover:bg-red-900/60">
            ✕ clear
          </button>
        )}
        <span className="text-md-outline-var/50 select-none">|</span>
        {/* Advanced toggle */}
        <button
          onClick={() => setShowAdvanced(a => !a)}
          className={`px-2.5 py-0.5 rounded text-xs font-medium shrink-0 transition-colors border ${
            showAdvanced || selSigs.size > 0
              ? 'bg-indigo-900/50 text-indigo-300 border-indigo-600'
              : 'bg-md-surface-high text-md-on-surface-var border-md-outline-var hover:text-white'
          }`}>
          Advanced {showAdvanced ? '▴' : '▾'}{selSigs.size > 0 ? ` (${selSigs.size})` : ''}
        </button>
        {selSigs.size > 0 && (
          <button onClick={() => setSelSigs(new Set())}
            className="px-2 py-0.5 rounded text-xs shrink-0 bg-red-900/40 text-red-400 hover:bg-red-900/60">
            ✕ sigs
          </button>
        )}
      </div>

      {/* ── Advanced filters (collapsible) ── */}
      {showAdvanced && (
        <div className="border-b border-white/[0.07] bg-md-surface-con/30">
          {/* Signal chips */}
          <div className="flex flex-wrap items-center gap-x-1 gap-y-1 px-3 py-2 overflow-x-auto">
            <span className="text-md-on-surface-var text-xs shrink-0 mr-0.5">SIG</span>
            <button onClick={() => setSelSigs(new Set())}
              className={`px-2 py-0.5 rounded text-xs shrink-0 ${selSigs.size === 0 ? 'bg-blue-600 text-white' : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}>
              All
            </button>
            {SIG_GROUPS.map((s, i) =>
              s.divider
                ? <span key={`div-${i}`} className="text-gray-700 select-none px-0.5 self-center">·</span>
                : (
                  <button key={s.key} onClick={() => toggleSig(s.key)}
                    className={`px-2 py-0.5 rounded text-xs shrink-0 transition-colors
                      ${selSigs.has(s.key) ? `${s.cls} bg-gray-700 font-semibold` : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}>
                    {s.label}
                  </button>
                )
            )}
          </div>
          {/* Sector filter */}
          <div className="flex flex-wrap items-center gap-x-1 gap-y-1 px-3 py-1.5 border-t border-md-outline-var/30">
            <span className="text-md-on-surface-var text-xs shrink-0 mr-0.5 w-16">Sector</span>
            {[
              { label: 'All',  val: '',            cls: 'text-md-on-surface-var' },
              { label: 'XLC',  val: 'communicat',  cls: 'text-blue-300' },
              { label: 'XLY',  val: 'cyclical',    cls: 'text-orange-300' },
              { label: 'XLP',  val: 'defensive',   cls: 'text-green-300' },
              { label: 'XLE',  val: 'energy',      cls: 'text-yellow-300' },
              { label: 'XLF',  val: 'financ',      cls: 'text-cyan-300' },
              { label: 'XLV',  val: 'health',      cls: 'text-red-300' },
              { label: 'XLI',  val: 'industrial',  cls: 'text-sky-300' },
              { label: 'XLB',  val: 'material',    cls: 'text-lime-300' },
              { label: 'XLRE', val: 'real estate', cls: 'text-amber-300' },
              { label: 'XLK',  val: 'tech',        cls: 'text-violet-300' },
              { label: 'XLU',  val: 'utilities',   cls: 'text-teal-300' },
            ].map(s => (
              <button key={s.val} onClick={() => setSecFilter(s.val)}
                className={`px-2 py-0.5 rounded text-xs shrink-0 transition-colors
                  ${secFilter === s.val
                    ? `${s.cls} bg-gray-700 font-semibold`
                    : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}>
                {s.label}
              </button>
            ))}
          </div>
          {/* RTB Phase */}
          <div className="flex flex-wrap items-center gap-x-1 gap-y-1 px-3 py-1.5 border-t border-md-outline-var/30">
            <span className="text-md-on-surface-var text-xs shrink-0 mr-0.5 w-16">RTB Phase</span>
            {[
              { label: 'All',        val: '', cls: 'text-md-on-surface-var' },
              { label: 'A — Build',  val: 'A', cls: 'text-md-on-surface' },
              { label: 'B — Turn',   val: 'B', cls: 'text-sky-300' },
              { label: 'C — Ready',  val: 'C', cls: 'text-lime-300' },
              { label: 'D — Late',   val: 'D', cls: 'text-orange-300' },
            ].map(s => (
              <button key={s.val} onClick={() => setRtbPhase(s.val)}
                className={`px-2 py-0.5 rounded text-xs shrink-0 transition-colors
                  ${rtbPhase === s.val
                    ? `${s.cls} bg-gray-700 font-semibold ring-1 ring-gray-500`
                    : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}>
                {s.label}
              </button>
            ))}
          </div>
        </div>
      )}


      {/* Progress / error */}
      {scanning && (
        <div className="px-4 py-1.5 border-b border-white/[0.07] bg-violet-950/30 text-violet-300 animate-pulse">
          ⚡ TURBO — {UNIVERSES.find(u => u.key === universe)?.label ?? universe} ({localTf.toUpperCase()}) — 1-2 minutes…
        </div>
      )}
      {error && (
        <div className="px-4 py-1.5 text-md-error border-b border-white/[0.07] flex items-center gap-3">
          {error === '__stuck__'
            ? <>
                <span>Another scan is in progress — wait for it to finish, then try again</span>
                <button
                  onClick={() => api.turboScanReset().then(() => setError(null))}
                  className="px-2 py-0.5 text-xs bg-red-900/50 hover:bg-red-800/60 border border-red-700 rounded">
                  Force Reset
                </button>
              </>
            : error}
        </div>
      )}

      {/* ── Table (ScannerDataGrid) ── */}
      <ScannerDataGrid
        results={results.map(withAges)}
        onSelectTicker={onSelectTicker}
        onWatchlistToggle={_pwlToggle}
        localTf={localTf}
        pickedTickers={pickedTickers}
        onTogglePicked={togglePicked}
        sortBy={sortBy}
        sortDir={sortDir}
        onSort={toggleSort}
        isLoading={scanning && results.length === 0}
        effectiveScoreCol={effectiveScoreCol}
        universe={universe}
        variant="turbo"
        allPicked={results.length > 0 && results.every(r => pickedTickers.has(r.ticker))}
        onPickAll={checked => {
          if (checked) setPickedTickers(new Set(results.map(r => r.ticker)))
          else setPickedTickers(new Set())
        }}
        handleRowEnter={handleRowEnter}
        handleRowLeave={handleRowLeave}
      />
      {/* keep the old table structure below but commented as dead code — replaced by ScannerDataGrid */}
      {false && <div className="overflow-auto flex-1">
        <table className="w-full border-collapse">
          <tbody>
            {results.map(_raw => {
              const r = withAges(_raw)
              return (
              <tr key={r.ticker}
                onClick={() => onSelectTicker?.(r.ticker)}
                onMouseEnter={e => handleRowEnter(e, _raw)}
                onMouseLeave={handleRowLeave}>

                {/* Checkbox */}
                <td className="px-2 py-1 w-5" onClick={e => e.stopPropagation()}>
                  <input type="checkbox" className="accent-indigo-500 cursor-pointer"
                    checked={pickedTickers.has(r.ticker)}
                    onChange={e => togglePicked(r.ticker, e)} />
                </td>

                {/* Star / save to personal watchlist */}
                <td className="px-1 py-1 w-5">
                  <StarBtn ticker={r.ticker} tf={localTf} onToggle={() => _pwlToggle(r)} />
                </td>

                {/* Ticker */}
                <td className="px-2 py-1 font-mono font-semibold text-blue-300">
                  <div className="flex items-center gap-1">
                    <span>{r.ticker}</span>
                    {r.vol_bucket && (
                      <span className="text-md-on-surface-var/70 font-normal">{r.vol_bucket}</span>
                    )}
                    {r.data_source === 'yfinance' && (
                      <span
                        title="Data from yfinance fallback — may differ from Polygon (splits, adjusted prices)"
                        className="text-[8px] text-orange-400/60 font-normal align-top">yf</span>
                    )}
                    <a
                      href={`https://www.tradingview.com/chart/?symbol=${r.ticker}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={`Open ${r.ticker} on TradingView`}
                      onClick={e => e.stopPropagation()}
                      className="text-md-on-surface-var/70 hover:text-blue-400 transition-colors text-[10px] leading-none">
                      ↗
                    </a>
                  </div>
                </td>

                {/* Score — shows N-appropriate score */}
                <td className="px-2 py-1 text-center" title={scoreReason(r)}>
                  {(() => {
                    const sc = r[effectiveScoreCol] ?? r.turbo_score ?? 0
                    return (
                      <div className={`font-mono font-bold text-sm ${scoreColor(sc)}`}>
                        {fmt(sc, 1)}
                      </div>
                    )
                  })()}
                  <div className="text-[9px] text-md-on-surface-var/70 leading-tight mt-0.5 max-w-[72px] truncate">
                    {scoreReason(r)}
                  </div>
                </td>

                {/* BETA Score */}
                <td className="px-2 py-1 text-center"
                  title={r.beta_zone ? `BETA ${r.beta_score} | Zone: ${r.beta_zone} | Setup: ${r.beta_setup} Mom: ${r.beta_momentum} Excess: ${r.beta_excess}${r.beta_auto_buy ? ' | ★ AUTO-BUY' : ''}` : 'No BETA data'}>
                  {r.beta_score > 0 ? (
                    <div className="leading-none">
                      <div className={`font-mono font-bold text-sm ${betaZoneCls(r.beta_zone)}`}>
                        {r.beta_auto_buy ? '★ ' : ''}{r.beta_score}
                      </div>
                      <div className={`text-[9px] mt-0.5 ${betaZoneCls(r.beta_zone)}`}>{r.beta_zone}</div>
                    </div>
                  ) : <span className="text-gray-700">—</span>}
                </td>

                {/* RTB v4 phase + score */}
                <td className="px-2 py-1 text-center"
                  title={r.rtb_phase ? `RTB v4 | Phase ${r.rtb_phase} | Build ${r.rtb_build} Turn ${r.rtb_turn} Ready ${r.rtb_ready} Late ${r.rtb_late} | ${r.rtb_transition ?? ''}` : 'No RTB data'}>
                  {r.rtb_phase && r.rtb_phase !== '0' ? (
                    <div className="leading-none">
                      <span className={`inline-block font-bold text-[10px] px-1 rounded ${
                        r.rtb_phase === 'C' ? 'bg-lime-700/80 text-lime-100 ring-1 ring-lime-400' :
                        r.rtb_phase === 'B' ? 'bg-sky-800/80  text-sky-200  ring-1 ring-sky-500' :
                        r.rtb_phase === 'A' ? 'bg-gray-700    text-md-on-surface' :
                        /* D */ 'bg-orange-800/70 text-orange-200'
                      }`}>{r.rtb_phase}</span>
                      <div className="font-mono text-[10px] text-md-on-surface-var mt-0.5">{(r.rtb_total ?? 0).toFixed(0)}</div>
                    </div>
                  ) : <span className="text-gray-700">—</span>}
                </td>

                {/* T/Z signal */}
                <td className="px-2 py-1 text-center">
                  {r.tz_sig ? (
                    <span className={`font-mono font-semibold ${TZ_STRONG.has(r.tz_sig) ? 'text-lime-300' : TZ_BEAR.has(r.tz_sig) ? 'text-red-400' : 'text-blue-300'}`}>
                      {r.tz_sig}
                    </span>
                  ) : '—'}
                </td>

                {/* GOG tier + context signals + SIGNAL_SCORE */}
                <td className="px-2 py-1 text-center">
                  {(() => {
                    const ctx = ctxTokens(r)
                    const score = r.signal_score ?? 0
                    const hasContent = r.gog_tier || ctx.length > 0 || score > 0
                    if (!hasContent) return <span className="text-gray-700 text-xs">—</span>
                    return (
                      <div className="flex flex-col items-center gap-0.5">
                        {r.gog_tier && (
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${gogTierCls(r.gog_tier)}`}>
                            {r.gog_tier}
                          </span>
                        )}
                        {ctx.map(tok => (
                          <span key={tok} className={`text-[9px] px-1 py-0 rounded leading-tight ${ctxTokCls(tok)}`}>
                            {tok}
                          </span>
                        ))}
                        {score > 0 && (
                          <span className={`font-mono text-[10px] leading-none ${scoreCls(score)}`}>
                            {score}
                          </span>
                        )}
                        {r.already_extended ? (
                          <span className="text-[8px] text-orange-400 font-bold leading-none">EXT</span>
                        ) : null}
                      </div>
                    )
                  })()}
                </td>

                {/* VABS + Delta */}
                <td className="px-2 py-1">
                  <div className="flex flex-wrap gap-0.5">
                    {r.vol_spike_20x ? <Badge label="V×20" cls="bg-red-700 text-red-100 ring-1 ring-red-400 font-bold" /> : r.vol_spike_10x ? <Badge label="V×10" cls="bg-orange-700/80 text-orange-100 ring-1 ring-orange-400" /> : r.vol_spike_5x ? <Badge label="V×5" cls="bg-yellow-700/70 text-yellow-100" /> : null}
                    {r.best_sig   ? <Badge label="BEST★" cls="bg-lime-800 text-lime-200 ring-1 ring-lime-500" /> : null}
                    {r.strong_sig && !r.best_sig ? <Badge label="STR"  cls="bg-emerald-800 text-emerald-200" /> : null}
                    {r.vbo_up     ? <Badge label="VBO↑" cls="bg-green-700 text-white" /> : null}
                    {r.vbo_dn     ? <Badge label="VBO↓" cls="bg-red-800 text-red-200" /> : null}
                    {r.abs_sig    ? <Badge label="ABS"  cls="bg-teal-800 text-teal-200" /> : null}
                    {r.climb_sig  ? <Badge label="CLB"  cls="bg-cyan-800 text-cyan-200" /> : null}
                    {r.load_sig   ? <Badge label="LD"   cls="bg-blue-800 text-blue-200" /> : null}
                    {/* Delta signals */}
                    {r.d_blast_bull  ? <Badge label="ΔΔ↑" cls="bg-yellow-700/60 text-yellow-200 ring-1 ring-yellow-500" /> : null}
                    {r.d_surge_bull && !r.d_blast_bull ? <Badge label="Δ↑"  cls="bg-teal-800/60 text-teal-200" /> : null}
                    {r.d_strong_bull ? <Badge label="B/S↑" cls="bg-lime-900/50 text-lime-300" /> : null}
                    {r.d_absorb_bull ? <Badge label="Ab↑"  cls="bg-yellow-900/50 text-yellow-300" /> : null}
                    {r.d_div_bull    ? <Badge label="T↓"   cls="bg-cyan-900/50 text-cyan-300" /> : null}
                    {r.d_cd_bull  && !r.d_div_bull ? <Badge label="cd↑" cls="bg-sky-900/40 text-sky-300" /> : null}
                    {r.d_spring       ? <Badge label="dSPR" cls="bg-lime-800/60 text-lime-200 ring-1 ring-lime-500" /> : null}
                    {r.d_vd_div_bull && !r.d_spring ? <Badge label="NS" cls="bg-teal-900/40 text-teal-300" /> : null}
                    {r.rs_strong ? <Badge label="RS+" cls="bg-lime-800/60 text-lime-200 ring-1 ring-lime-500" /> : r.rs ? <Badge label="RS" cls="bg-green-900/50 text-green-300" /> : null}
                    {/* PREUP — EMA cross ↑ */}
                    {r.preup66 ? <Badge label="P66" cls="text-lime-200 bg-lime-800/60 ring-1 ring-lime-400" /> : r.preup55 ? <Badge label="P55" cls="text-emerald-200 bg-emerald-800/50" /> : r.preup89 ? <Badge label="P89" cls="text-teal-300 bg-teal-900/40" /> : r.preup3 ? <Badge label="P3" cls="text-cyan-300 bg-cyan-900/40" /> : r.preup2 ? <Badge label="P2" cls="text-cyan-400 bg-cyan-900/30" /> : r.preup50 ? <Badge label="P50" cls="text-sky-300 bg-sky-900/40" /> : null}
                    {/* PREDN — EMA drop ↓ */}
                    {r.predn66 ? <Badge label="D66" cls="text-red-200 bg-red-900/60 ring-1 ring-red-400" /> : r.predn55 ? <Badge label="D55" cls="text-red-300 bg-red-900/50" /> : r.predn89 ? <Badge label="D89" cls="text-orange-300 bg-orange-900/40" /> : r.predn3 ? <Badge label="D3" cls="text-orange-300 bg-orange-900/30" /> : r.predn2 ? <Badge label="D2" cls="text-red-300 bg-red-900/30" /> : r.predn50 ? <Badge label="D50" cls="text-orange-300 bg-orange-900/20" /> : null}
                  </div>
                </td>

                {/* Wyckoff */}
                <td className="px-2 py-1">
                  <div className="flex flex-wrap gap-0.5">
                    {/* VABS legacy */}
                    {r.ns ? <Badge label="NS" cls="bg-teal-900 text-teal-300" /> : null}
                    {r.sq ? <Badge label="SQ" cls="bg-cyan-900 text-cyan-300" /> : null}
                    {r.sc ? <Badge label="SC" cls="bg-orange-900 text-orange-300" /> : null}
                    {r.bc ? <Badge label="BC" cls="bg-rose-900 text-rose-300" /> : null}
                    {r.nd ? <Badge label="ND" cls="bg-pink-900 text-pink-300" /> : null}
                  </div>
                </td>

                {/* Combo */}
                <td className="px-2 py-1">
                  <div className="flex flex-wrap gap-0.5">
                    {r.rocket    ? <Badge label="🚀"    cls="bg-red-900 text-red-200 font-bold" /> : null}
                    {r.buy_2809  ? <Badge label="BUY"   cls="bg-lime-800 text-lime-200" /> : null}
                    {r.sig3g     ? <Badge label="3G"    cls="bg-cyan-800 text-cyan-200" /> : null}
                    {r.rtv       ? <Badge label="RTV"   cls="bg-blue-800 text-blue-200" /> : null}
                    {r.hilo_buy  ? <Badge label="HILO↑" cls="bg-green-800 text-green-200" /> : null}
                    {r.atr_brk   ? <Badge label="ATR↑"  cls="bg-emerald-800 text-emerald-200" /> : null}
                    {r.bb_brk    ? <Badge label="BB↑"   cls="bg-teal-800 text-teal-200" /> : null}
                    {r.va        ? <Badge label="VA"    cls="bg-lime-800/70 text-lime-200 ring-1 ring-lime-400" /> : null}
                    {r.bias_up   ? <Badge label="↑BIAS" cls="bg-green-900 text-green-300" /> : null}
                    {r.hilo_sell ? <Badge label="HILO↓" cls="bg-rose-900 text-rose-300" /> : null}
                    {r.bias_down ? <Badge label="↓BIAS" cls="bg-red-900 text-red-300" /> : null}
                    {r.um_2809    ? <Badge label="UM"   cls="bg-teal-800 text-teal-200" /> : null}
                    {r.svs_2809   ? <Badge label="SVS"  cls="bg-orange-800 text-orange-200" /> : null}
                    {r.conso_2809 ? <Badge label="CON"  cls="bg-yellow-800/60 text-yellow-200" /> : null}
                    {/* F confluences */}
                    {r.cd ? <Badge label="CD" cls="bg-lime-800 text-lime-200 ring-1 ring-lime-400" /> : r.ca ? <Badge label="CA" cls="bg-cyan-800 text-cyan-200 ring-1 ring-cyan-400" /> : r.cw ? <Badge label="CW" cls="bg-yellow-800/70 text-yellow-200 ring-1 ring-yellow-400" /> : null}
                    {/* F1-F11 signals */}
                    {r.any_f ? <Badge label="ANY F" cls="bg-amber-700/70 text-amber-200 ring-1 ring-amber-400" /> : null}
                    {r.f1   ? <Badge label="F1"  cls="bg-orange-800/60 text-orange-200" /> : null}
                    {r.f2   ? <Badge label="F2"  cls="bg-gray-700 text-md-on-surface" /> : null}
                    {r.f3   ? <Badge label="F3"  cls="bg-sky-800/60 text-sky-200" /> : null}
                    {r.f4   ? <Badge label="F4"  cls="bg-gray-700 text-md-on-surface" /> : null}
                    {r.f5   ? <Badge label="F5"  cls="bg-cyan-800/60 text-cyan-200" /> : null}
                    {r.f6   ? <Badge label="F6"  cls="bg-gray-700 text-md-on-surface" /> : null}
                    {r.f7   ? <Badge label="F7"  cls="bg-green-800/60 text-green-200" /> : null}
                    {r.f8   ? <Badge label="F8"  cls="bg-blue-800/60 text-blue-200" /> : null}
                    {r.f9   ? <Badge label="F9"  cls="bg-gray-700 text-md-on-surface" /> : null}
                    {r.f10  ? <Badge label="F10" cls="bg-lime-800/60 text-lime-200" /> : null}
                    {r.f11  ? <Badge label="F11" cls="bg-fuchsia-800/60 text-fuchsia-200" /> : null}
                    {/* G signals */}
                    {r.g1  ? <Badge label="G1"  cls="bg-lime-700/60 text-lime-200" /> : null}
                    {r.g2  ? <Badge label="G2"  cls="bg-cyan-700/60 text-cyan-200" /> : null}
                    {r.g4  ? <Badge label="G4"  cls="bg-fuchsia-700/60 text-fuchsia-200" /> : null}
                    {r.g6  ? <Badge label="G6"  cls="bg-orange-700/60 text-orange-200" /> : null}
                    {r.g11 ? <Badge label="G11" cls="bg-yellow-700/60 text-yellow-200" /> : null}
                    {r.seq_bcont   ? <Badge label="SBC"  cls="bg-violet-800/60 text-violet-200" /> : null}
                    {/* B signals (260321) */}
                    {r.b1  ? <Badge label="B1"  cls="bg-lime-800/60 text-lime-200" /> : null}
                    {r.b2  ? <Badge label="B2"  cls="bg-cyan-800/60 text-cyan-200" /> : null}
                    {r.b3  ? <Badge label="B3"  cls="bg-teal-800/60 text-teal-200" /> : null}
                    {r.b4  ? <Badge label="B4"  cls="bg-blue-800/60 text-blue-200" /> : null}
                    {r.b5  ? <Badge label="B5"  cls="bg-green-800/60 text-green-200" /> : null}
                    {r.b6  ? <Badge label="B6"  cls="bg-emerald-800/60 text-emerald-200" /> : null}
                    {r.b7  ? <Badge label="B7"  cls="bg-sky-800/60 text-sky-200" /> : null}
                    {r.b8  ? <Badge label="B8"  cls="bg-indigo-800/60 text-indigo-200" /> : null}
                    {r.b9  ? <Badge label="B9"  cls="bg-violet-800/60 text-violet-200" /> : null}
                    {r.b10 ? <Badge label="B10" cls="bg-purple-800/60 text-purple-200" /> : null}
                    {r.b11 ? <Badge label="B11" cls="bg-fuchsia-800/60 text-fuchsia-200" /> : null}
                    {r.tz_bull_flip ? <Badge label="TZ→3" cls="bg-lime-700/60 text-lime-200 ring-1 ring-lime-400" /> : null}
                    {r.tz_attempt && !r.tz_bull_flip ? <Badge label="TZ→2" cls="bg-cyan-800/50 text-cyan-200" /> : null}
                    {/* RGTI 260404 + SMX 260402 */}
                    {r.smx           ? <Badge label="SMX"  cls="bg-lime-700/70 text-lime-200 ring-1 ring-lime-400 font-bold" /> : null}
                    {r.rgti_ll       ? <Badge label="LL"   cls="bg-purple-800/60 text-purple-200 ring-1 ring-purple-400" /> : null}
                    {r.rgti_up       ? <Badge label="UP"   cls="bg-blue-800/60 text-blue-200 ring-1 ring-blue-400" /> : null}
                    {r.rgti_upup     ? <Badge label="↑↑"   cls="bg-fuchsia-800/60 text-fuchsia-200" /> : null}
                    {r.rgti_upupup   ? <Badge label="↑↑↑"  cls="bg-sky-800/60 text-sky-200" /> : null}
                    {r.rgti_orange   ? <Badge label="ORG"  cls="bg-orange-700/60 text-orange-200" /> : null}
                    {r.rgti_green    ? <Badge label="GRN"  cls="bg-green-800/60 text-green-200" /> : null}
                    {r.rgti_greencirc? <Badge label="GC"   cls="bg-emerald-800/60 text-emerald-200" /> : null}
                    {/* PARA 260420 */}
                    {r.para_plus   ? <Badge label="PARA+" cls="bg-cyan-700/70 text-cyan-100 ring-1 ring-cyan-400 font-bold" /> : null}
                    {r.para_start && !r.para_plus ? <Badge label="PARA" cls="bg-lime-700/60 text-lime-200 ring-1 ring-lime-400" /> : null}
                    {r.para_prep   ? <Badge label="PREP"  cls="bg-green-800/60 text-green-200" /> : null}
                    {r.para_retest ? <Badge label="RTEST" cls="bg-emerald-700/60 text-emerald-200 ring-1 ring-emerald-400" /> : null}
                    {/* 260501 Composite setup signals */}
                    {r.akan_sig ? <Badge label="A"   cls="bg-orange-700/80 text-orange-100 ring-1 ring-orange-400 font-bold" /> : null}
                    {r.smx_sig  ? <Badge label="SM"  cls="bg-lime-700/80 text-lime-100 ring-1 ring-lime-400 font-bold" /> : null}
                    {r.nnn_sig  ? <Badge label="N"   cls="bg-cyan-700/80 text-cyan-100 ring-1 ring-cyan-400 font-bold" /> : null}
                    {r.mx_sig   ? <Badge label="MX"  cls="bg-pink-700/80 text-pink-100 ring-1 ring-pink-400 font-bold" /> : null}
                    {r.gog_sig  ? <Badge label="GOG" cls="bg-fuchsia-700/80 text-fuchsia-100 ring-1 ring-fuchsia-400 font-bold" /> : null}
                    {/* FLY 260424 */}
                    {r.fly_abcd ? <Badge label="ABCD" cls="bg-lime-700/70 text-lime-100 ring-1 ring-lime-400 font-bold" /> : null}
                    {r.fly_cd && !r.fly_abcd ? <Badge label="CD" cls="bg-cyan-700/60 text-cyan-200" /> : null}
                    {r.fly_bd && !r.fly_abcd ? <Badge label="BD" cls="bg-blue-700/60 text-blue-200" /> : null}
                    {r.fly_ad && !r.fly_abcd ? <Badge label="AD" cls="bg-violet-700/60 text-violet-200" /> : null}
                  </div>
                </td>

                {/* L-signals / WLNBB / Ultra */}
                <td className="px-2 py-1">
                  <div className="flex flex-wrap gap-0.5">
                    {r.fri34            ? <Badge label="FRI34" cls="text-cyan-300 bg-cyan-900/40" /> : null}
                    {r.fri43            ? <Badge label="FRI43" cls="text-sky-300 bg-sky-900/40" /> : null}
                    {r.fri64            ? <Badge label="FRI64" cls="text-indigo-300 bg-indigo-900/40" /> : null}
                    {r.l34  && !r.fri34 ? <Badge label="L34"   cls="text-blue-300 bg-blue-900/30" /> : null}
                    {r.l43  && !r.fri43 ? <Badge label="L43"   cls="text-teal-300 bg-teal-900/30" /> : null}
                    {r.l64  && !r.fri64 ? <Badge label="L64"   cls="text-orange-400 bg-orange-900/30" /> : null}
                    {r.l22              ? <Badge label="L22"   cls="text-red-400 bg-red-900/30" /> : null}
                    {r.l555             ? <Badge label="L555"  cls="text-rose-400 bg-rose-900/30" /> : null}
                    {r.only_l2l4        ? <Badge label="L2L4"  cls="text-sky-400 bg-sky-900/20" /> : null}
                    {r.blue             ? <Badge label="BL"    cls="text-sky-300 bg-sky-900/30" /> : null}
                    {r.cci_ready        ? <Badge label="CCI"   cls="text-violet-300 bg-violet-900/30" /> : null}
                    {r.cci_0_retest     ? <Badge label="CCI0R" cls="text-violet-400 bg-violet-900/30" /> : null}
                    {r.cci_blue_turn    ? <Badge label="CCIB"  cls="text-purple-300 bg-purple-900/30" /> : null}
                    {r.bo_up            ? <Badge label="BO↑"   cls="text-lime-300 bg-lime-900/30" /> : null}
                    {r.bo_dn            ? <Badge label="BO↓"   cls="text-red-400 bg-red-900/20" /> : null}
                    {r.bx_up            ? <Badge label="BX↑"   cls="text-lime-400 bg-lime-900/20" /> : null}
                    {r.bx_dn            ? <Badge label="BX↓"   cls="text-red-400 bg-red-900/20" /> : null}
                    {r.be_up            ? <Badge label="BE↑"   cls="text-emerald-300 bg-emerald-900/30" /> : null}
                    {r.be_dn            ? <Badge label="BE↓"   cls="text-red-300 bg-red-900/20" /> : null}
                    {r.fuchsia_rh       ? <Badge label="RH"    cls="text-fuchsia-400 bg-fuchsia-900/30" /> : null}
                    {r.fuchsia_rl       ? <Badge label="RL"    cls="text-fuchsia-300 bg-fuchsia-900/20" /> : null}
                    {r.pre_pump         ? <Badge label="PP"    cls="text-yellow-400 bg-yellow-900/30" /> : null}
                    {r.x2g_wick ? <Badge label="X2G" cls="text-cyan-200 bg-cyan-800/60 ring-1 ring-cyan-400 font-bold" /> : null}
                    {r.x2_wick  ? <Badge label="X2"  cls="text-sky-200 bg-sky-800/50" /> : null}
                    {r.x1g_wick ? <Badge label="X1G" cls="text-lime-200 bg-lime-800/50 ring-1 ring-lime-400" /> : null}
                    {r.x1_wick  ? <Badge label="X1"  cls="text-green-200 bg-green-800/40" /> : null}
                    {r.x3_wick  ? <Badge label="X3"  cls="text-yellow-300 bg-yellow-900/40" /> : null}
                    {r.wick_bull        ? <Badge label="WK↑"   cls="text-emerald-300 bg-emerald-900/30" /> : null}
                    {/* Ultra v2 */}
                    {r.best_long  ? <Badge label="BEST↑" cls="text-yellow-200 bg-yellow-800/60 ring-1 ring-yellow-500" /> : null}
                    {r.fbo_bull && !r.best_long ? <Badge label="FBO↑" cls="text-sky-300 bg-sky-900/40" /> : null}
                    {r.fbo_bear   ? <Badge label="FBO↓" cls="text-red-300 bg-red-900/30" /> : null}
                    {r.eb_bull    ? <Badge label="EB↑"  cls="text-amber-300 bg-amber-900/30" /> : null}
                    {r.eb_bear    ? <Badge label="EB↓"  cls="text-red-300 bg-red-900/30" /> : null}
                    {r.bf_buy     ? <Badge label="4BF"  cls="text-pink-300 bg-pink-900/30" /> : null}
                    {r.bf_sell    ? <Badge label="4BF↓" cls="text-red-300 bg-red-900/30" /> : null}
                    {r.ultra_3up  ? <Badge label="3↑"   cls="text-lime-300 bg-lime-900/20" /> : null}
                    {/* 260308 */}
                    {r.sig_l88    ? <Badge label="L88"  cls="text-violet-200 bg-violet-800/50 ring-1 ring-violet-500" /> : null}
                    {r.sig_260308 && !r.sig_l88 ? <Badge label="260308" cls="text-purple-300 bg-purple-900/30" /> : null}
                  </div>
                </td>

                {/* Profile Score */}
                <td className="px-2 py-1 text-center font-mono text-xs text-md-on-surface-var"
                  title={r.matched_profile_pairs?.length ? `Pairs: ${r.matched_profile_pairs.join(', ')}` : r.profile_name}>
                  {r.profile_score != null ? r.profile_score : '—'}
                </td>

                {/* Profile Category */}
                <td className="px-2 py-1 text-center">
                  {r.profile_category ? (() => {
                    const cfg = {
                      SWEET_SPOT: { bg: 'bg-green-900/60', text: 'text-green-300', border: 'border-green-700/60', icon: '⭐' },
                      BUILDING:   { bg: 'bg-yellow-900/40', text: 'text-yellow-300', border: 'border-yellow-700/40', icon: '↑' },
                      WATCH:      { bg: 'bg-md-surface-high/60',   text: 'text-md-on-surface-var',  border: 'border-md-outline-var/40',  icon: '' },
                      LATE:       { bg: 'bg-amber-900/50',  text: 'text-amber-300', border: 'border-amber-700/60', icon: '⚠' },
                    }[r.profile_category] || { bg: 'bg-md-surface-high', text: 'text-md-on-surface-var', border: 'border-md-outline-var', icon: '' }
                    return (
                      <span className={`text-xs px-1.5 py-0.5 rounded border font-medium ${cfg.bg} ${cfg.text} ${cfg.border}`}
                        title={`Profile: ${r.profile_name}${r.profile_experimental ? ' (experimental)' : ''}${r.matched_profile_signals?.length ? `\nSignals: ${r.matched_profile_signals.join(', ')}` : ''}`}>
                        {cfg.icon}{cfg.icon ? ' ' : ''}{r.profile_category}
                      </span>
                    )
                  })() : '—'}
                </td>

                {/* RSI */}
                <td className={`px-2 py-1 text-center font-mono text-xs
                  ${r.rsi >= 70 ? 'text-red-400' : r.rsi <= 30 ? 'text-lime-400' : 'text-md-on-surface-var'}`}>
                  {r.rsi != null ? fmt(r.rsi, 0) : '—'}
                </td>

                {/* CCI */}
                <td className={`px-2 py-1 text-center font-mono text-xs
                  ${r.cci >= 100 ? 'text-lime-400' : r.cci <= -100 ? 'text-red-400' : 'text-md-on-surface-var'}`}>
                  {r.cci != null ? fmt(r.cci, 0) : '—'}
                </td>

                {/* Price */}
                <td className="px-2 py-1 text-right font-mono text-md-on-surface">
                  ${fmt(r.last_price)}
                </td>

                {/* Change % */}
                <td className={`px-2 py-1 text-right font-mono ${r.change_pct >= 0 ? 'text-lime-400' : 'text-red-400'}`}>
                  {r.change_pct >= 0 ? '+' : ''}{fmt(r.change_pct)}%
                  {r.avg_vol > 0 && (
                    <span className="ml-1 text-md-on-surface-var/70 font-normal text-xs">
                      {r.avg_vol >= 1_000_000
                        ? `${(r.avg_vol/1_000_000).toFixed(1)}M`
                        : r.avg_vol >= 1_000
                          ? `${Math.round(r.avg_vol/1_000)}K`
                          : Math.round(r.avg_vol)}
                    </span>
                  )}
                </td>

                {/* Split lifecycle badge (only when SPLIT universe) */}
                {universe === 'split' && (
                  <td className="px-2 py-1 text-center font-mono text-xs">
                    {r.split_date ? (() => {
                      const phase = r.split_phase || ''
                      const wave  = r.split_wave  || ''
                      const doff  = r.split_days_offset ?? 0
                      const heat  = r.split_heat_score  ?? 0
                      const dLabel = doff === 0 ? 'D0' : doff > 0 ? `D+${doff}` : `D${doff}`
                      const phaseColors = {
                        PRE_SPLIT:        'bg-blue-900/40 text-blue-300 border-blue-700/50',
                        SPLIT_DAY:        'bg-purple-900/50 text-purple-200 border-purple-600/60',
                        WAVE_1:           'bg-green-900/50 text-green-300 border-green-600/60',
                        WAVE_2_SETUP:     'bg-teal-900/40 text-teal-300 border-teal-700/50',
                        WAVE_3_SETUP:     'bg-cyan-900/40 text-cyan-300 border-cyan-700/50',
                        POST_MONITOR:     'bg-md-surface-high/60 text-md-on-surface-var border-gray-600/40',
                        EXTENDED_MONITOR: 'bg-md-surface-con/60 text-md-on-surface-var border-md-outline-var/30',
                      }
                      const colorCls = (phaseColors[phase] || 'bg-md-surface-high/40 text-md-on-surface-var border-gray-600/40') +
                        (heat >= 6 ? ' ring-1 ring-yellow-500/40' : '')
                      const tooltip = [
                        `Split: ${r.split_date}`,
                        `Ratio: ${r.split_ratio}`,
                        `Phase: ${phase} (${wave})`,
                        `Offset: ${dLabel}`,
                        r.split_next_wave_label ? `Next: ${r.split_next_wave_label} ${r.split_next_wave_start || ''}→${r.split_next_wave_end || ''}` : null,
                        `Watch until: ${r.split_watch_until || '—'}`,
                        `Heat score: ${heat}/10`,
                        r.split_notes || null,
                      ].filter(Boolean).join('\n')
                      return (
                        <span className={`px-1.5 py-0.5 rounded border font-semibold ${colorCls}`}
                          title={tooltip}>
                          {r.split_ratio}
                          <span className="ml-1 font-normal opacity-80">{wave}</span>
                          <span className="ml-1 text-md-on-surface-var font-normal">{dLabel}</span>
                        </span>
                      )
                    })() : '—'}
                  </td>
                )}
              </tr>
              )
            })}

          </tbody>
        </table>
      </div>}
    </div>
  )
}
