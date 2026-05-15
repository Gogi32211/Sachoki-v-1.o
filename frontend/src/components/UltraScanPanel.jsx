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

// ── ULTRA Score colour helper (replay v2 calibration) ───────────────────────
// 90+ A+ HIGH_PRIORITY → strongest visual treatment (replay edge concentrated
// here). 80–89 A WATCH_A. 65–79 B STRONG_WATCH. 50–64 C CONTEXT_WATCH. <50 D LOW.
function ultraScoreCls(s) {
  if (s == null) return 'text-gray-700'
  if (s >= 90) return 'text-emerald-200 font-extrabold drop-shadow-[0_0_3px_rgba(110,231,183,0.45)]'
  if (s >= 80) return 'text-emerald-300 font-bold'
  if (s >= 65) return 'text-teal-300 font-semibold'
  if (s >= 50) return 'text-yellow-200/90'
  return 'text-md-on-surface-var'
}

// v2 band/priority labels — UI prefers v2 over old A/B/C/D when present.
function ultraBandV2Label(s, fallback) {
  if (s == null) return fallback || ''
  if (s >= 90) return 'A+'
  if (s >= 80) return 'A'
  if (s >= 65) return 'B'
  if (s >= 50) return 'C'
  return 'D'
}
function ultraPriorityLabel(s) {
  if (s == null) return ''
  if (s >= 90) return 'HIGH_PRIORITY'
  if (s >= 80) return 'WATCH_A'
  if (s >= 65) return 'STRONG_WATCH'
  if (s >= 50) return 'CONTEXT_WATCH'
  return 'LOW'
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

// ── Compact-label maps for Pullback / Rare Reversal cells (and CSV) ──────────
const PULLBACK_COMPACT = {
  ANECDOTAL_PULLBACK: 'APB',
  CONFIRMED_PULLBACK: 'CPB',
  READY_PULLBACK:     'RPB',
  FORMING_PULLBACK:   'FPB',
  GO_PULLBACK:        'GPB',
  WATCH_PULLBACK:     'WPB',
}
function pullbackCompact(p) {
  if (!p) return ''
  const tier = (p.evidence_tier || '').toUpperCase()
  if (PULLBACK_COMPACT[tier]) return PULLBACK_COMPACT[tier]
  // Fallback: first letter of each underscore-separated token
  return tier.split('_').map(t => t.slice(0, 1)).join('')
}

const RARE_COMPACT = {
  FORMING_PATTERN:   'FP',
  CONFIRMED_PATTERN: 'CP',
  CONFIRMED_RARE:    'CP',
  READY_PATTERN:     'RP',
  ACTIVE_PATTERN:    'AP',
  WATCH_PATTERN:     'WP',
  ANECDOTAL_RARE:    'AR',
}
function rareCompact(r) {
  if (!r) return ''
  const tier = (r.evidence_tier || '').toUpperCase()
  if (RARE_COMPACT[tier]) return RARE_COMPACT[tier]
  return tier.split('_').map(t => t.slice(0, 1)).join('')
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

// ── ULTRA scan localStorage cache (separate keyspace from Turbo) ─────────────
const _tsKey  = (tf, uni) => `sachoki_ultra_${tf}_${uni}`
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

function ultraCacheSet(tf, uni, results, lastScan) {
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

const _tsSet = ultraCacheSet

// ─────────────────────────────────────────────────────────────────────────────
const _initTf  = () => { try { return localStorage.getItem('sachoki_ultra_tf')  || '1d'    } catch { return '1d'    } }
const _initUni = () => { try { return localStorage.getItem('sachoki_ultra_uni') || 'sp500' } catch { return 'sp500' } }

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

export default function UltraScanPanel({ onSelectTicker }) {
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
  const [expandedRows, setExpandedRows] = useState(new Set())  // tickers with open sub-row
  const [showAdvanced, setShowAdvanced] = useState(false)      // collapsible adv filters
  const hoverTimer = useRef(null)

  // which TFs have a cache entry for current universe
  const tfCached = useMemo(
    () => Object.fromEntries(TF_OPTS.map(t => [t, !!_tsGet(t, universe)?.results?.length])),
    [universe, allResults]  // re-check when results change (after scan saves cache)
  )

  // Fetch fresh results from server after a scan completes, then cache.
  // Uses fetchSeqRef to discard responses from stale (superseded) requests.
  const fetchFreshResults = useCallback((tf, uni) => {
    const seq = ++fetchSeqRef.current
    api.ultraScanResults(uni, tf)
      .then(d => {
        if (seq !== fetchSeqRef.current) return  // superseded by a newer fetch
        const results = d.results || []
        const ls = d.last_scan
        // ULTRA results may be empty before the first scan — that's OK, don't error
        if (results.length > 0) {
          ultraCacheSet(tf, uni, results, ls)
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

  // ULTRA does not subscribe to the admin "sachoki:scan-cached" Turbo event —
  // that event delivers raw Turbo rows, which would overwrite ULTRA's enriched
  // rows. ULTRA refreshes via its own /api/ultra-scan/results endpoint.

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

  // CSV cell sanitiser.
  // Numeric values pass through unchanged (negative numbers must NOT be
  // prefixed with apostrophe — that's a regression that breaks Excel/Sheets
  // numeric columns). Only strings starting with =,+,-,@ get the prefix,
  // and only when the string is not a parseable number.
  const _csvCell = (v) => {
    if (v == null) return ''
    if (Array.isArray(v))     return _csvCell(v.join(';'))
    if (typeof v === 'number') return Number.isFinite(v) ? String(v) : ''
    if (typeof v === 'boolean') return v ? 'true' : 'false'
    let s = String(v)
    // If the string is actually a number (including negatives like "-139.4"),
    // leave it as-is — it's a numeric value, not a formula.
    const isNumeric = s !== '' && !isNaN(Number(s)) && /^-?\d/.test(s)
    if (!isNumeric && /^[=+\-@]/.test(s)) s = "'" + s
    return s.includes(',') || s.includes('"') || s.includes('\n')
      ? `"${s.replace(/"/g, '""')}"` : s
  }

  // Display-column generators — mirror exactly what each table cell renders
  // so the CSV reflects what's visible on screen, badge-for-badge.
  const _displayRtb = (r) => {
    if (!r.rtb_phase || r.rtb_phase === '0') return ''
    return `${r.rtb_phase} ${(r.rtb_total ?? 0).toFixed(0)}`
  }
  const _displayTz = (r) => r.tz_sig || ''
  const _displayGog = (r) => {
    const parts = []
    if (r.gog_tier) parts.push(r.gog_tier)
    parts.push(...ctxTokens(r))
    if ((r.signal_score ?? 0) > 0) parts.push(`SCORE=${r.signal_score}`)
    if (r.already_extended) parts.push('EXT')
    return parts.join(' | ')
  }
  const _displayVabs = (r) => {
    const out = []
    if (r.vol_spike_20x) out.push('V×20')
    else if (r.vol_spike_10x) out.push('V×10')
    else if (r.vol_spike_5x)  out.push('V×5')
    if (r.best_sig)   out.push('BEST★')
    if (r.strong_sig && !r.best_sig) out.push('STR')
    if (r.vbo_up)     out.push('VBO↑')
    if (r.vbo_dn)     out.push('VBO↓')
    if (r.abs_sig)    out.push('ABS')
    if (r.climb_sig)  out.push('CLB')
    if (r.load_sig)   out.push('LD')
    if (r.d_blast_bull)  out.push('ΔΔ↑')
    if (r.d_surge_bull && !r.d_blast_bull) out.push('Δ↑')
    if (r.d_strong_bull) out.push('B/S↑')
    if (r.d_absorb_bull) out.push('Ab↑')
    if (r.d_div_bull)    out.push('T↓')
    if (r.d_cd_bull && !r.d_div_bull) out.push('cd↑')
    if (r.d_spring)      out.push('dSPR')
    if (r.d_vd_div_bull && !r.d_spring) out.push('NS')
    if (r.rs_strong)     out.push('RS+')
    else if (r.rs)       out.push('RS')
    const PREUP = [['preup66','P66'],['preup55','P55'],['preup89','P89'],
                    ['preup3','P3'],['preup2','P2'],['preup50','P50']]
    for (const [k, lbl] of PREUP) if (r[k]) { out.push(lbl); break }
    const PREDN = [['predn66','D66'],['predn55','D55'],['predn89','D89'],
                    ['predn3','D3'],['predn2','D2'],['predn50','D50']]
    for (const [k, lbl] of PREDN) if (r[k]) { out.push(lbl); break }
    return out.join(' | ')
  }
  const _displayWyck = (r) => {
    const out = []
    if (r.ns) out.push('NS')
    if (r.sq) out.push('SQ')
    if (r.sc) out.push('SC')
    if (r.bc) out.push('BC')
    if (r.nd) out.push('ND')
    return out.join(' | ')
  }
  const _displayCombo = (r) => {
    const out = []
    if (r.rocket)    out.push('🚀')
    if (r.buy_2809)  out.push('BUY')
    if (r.sig3g)     out.push('3G')
    if (r.rtv)       out.push('RTV')
    if (r.hilo_buy)  out.push('HILO↑')
    if (r.atr_brk)   out.push('ATR↑')
    if (r.bb_brk)    out.push('BB↑')
    if (r.va)        out.push('VA')
    if (r.bias_up)   out.push('↑BIAS')
    if (r.hilo_sell) out.push('HILO↓')
    if (r.bias_down) out.push('↓BIAS')
    if (r.um_2809)    out.push('UM')
    if (r.svs_2809)   out.push('SVS')
    if (r.conso_2809) out.push('CON')
    if (r.cd) out.push('CD')
    else if (r.ca) out.push('CA')
    else if (r.cw) out.push('CW')
    if (r.any_f) out.push('ANY F')
    for (const k of ['f1','f2','f3','f4','f5','f6','f7','f8','f9','f10','f11'])
      if (r[k]) out.push(k.toUpperCase())
    for (const k of ['g1','g2','g4','g6','g11'])
      if (r[k]) out.push(k.toUpperCase())
    if (r.seq_bcont) out.push('SBC')
    for (const k of ['b1','b2','b3','b4','b5','b6','b7','b8','b9','b10','b11'])
      if (r[k]) out.push(k.toUpperCase())
    if (r.tz_bull_flip) out.push('TZ→3')
    else if (r.tz_attempt) out.push('TZ→2')
    if (r.smx)            out.push('SMX')
    if (r.rgti_ll)        out.push('LL')
    if (r.rgti_up)        out.push('UP')
    if (r.rgti_upup)      out.push('↑↑')
    if (r.rgti_upupup)    out.push('↑↑↑')
    if (r.rgti_orange)    out.push('ORG')
    if (r.rgti_green)     out.push('GRN')
    if (r.rgti_greencirc) out.push('GC')
    if (r.para_plus) out.push('PARA+')
    else if (r.para_start) out.push('PARA')
    if (r.para_prep)   out.push('PREP')
    if (r.para_retest) out.push('RTEST')
    if (r.akan_sig) out.push('A')
    if (r.smx_sig)  out.push('SM')
    if (r.nnn_sig)  out.push('N')
    if (r.mx_sig)   out.push('MX')
    if (r.gog_sig)  out.push('GOG')
    if (r.fly_abcd) out.push('ABCD')
    else {
      if (r.fly_cd) out.push('CD')
      if (r.fly_bd) out.push('BD')
      if (r.fly_ad) out.push('AD')
    }
    return out.join(' | ')
  }
  const _displayLsigUltra = (r) => {
    const out = []
    if (r.fri34) out.push('FRI34')
    if (r.fri43) out.push('FRI43')
    if (r.fri64) out.push('FRI64')
    if (r.l34  && !r.fri34) out.push('L34')
    if (r.l43  && !r.fri43) out.push('L43')
    if (r.l64  && !r.fri64) out.push('L64')
    if (r.l22)       out.push('L22')
    if (r.l555)      out.push('L555')
    if (r.only_l2l4) out.push('L2L4')
    if (r.blue)         out.push('BL')
    if (r.cci_ready)    out.push('CCI')
    if (r.cci_0_retest) out.push('CCI0R')
    if (r.cci_blue_turn)out.push('CCIB')
    if (r.bo_up) out.push('BO↑')
    if (r.bo_dn) out.push('BO↓')
    if (r.bx_up) out.push('BX↑')
    if (r.bx_dn) out.push('BX↓')
    if (r.be_up) out.push('BE↑')
    if (r.be_dn) out.push('BE↓')
    if (r.fuchsia_rh) out.push('RH')
    if (r.fuchsia_rl) out.push('RL')
    if (r.pre_pump)   out.push('PP')
    if (r.x2g_wick) out.push('X2G')
    if (r.x2_wick)  out.push('X2')
    if (r.x1g_wick) out.push('X1G')
    if (r.x1_wick)  out.push('X1')
    if (r.x3_wick)  out.push('X3')
    if (r.wick_bull) out.push('WK↑')
    if (r.best_long) out.push('BEST↑')
    else if (r.fbo_bull) out.push('FBO↑')
    if (r.fbo_bear) out.push('FBO↓')
    if (r.eb_bull)  out.push('EB↑')
    if (r.eb_bear)  out.push('EB↓')
    if (r.bf_buy)   out.push('4BF')
    if (r.bf_sell)  out.push('4BF↓')
    if (r.ultra_3up) out.push('3↑')
    if (r.sig_l88)   out.push('L88')
    else if (r.sig_260308) out.push('260308')
    return out.join(' | ')
  }
  const _displayCategory = (r) => r.profile_category || ''
  const _displayProfile = (r) => {
    const parts = []
    if (r.profile_score != null) parts.push(`PF=${r.profile_score}`)
    if (r.profile_category)      parts.push(r.profile_category)
    if (r.profile_name)          parts.push(r.profile_name)
    if (r.sweet_spot_active)     parts.push('SWEET')
    if (r.late_warning)          parts.push('LATE')
    return parts.join(' | ')
  }

  // ULTRA export: CSV including Turbo fields + enrichment columns (read-only)
  const exportTickers = () => {
    const src = pickedTickers.size > 0
      ? results.filter(r => pickedTickers.has(r.ticker))
      : results

    // Display columns — one per visible badge group, joined by " | "
    const DISPLAY_FIELDS = [
      'turbo_rtb_display', 'turbo_tz_display', 'turbo_gog_display',
      'turbo_vabs_display', 'turbo_wyck_display', 'turbo_combo_display',
      'turbo_lsig_ultra_display',
      'turbo_category_display', 'turbo_profile_display',
      // ULTRA Score + compact pullback / rare labels (v2 calibration adds
      // band_v2/priority/regime_bonus/caps so CSV consumers see the same
      // fields the live UI reads).
      'ultra_score', 'ultra_score_band', 'ultra_score_band_v2',
      'ultra_score_priority', 'ultra_score_regime_bonus',
      'ultra_score_caps_applied', 'ultra_score_cap_reason',
      'ultra_score_reasons',
      'pullback_display_compact', 'rare_reversal_display_compact',
    ]

    // Core / category fields visible in the table
    const CORE_FIELDS = [
      'ticker', 'turbo_score', 'turbo_score_n3', 'turbo_score_n5', 'turbo_score_n10',
      'rtb_total', 'rtb_phase', 'tz_sig', 'tz_bull',
      'signal_score', 'profile_score', 'profile_category', 'profile_name',
      'sweet_spot_active', 'late_warning', 'gog_tier',
      'rsi', 'cci', 'last_price', 'change_pct', 'avg_vol', 'vol_bucket',
      'sector', 'data_source',
      // BETA Score
      'beta_score', 'beta_raw', 'beta_setup', 'beta_momentum',
      'beta_excess', 'beta_zone', 'beta_auto_buy',
    ]

    // Raw signal fields behind every visible Turbo badge (boolean / numeric).
    // Order grouped by family for readability — does not affect correctness.
    const RAW_TURBO_FIELDS = [
      // GOG / setup
      'gog_sig','gog_score','gog_g1p','gog_g2p','gog_g3p',
      'gog_g1l','gog_g2l','gog_g3l','gog_g1c','gog_g2c','gog_g3c',
      'gog_gog1','gog_gog2','gog_gog3',
      'smx_sig','akan_sig','nnn_sig','mx_sig','already_extended',
      // VABS / Wyckoff
      'best_sig','strong_sig','vbo_up','vbo_dn','abs_sig','climb_sig','load_sig',
      'ns','nd','sc','bc','sq','va',
      'vol_spike_5x','vol_spike_10x','vol_spike_20x',
      // Combo / 2809 / trend
      'buy_2809','rocket','sig3g','rtv','hilo_buy','hilo_sell',
      'atr_brk','bb_brk','bias_up','bias_down','cons_atr',
      'um_2809','svs_2809','conso_2809',
      'ca','cd','cw','seq_bcont','any_f',
      'f1','f2','f3','f4','f5','f6','f7','f8','f9','f10','f11',
      // B
      'b1','b2','b3','b4','b5','b6','b7','b8','b9','b10','b11',
      // G
      'g1','g2','g4','g6','g11',
      // T/Z
      'tz_state','tz_bull_flip','tz_attempt','tz_weak_bull','tz_weak_bear',
      // WLNBB / L-Sig
      'fri34','fri43','fri64','l34','l43','l64','l22','l555','only_l2l4',
      'blue','cci_ready','cci_0_retest','cci_blue_turn',
      'fuchsia_rh','fuchsia_rl','pre_pump',
      // Breakout / Ultra-v2
      'eb_bull','eb_bear','fbo_bull','fbo_bear','bf_buy','bf_sell',
      'ultra_3up','ultra_3dn','best_long','best_short',
      'bo_up','bo_dn','bx_up','bx_dn','be_up','be_dn',
      'sig_260308','sig_l88',
      // Delta / order-flow
      'd_strong_bull','d_strong_bear','d_absorb_bull','d_absorb_bear',
      'd_div_bull','d_div_bear','d_cd_bull','d_cd_bear',
      'd_surge_bull','d_surge_bear','d_blast_bull','d_blast_bear',
      'd_vd_div_bull','d_vd_div_bear','d_spring','d_upthrust',
      'd_flip_bull','d_flip_bear','d_orange_bull',
      'd_blast_bull_red','d_blast_bear_grn',
      'd_surge_bull_red','d_surge_bear_grn',
      // Wick
      'wick_bull','wick_bear','x2g_wick','x2_wick','x1g_wick','x1_wick','x3_wick',
      // RTB
      'rtb_build','rtb_turn','rtb_ready','rtb_bonus3','rtb_late',
      'rtb_transition','rtb_phase_age',
      // RGTI / SMX / PARA / FLY
      'rgti_ll','rgti_up','rgti_upup','rgti_upupup','rgti_orange','rgti_green','rgti_greencirc',
      'smx',
      'para_prep','para_start','para_plus','para_retest',
      'fly_abcd','fly_cd','fly_bd','fly_ad',
      // PREUP / PREDN
      'preup66','preup55','preup89','preup3','preup2','preup50',
      'predn66','predn55','predn89','predn3','predn2','predn50',
      // RS
      'rs','rs_strong',
    ]

    // ULTRA enrichment fields — unchanged
    const ULTRA_FIELDS = [
      'ultra_enriched',
      'ultra_has_turbo', 'ultra_has_tz_wlnbb', 'ultra_has_tz_intel',
      'ultra_has_pullback', 'ultra_has_rare_reversal',
      'tz_wlnbb_t_signal', 'tz_wlnbb_z_signal', 'tz_wlnbb_l_signal',
      'tz_wlnbb_preup_signal', 'tz_wlnbb_predn_signal',
      'tz_wlnbb_lane1_label', 'tz_wlnbb_lane3_label',
      'tz_wlnbb_volume_bucket', 'tz_wlnbb_wick_suffix',
      'tz_intel_role', 'tz_intel_quality', 'tz_intel_action', 'tz_intel_score',
      'tz_intel_matched_status', 'tz_intel_matched_med10d_pct', 'tz_intel_matched_fail10d_pct',
      'abr_category', 'abr_med10d_pct', 'abr_fail10d_pct',
      'abr_context_type', 'abr_action_hint', 'abr_conflict_flag', 'abr_confirmation_flag',
      'pullback_evidence_tier', 'pullback_pullback_stage', 'pullback_pattern_key',
      'pullback_pattern_length', 'pullback_score',
      'pullback_median_10d_return', 'pullback_win_rate_10d', 'pullback_fail_rate_10d',
      'pullback_is_currently_active', 'pullback_current_pattern_completion',
      'rare_evidence_tier', 'rare_base4_key', 'rare_extended5_key', 'rare_extended6_key',
      'rare_pattern_length', 'rare_score',
      'rare_median_10d_return', 'rare_fail_rate_10d',
      'rare_is_currently_active', 'rare_current_pattern_completion',
    ]

    const COLS = [...DISPLAY_FIELDS, ...CORE_FIELDS, ...RAW_TURBO_FIELDS, ...ULTRA_FIELDS]

    const flatten = (r) => {
      const u = r.ultra_sources || {}
      const w = r.tz_wlnbb || {}
      const i = r.tz_intel || {}
      const a = r.abr      || {}
      const p = r.pullback || {}
      const x = r.rare_reversal || {}
      const flat = {}

      // Display columns
      flat.turbo_rtb_display        = _displayRtb(r)
      flat.turbo_tz_display         = _displayTz(r)
      flat.turbo_gog_display        = _displayGog(r)
      flat.turbo_vabs_display       = _displayVabs(r)
      flat.turbo_wyck_display       = _displayWyck(r)
      flat.turbo_combo_display      = _displayCombo(r)
      flat.turbo_lsig_ultra_display = _displayLsigUltra(r)
      flat.turbo_category_display   = _displayCategory(r)
      flat.turbo_profile_display    = _displayProfile(r)

      // ULTRA Score fields (computed on the backend) + compact tier displays
      flat.ultra_score              = r.ultra_score ?? ''
      flat.ultra_score_band         = r.ultra_score_band ?? ''
      flat.ultra_score_band_v2      = r.ultra_score_band_v2
        ?? ultraBandV2Label(r.ultra_score, r.ultra_score_band) ?? ''
      flat.ultra_score_priority     = r.ultra_score_priority
        ?? ultraPriorityLabel(r.ultra_score) ?? ''
      flat.ultra_score_regime_bonus = r.ultra_score_regime_bonus ?? ''
      flat.ultra_score_caps_applied = Array.isArray(r.ultra_score_caps_applied)
        ? r.ultra_score_caps_applied.join(' ')
        : (r.ultra_score_caps_applied ?? '')
      flat.ultra_score_cap_reason   = r.ultra_score_cap_reason ?? ''
      flat.ultra_score_reasons      = r.ultra_score_reasons ?? ''
      flat.pullback_display_compact      = pullbackCompact(r.pullback)
      flat.rare_reversal_display_compact = rareCompact(r.rare_reversal)

      // Pass-through core + raw fields. Numerics stay numeric so _csvCell
      // doesn't mangle them.
      for (const k of CORE_FIELDS)      flat[k] = r[k]
      for (const k of RAW_TURBO_FIELDS) flat[k] = r[k]

      // ULTRA flags / enrichment slots
      flat.ultra_enriched           = !!r.ultra_enriched
      flat.ultra_has_turbo          = !!u.has_turbo
      flat.ultra_has_tz_wlnbb       = !!u.has_tz_wlnbb
      flat.ultra_has_tz_intel       = !!u.has_tz_intel
      flat.ultra_has_pullback       = !!u.has_pullback
      flat.ultra_has_rare_reversal  = !!u.has_rare_reversal
      for (const [k, v] of Object.entries(w)) flat[`tz_wlnbb_${k}`] = v
      flat.tz_intel_role                = i.role
      flat.tz_intel_quality             = i.quality
      flat.tz_intel_action              = i.action
      flat.tz_intel_score               = i.score
      flat.tz_intel_matched_status      = i.matched_status
      flat.tz_intel_matched_med10d_pct  = i.matched_med10d_pct
      flat.tz_intel_matched_fail10d_pct = i.matched_fail10d_pct
      flat.abr_category         = a.category
      flat.abr_med10d_pct       = a.med10d_pct
      flat.abr_fail10d_pct      = a.fail10d_pct
      flat.abr_context_type     = a.context_type
      flat.abr_action_hint      = a.action_hint
      flat.abr_conflict_flag    = !!a.conflict_flag
      flat.abr_confirmation_flag= !!a.confirmation_flag
      flat.pullback_evidence_tier              = p.evidence_tier
      flat.pullback_pullback_stage             = p.pullback_stage
      flat.pullback_pattern_key                = p.pattern_key
      flat.pullback_pattern_length             = p.pattern_length
      flat.pullback_score                      = p.score
      flat.pullback_median_10d_return          = p.median_10d_return
      flat.pullback_win_rate_10d               = p.win_rate_10d
      flat.pullback_fail_rate_10d              = p.fail_rate_10d
      flat.pullback_is_currently_active        = !!p.is_currently_active
      flat.pullback_current_pattern_completion = p.current_pattern_completion
      flat.rare_evidence_tier              = x.evidence_tier
      flat.rare_base4_key                  = x.base4_key
      flat.rare_extended5_key              = x.extended5_key
      flat.rare_extended6_key              = x.extended6_key
      flat.rare_pattern_length             = x.pattern_length
      flat.rare_score                      = x.score
      flat.rare_median_10d_return          = x.median_10d_return
      flat.rare_fail_rate_10d              = x.fail_rate_10d
      flat.rare_is_currently_active        = !!x.is_currently_active
      flat.rare_current_pattern_completion = x.current_pattern_completion
      return flat
    }

    const lines = [COLS.join(',')]
    for (const r of src) {
      const flat = flatten(r)
      lines.push(COLS.map(c => _csvCell(flat[c])).join(','))
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    const date = new Date().toISOString().slice(0, 10)
    a.href     = url

    const parts = [universe, localTf.toUpperCase()]
    if (direction !== 'all') parts.push(direction.toUpperCase())
    if (!scoreBands.has('all') && scoreBands.size > 0) {
      parts.push([...scoreBands].join('+'))
    }
    if (pickedTickers.size > 0) parts.push(`picked${pickedTickers.size}`)
    parts.push(date)
    a.download = `ultra_${parts.join('_')}.csv`
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

  // ULTRA scan progress: phase + per-source state + enrich stage
  const [phase, setPhase]       = useState(null)
  const [phases, setPhases]     = useState({})
  const [sources, setSources]   = useState({})
  const [warnings, setWarnings] = useState([])
  const [stage,  setStage]      = useState(null)   // 'turbo' | 'enrich' | null
  const [enriching, setEnriching] = useState(false)

  const _poll = () => {
    _stopPoll()  // kill any previous poll before starting a new one
    const activeTf = localTf
    const uni      = universe
    pollIvRef.current = setInterval(() => {
      api.ultraScanStatus()
        .then(s => {
          setPhase(s.phase || null)
          setPhases(s.phases || {})
          setWarnings(s.warnings || [])
          setSources(s.sources || {})
          setStage(s.stage || null)
          if (!s.running) {
            _stopPoll(); setScanning(false); setEnriching(false)
            if (s.error) setError(s.error)
            else fetchFreshResults(activeTf, uni)
          }
        })
        .catch(() => { _stopPoll(); setScanning(false); setEnriching(false) })
    }, 2000)
    setTimeout(() => { _stopPoll(); setScanning(false); setEnriching(false); fetchFreshResults(activeTf, uni) }, 600_000)
  }

  // Stage 2: enrich a subset of tickers. If user has rows checked (via the
  // checkbox column) we enrich those; otherwise we enrich whatever is
  // currently visible after filters. Never enriches the full universe.
  const enrich = () => {
    if (scanning || enriching) return
    const targetTickers = pickedTickers.size > 0
      ? results.filter(r => pickedTickers.has(r.ticker)).map(r => r.ticker)
      : results.map(r => r.ticker)
    if (!targetTickers.length) {
      setError('No tickers to enrich — run ULTRA Scan first or adjust filters.')
      return
    }
    setEnriching(true); setError(null)
    setWarnings([]); setPhase(null)
    // Reset Phase 2 pills to 'pending' so the UI immediately reflects intent
    setPhases(prev => ({
      ...prev,
      stock_stat:      { state: 'pending', message: '' },
      tz_wlnbb:        { state: 'pending', message: '' },
      tz_intelligence: { state: 'pending', message: '' },
      pullback:        { state: 'pending', message: '' },
      rare_reversal:   { state: 'pending', message: '' },
      merge:           { state: 'pending', message: '' },
    }))
    api.ultraScanEnrich({
      universe, tf: localTf, tickers: targetTickers,
      direction, minPrice: 0, maxPrice: 1e9, minVolume: volMin,
    })
      .then(() => _poll())
      .catch(e => {
        setEnriching(false)
        const msg = e?.detail || e?.message || String(e)
        if (msg.includes('409') || msg.toLowerCase().includes('already running')) {
          setError('__stuck__')
        } else {
          setError(msg)
        }
      })
  }

  const scan = () => {
    if (scanning) return  // guard against double-trigger
    setScanning(true); setError(null); setWarnings([]); setSources({}); setPhases({}); setPhase(null)
    api.ultraScanTrigger(localTf, universe, {
      lookbackN, partialDay, minVolume: volMin,
      minStoreScore: getCacheBackend() === 'idb' ? 0 : 5,
    })
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
            onClick={() => { setUniverse(u.key); setAllResults([]); setLastScan(null); try { localStorage.setItem('sachoki_ultra_uni', u.key) } catch {} }}
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
            <button key={t} onClick={() => { setLocalTf(t); setLastScan(null); try { localStorage.setItem('sachoki_ultra_tf', t) } catch {} }}
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

        {/* Stage 1: Turbo-only scan button */}
        <button onClick={scan} disabled={scanning || enriching}
          title="Stage 1 — runs Turbo only. Use Enrich after to fill TZ/WLNBB/Intel/Pullback/Rare for the visible/selected subset."
          className={`px-3 py-1 rounded text-xs font-semibold transition-colors
            ${scanning ? 'bg-gray-700 text-md-on-surface-var cursor-not-allowed'
                       : 'bg-fuchsia-600 hover:bg-fuchsia-500 text-white'}`}>
          {scanning
            ? <span className="animate-pulse">🧬 Scanning…</span>
            : '🧬 ULTRA Scan'}
        </button>

        {/* Stage 2: enrich the visible / selected subset */}
        {(() => {
          const enrichCount = pickedTickers.size > 0 ? pickedTickers.size : results.length
          const enrichLabel = pickedTickers.size > 0
            ? `✨ Enrich ${enrichCount} selected`
            : `✨ Enrich ${enrichCount} visible`
          return (
            <button onClick={enrich} disabled={scanning || enriching || enrichCount === 0}
              title="Stage 2 — generates an ULTRA-private subset stock_stat for these tickers (extracted from canonical when present), then runs TZ/WLNBB + TZ Intel + Pullback + Rare Reversal."
              className={`px-3 py-1 rounded text-xs font-semibold transition-colors
                ${enriching ? 'bg-gray-700 text-md-on-surface-var cursor-not-allowed'
                  : enrichCount === 0
                    ? 'bg-md-surface-high text-md-on-surface-var/70 cursor-not-allowed border border-md-outline-var'
                    : pickedTickers.size > 0
                      ? 'bg-amber-600 hover:bg-amber-500 text-black'
                      : 'bg-emerald-700 hover:bg-emerald-600 text-white'}`}>
              {enriching
                ? <span className="animate-pulse">✨ Enriching…</span>
                : enrichLabel}
            </button>
          )
        })()}

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

      {/* ── Advanced Filters toggle (SIG / Sector / RTB Phase) ── */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-white/[0.07] bg-md-surface-con/20">
        <button
          onClick={() => setShowAdvanced(a => !a)}
          className={`px-2.5 py-0.5 rounded text-xs font-medium shrink-0 transition-colors border ${
            showAdvanced || selSigs.size > 0 || secFilter || rtbPhase
              ? 'bg-indigo-900/50 text-indigo-300 border-indigo-600'
              : 'bg-md-surface-high text-md-on-surface-var border-md-outline-var hover:text-white'
          }`}>
          Advanced Filters {showAdvanced ? '▴' : '▾'}{selSigs.size > 0 ? ` · ${selSigs.size} sig` : ''}{secFilter ? ' · sector' : ''}{rtbPhase ? ` · RTB:${rtbPhase}` : ''}
        </button>
        {(selSigs.size > 0 || secFilter || rtbPhase) && (
          <button onClick={() => { setSelSigs(new Set()); setSecFilter(''); setRtbPhase('') }}
            className="px-2 py-0.5 rounded text-xs shrink-0 bg-red-900/40 text-red-400 hover:bg-red-900/60">
            ✕ clear adv
          </button>
        )}
      </div>

      {/* ── Advanced Filters (collapsible) ── */}
      {showAdvanced && (
        <div className="border-b border-white/[0.07] bg-md-surface-con/30">
          <div className="flex flex-wrap items-center gap-x-1 gap-y-1 px-3 py-2 border-b border-white/[0.07]/30">
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
            {selSigs.size > 0 && (
              <button onClick={() => setSelSigs(new Set())}
                className="ml-2 px-2 py-0.5 rounded text-xs shrink-0 bg-red-900/40 text-red-400 hover:bg-red-900/60">
                ✕ clear
              </button>
            )}
          </div>

          {/* Sector filter */}
          <div className="flex flex-wrap items-center gap-x-1 gap-y-1 px-3 py-1.5 border-b border-white/[0.07]/30">
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
            {secFilter && Object.keys(sectorMap).length === 0 && allResults.some(r => !r.sector) && (
              <span className="ml-1 text-md-on-surface-var/70 text-xs animate-pulse">
                — loading sectors…
              </span>
            )}
          </div>

          {/* RTB Phase filter */}
          <div className="flex flex-wrap items-center gap-x-1 gap-y-1 px-3 py-1.5">
            <span className="text-md-on-surface-var text-xs shrink-0 mr-0.5 w-16">RTB Phase</span>
        {[
              { label: 'All',    val: '', cls: 'text-md-on-surface-var' },
              { label: 'A — Build',    val: 'A', cls: 'text-md-on-surface',
                title: 'Build phase: base forming, drying volume, accumulation — no turn yet' },
              { label: 'B — Turn',     val: 'B', cls: 'text-sky-300',
                title: 'Turn phase: first real reversal bar, momentum changed — not yet breakout-ready' },
              { label: 'C — Ready',    val: 'C', cls: 'text-lime-300',
                title: 'Ready phase: prime pre-breakout stalking zone (1-3 bars before breakout)' },
              { label: 'D — Late',     val: 'D', cls: 'text-orange-300',
                title: 'Late/breakout-live: move already launching — chase risk high' },
            ].map(s => (
              <button key={s.val} onClick={() => setRtbPhase(s.val)}
                title={s.title}
                className={`px-2 py-0.5 rounded text-xs shrink-0 transition-colors
                  ${rtbPhase === s.val
                    ? `${s.cls} bg-gray-700 font-semibold ring-1 ring-gray-500`
                    : 'bg-md-surface-high text-md-on-surface-var hover:text-white'}`}>
                {s.label}
              </button>
            ))}
            {rtbPhase && (
              <span className="ml-2 text-md-on-surface-var/70 text-xs">
                {results.length} ticker{results.length !== 1 ? 's' : ''}
              </span>
            )}
          </div>
        </div>
      )}

      {/* ── Row 5: Profile Sweet Spot filter ── */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-1.5 border-b border-white/[0.07] bg-md-surface-con/20">
        <span className="text-md-on-surface-var text-xs shrink-0 mr-0.5 w-16">Profile</span>
        <button
          onClick={() => setSweetSpotFilter(f => !f)}
          title="Show only tickers in sweet_spot_active=true AND late_warning=false. Sorted by profile_score DESC, then turbo_score DESC."
          className={`px-2.5 py-0.5 rounded text-xs font-semibold shrink-0 transition-colors border ${
            sweetSpotFilter
              ? 'bg-green-900/60 text-green-300 border-green-600 ring-1 ring-green-500'
              : 'bg-md-surface-high text-md-on-surface-var border-md-outline-var hover:text-white'
          }`}>
          ⭐ Sweet Spot
        </button>
        <button
          onClick={() => setBuildingFilter(f => !f)}
          title="Show only tickers with profile_category = BUILDING. Sorted by profile_score DESC."
          className={`px-2.5 py-0.5 rounded text-xs font-semibold shrink-0 transition-colors border ${
            buildingFilter
              ? 'bg-yellow-900/60 text-yellow-300 border-yellow-600 ring-1 ring-yellow-500'
              : 'bg-md-surface-high text-md-on-surface-var border-md-outline-var hover:text-white'
          }`}>
          ↑ Building
        </button>
        <button
          onClick={() => setWatchFilter(f => !f)}
          title="Show only tickers with profile_category = WATCH. Sorted by profile_score DESC."
          className={`px-2.5 py-0.5 rounded text-xs font-semibold shrink-0 transition-colors border ${
            watchFilter
              ? 'bg-gray-700 text-md-on-surface border-gray-500 ring-1 ring-gray-400'
              : 'bg-md-surface-high text-md-on-surface-var border-md-outline-var hover:text-white'
          }`}>
          👁 Watch
        </button>
        {(sweetSpotFilter || buildingFilter || watchFilter) && (
          <span className="text-xs text-md-on-surface-var">
            {results.length} ticker{results.length !== 1 ? 's' : ''} · sorted by Pf Score
          </span>
        )}
        {(sweetSpotFilter || buildingFilter || watchFilter) && (
          <button onClick={() => { setSweetSpotFilter(false); setBuildingFilter(false); setWatchFilter(false) }}
            className="ml-1 px-2 py-0.5 rounded text-xs shrink-0 bg-red-900/40 text-red-400 hover:bg-red-900/60">
            ✕ clear
          </button>
        )}
        <span className="ml-auto text-md-on-surface-var/70 text-xs">
          Profile score is additive context only — does not replace canonical score
          {(universe === 'nasdaq' || universe === 'russell2k' || universe === 'all_us') &&
            <span className="ml-1 text-amber-600"> · NASDAQ profile is experimental</span>}
          {universe === 'split' &&
            <span className="ml-1 text-sky-700"> · SPLIT lifecycle: D-7→D+90 window</span>}
        </span>
      </div>

      {/* Source-status badges (informational; do not control row visibility) */}
      {(Object.keys(sources).length > 0 || warnings.length > 0) && (
        <div className="px-4 py-1.5 border-b border-white/[0.07] bg-md-surface-con/40 flex flex-wrap items-center gap-2 text-[11px]">
          {['turbo', 'tz_wlnbb', 'tz_intelligence', 'pullback', 'rare_reversal'].map(k => {
            const s = sources[k]
            if (!s) return null
            const cls = s.ok
              ? 'bg-emerald-900/50 text-emerald-200 border-emerald-700/60'
              : 'bg-red-900/30 text-red-300 border-red-700/40'
            return (
              <span key={k} className={`px-1.5 py-0.5 rounded border ${cls}`}>
                {k}: {s.ok ? `ok (${s.count})` : 'unavailable'}
              </span>
            )
          })}
          {warnings.length > 0 && (
            <span className="text-amber-300 ml-1" title={warnings.join('\n')}>
              ⚠ {warnings.length} warning{warnings.length > 1 ? 's' : ''}
            </span>
          )}
        </div>
      )}

      {/* Progress / error */}
      {(scanning || enriching) && (
        <div className="px-4 py-1.5 border-b border-white/[0.07] bg-fuchsia-950/30 text-fuchsia-300">
          <div className="animate-pulse">
            🧬 ULTRA — {UNIVERSES.find(u => u.key === universe)?.label ?? universe} ({localTf.toUpperCase()})
            {' · '}{enriching ? 'Stage 2: enriching subset' : 'Stage 1: Turbo'}
            {phase ? ` · phase: ${phase}` : ''}
          </div>
          {Object.keys(phases).length > 0 && (() => {
            // Group pills by pipeline phase so the user can see the
            // dependency-aware execution at a glance.
            const PHASE_GROUPS = [
              { label: 'Phase 1 (parallel)', keys: ['turbo', 'stock_stat'] },
              { label: 'Phase 2 (parallel)', keys: ['tz_wlnbb', 'tz_intelligence', 'pullback', 'rare_reversal'] },
              { label: 'Phase 3',            keys: ['merge'] },
            ]
            const stateCls = (s) =>
                s === 'ok'      ? 'text-emerald-400'
              : s === 'running' ? 'text-fuchsia-300'
              : s === 'error'   ? 'text-red-400'
              : s === 'skipped' ? 'text-amber-300'
              : 'text-md-on-surface-var'
            return (
              <div className="flex flex-col gap-1 mt-1 text-[10px]">
                {PHASE_GROUPS.map(g => (
                  <div key={g.label} className="flex flex-wrap items-center gap-1">
                    <span className="text-md-on-surface-var/70 mr-1">{g.label}:</span>
                    {g.keys.map(p => {
                      const ph = phases[p]
                      if (!ph) return null
                      return (
                        <span key={p} className={`px-1.5 py-0.5 border border-md-outline-var rounded ${stateCls(ph.state)}`}
                              title={ph.message || ph.state}>
                          {p}: {ph.state}
                        </span>
                      )
                    })}
                  </div>
                ))}
              </div>
            )
          })()}
        </div>
      )}
      {error && (
        <div className="px-4 py-1.5 text-md-error border-b border-white/[0.07] flex items-center gap-3">
          {error === '__stuck__'
            ? <span>Another ULTRA scan is in progress — wait for it to finish, then try again</span>
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
        isLoading={(scanning || enriching) && results.length === 0}
        effectiveScoreCol={effectiveScoreCol}
        universe={universe}
        variant="ultra"
        allPicked={results.length > 0 && results.every(r => pickedTickers.has(r.ticker))}
        onPickAll={checked => {
          if (checked) setPickedTickers(new Set(results.map(r => r.ticker)))
          else setPickedTickers(new Set())
        }}
        handleRowEnter={handleRowEnter}
        handleRowLeave={handleRowLeave}
      />
    </div>
  )
}
