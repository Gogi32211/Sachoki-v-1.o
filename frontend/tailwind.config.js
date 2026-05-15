/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      // ── MD3 Color Tokens (via CSS variables) ──────────────────────────────
      colors: {
        'md-primary':              'var(--md-primary)',
        'md-on-primary':           'var(--md-on-primary)',
        'md-primary-container':    'var(--md-primary-container)',
        'md-on-primary-container': 'var(--md-on-primary-container)',
        'md-secondary':            'var(--md-secondary)',
        'md-on-secondary':         'var(--md-on-secondary)',
        'md-secondary-container':  'var(--md-secondary-container)',
        'md-surface':              'var(--md-surface)',
        'md-surface-dim':          'var(--md-surface-dim)',
        'md-surface-low':          'var(--md-surface-container-low)',
        'md-surface-con':          'var(--md-surface-container)',
        'md-surface-high':         'var(--md-surface-container-high)',
        'md-surface-highest':      'var(--md-surface-container-highest)',
        'md-on-surface':           'var(--md-on-surface)',
        'md-on-surface-var':       'var(--md-on-surface-variant)',
        'md-outline':              'var(--md-outline)',
        'md-outline-var':          'var(--md-outline-variant)',
        'md-error':                'var(--md-error)',
        'md-on-error':             'var(--md-on-error)',
        'md-error-container':      'var(--md-error-container)',
        'md-positive':             'var(--md-positive)',
        'md-positive-con':         'var(--md-positive-container)',
        'md-negative':             'var(--md-negative)',
        'md-negative-con':         'var(--md-negative-container)',
        'md-warning':              'var(--md-warning)',
        'md-warning-con':          'var(--md-warning-container)',
      },

      // ── MD3 Typography Scale (all ≥ 14px) ────────────────────────────────
      fontSize: {
        'xs':   ['14px', { lineHeight: '20px' }],
        'sm':   ['14px', { lineHeight: '20px' }],
        'base': ['16px', { lineHeight: '24px' }],
        'lg':   ['18px', { lineHeight: '28px' }],
        'xl':   ['20px', { lineHeight: '28px' }],
        '2xl':  ['24px', { lineHeight: '32px' }],
        '3xl':  ['28px', { lineHeight: '36px' }],
        // MD3 named scale
        'md-label':    ['14px', { lineHeight: '20px', letterSpacing: '0.1px' }],
        'md-body':     ['16px', { lineHeight: '24px', letterSpacing: '0.5px' }],
        'md-title':    ['22px', { lineHeight: '28px' }],
        'md-headline': ['28px', { lineHeight: '36px' }],
        'md-display':  ['36px', { lineHeight: '44px' }],
      },

      // ── MD3 Shape Scale ───────────────────────────────────────────────────
      borderRadius: {
        'md-xs':   '4px',
        'md-sm':   '8px',
        'md-md':   '12px',
        'md-lg':   '16px',
        'md-xl':   '28px',
        'md-full': '9999px',
      },

      // ── MD3 Elevation Shadows (dark mode) ─────────────────────────────────
      boxShadow: {
        'md-1': '0px 1px 2px rgba(0,0,0,.35), 0px 1px 3px 1px rgba(0,0,0,.15)',
        'md-2': '0px 1px 2px rgba(0,0,0,.35), 0px 2px 6px 2px rgba(0,0,0,.15)',
        'md-3': '0px 4px 8px 3px rgba(0,0,0,.15), 0px 1px 3px rgba(0,0,0,.35)',
        'md-4': '0px 6px 10px 4px rgba(0,0,0,.15), 0px 2px 3px rgba(0,0,0,.35)',
      },
    },
  },
  plugins: [],
}
