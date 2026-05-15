"""
scanner.py — scan ticker universe for T/Z + WLNBB signals with combined scoring.
Saves results to SQLite. Scheduled at 09:30, 12:00, 15:30 EST.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from signal_engine import compute_signals, SIG_NAMES
from wlnbb_engine import compute_wlnbb, score_last_bar, l_signal_label
from combo_engine import compute_combo, last_n_active, active_signal_labels
from sq_engine   import compute_sq
from wick_engine import compute_wick
from cisd_engine import compute_cisd
from vabs_engine import compute_vabs

# ── Combo extra boolean columns ───────────────────────────────────────────────
_COMBO_L_COLS = [
    # WLNBB L signals
    "l34", "l43", "l64", "l22",
    "cci_ready", "blue", "fri34", "pre_pump", "bo_up", "bx_up",
    # WLNBB FUCHSIA RH/RL (from 260315)
    "fuchsia_rh", "fuchsia_rl",
    # 260312 VSA signals
    "sq", "ns", "nd", "sig3_up", "sig3_dn",
    # 3112_2C wick reversal
    "wick_bull", "wick_bear",
    # 250115 CISD sequences
    "cisd_seq", "cisd_ppm", "cisd_mpm", "cisd_pmm",
]

log = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "/tmp/scanner.db")

# ── Scan progress state (shared across threads) ───────────────────────────────
_scan_state: dict = {
    "running": False,
    "done": 0,
    "total": 0,
    "found": 0,
    "interval": "",
}

# ── Ticker universe ───────────────────────────────────────────────────────────

_FALLBACK = [
    # Mega-cap / S&P 500 core
    "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","BRK-B","JPM","JNJ",
    "V","PG","UNH","HD","MA","ABBV","MRK","LLY","CVX","PEP","KO","AVGO",
    "BAC","COST","MCD","WMT","TMO","CSCO","ACN","CRM","ABT","LIN","DHR",
    "NEE","TXN","VZ","ADBE","PM","WFC","BMY","CMCSA","RTX","NFLX","INTC",
    "AMGN","HON","QCOM","AMD","UPS","CAT","GS","LOW","SBUX","ELV","DE",
    "SPGI","BLK","AXP","LMT","SYK","GILD","MS","CVS","MDLZ","PLD","ISRG",
    "ZTS","ADI","BKNG","TJX","C","REGN","MO","SO","DUK","USB","PNC",
    "VRTX","CL","ITW","CI","EOG","SLB","EMR","AON","APD","ICE","MCO",
    "FIS","NSC","TGT","FISV","EW","GD","DXCM","FDX","HUM","WM","FCX",
    "OXY","MPC","PSA","MRNA","KLAC","LRCX","SNPS","CDNS","MCHP","AMAT",
    # Tech / Growth
    "MU","PANW","CRWD","SNOW","PLTR","SQ","SHOP","UBER","LYFT","ABNB",
    "COIN","RBLX","HOOD","SOFI","AFRM","UPST","RIVN","LCID","NIO",
    "BABA","JD","PDD","BIDU","DDOG","NET","ZS","OKTA","TWLO","MDB",
    "ESTC","HUBS","GTLB","U","DOCN","CFLT","IOT","TOST","ASAN","BILL",
    "DUOL","APPN","AI","PATH","BRZE","SEMR","SPT","MNDY","WIX",
    # Auto / Transport
    "F","GM","TM","HMC","RACE","XPEV","LI",
    "DAL","UAL","AAL","LUV","ALK","JBLU",
    # Leisure / Gaming
    "CCL","RCL","NCLH","MGM","WYNN","LVS","CZR","PENN","DKNG",
    "DIS","PARA","WBD","ROKU","SPOT","TTWO","EA","ATVI","NTES","BILI",
    # Energy
    "XOM","COP","HAL","BKR","MRO","DVN","FANG","PXD","VLO","PSX","HES",
    "APA","NOV","RIG","WTI","CTRA","MTDR",
    # Metals / Mining stocks (no ETFs — GLD/SLV removed)
    "NEM","AEM","WPM","FNV","RGLD","GOLD","KGC","AGI",
    # S&P 500 mid-tier
    "MMM","AOS","ABT","AIG","ARE","AFL","ALB","ALGN","ALLE","LNT",
    "AEE","AEP","AXP","AMT","AWK","AMP","ADM","APTV","ACGL","ADI",
    "APH","AIZ","T","ATO","AZO","AVB","AVY","AXON","BKR","BALL",
    "BAX","BDX","BBY","BIO","TECH","BIIB","BXP","BSX","BA","BWA",
    "BR","BF-B","BLDR","BRO","CHRW","CDNS","CZR","CPT","CPB","COF",
    "CAH","KMX","CCL","CARR","CTLT","CAT","CBOE","CBRE","CDW","CE",
    "COR","CNC","CNP","CF","CHTR","CME","SCHW","LNG","CVX","CMG",
    "CB","CHD","CI","CINF","CTAS","CSCO","C","CFG","CLX","CMI",
    "CMS","KO","CTSH","CL","CMCSA","CAG","COP","ED","STZ","CEG",
    "COO","CPT","CPRT","GLW","CTRA","CSGP","COST","CTRA","CCI","CSX",
    "CMI","CVS","DHI","DHR","DRI","DVA","DAY","DE","DAL","XRAY",
    "DVN","DXCM","FANG","DLR","DFS","DG","DLTR","D","DPZ","ODFL",
    "DOV","DOW","DHI","DTE","DUK","DRE","DD","EMN","ETN","EBAY",
    "ECL","EIX","EW","EA","ELV","LLY","EMR","ENPH","ETR","EOG",
    "EPAM","EQT","EFX","EQIX","EQR","ESS","EL","EG","ES","RE",
    "EXC","EXPE","EXPD","EXR","XOM","FFIV","FDS","FICO","FAST",
    "FRT","FDX","FIS","FITB","FSLR","FE","FLT","FMC","F","FTNT",
    "FTV","FOXA","FOX","BEN","FCX","GRMN","IT","GEHC","GEN","GNRC",
    "GIS","GL","GPC","GWW","HAL","HIG","HAS","HCA","PEAK","HSIC",
    "HES","HPE","HLT","HOLX","HD","HON","HRL","HST","HWM","HPQ",
    "HUBB","HUM","HBAN","HII","IBM","IEX","IDXX","ITW","ILMN","INCY",
    "IR","PODD","INTC","ICE","IFF","IP","IPG","INTU","ISRG","IVZ",
    "INVH","IQV","IRM","JBHT","JBL","JKHY","J","JNJ","JCI","JPM",
    "JNPR","K","KVUE","KDP","KEY","KEYS","KMB","KIM","KMI","KHC",
    "KR","LHX","LH","LRCX","LW","LVS","LDOS","LEN","LNC","LIN",
    "LYV","LKQ","LMT","L","LOW","LULU","LYB","MTB","MRO","MPC",
    "MKTX","MAR","MMC","MLM","MAS","MA","MTCH","MKC","MCD","MCK",
    "MDT","MRK","META","MET","MTD","MGM","MCHP","MU","MSFT","MAA",
    "MRNA","MHK","MOH","TAP","MDLZ","MPWR","MNST","MCO","MS","MSI",
    "MSCI","NDAQ","NTAP","NFLX","NWL","NEM","NWSA","NWS","NEE","NKE",
    "NI","NDSN","NSC","NTRS","NOC","NCLH","NRG","NUE","NVDA","NVR",
    "NXPI","ORLY","OXY","ODFL","OMC","ON","OKE","ORCL","OTIS","PCAR",
    "PKG","PANW","PH","PAYX","PAYC","PYPL","PNR","PEP","PFE","PCG",
    "PM","PSX","PNW","PXD","PNC","POOL","PPG","PPL","PFG","PG",
    "PGR","PLD","PRU","PEG","PTC","PSA","PHM","QRVO","PWR","QCOM",
    "DGX","RL","RJF","RTX","O","REG","REGN","RF","RSG","RMD",
    "RVTY","ROK","ROL","ROP","ROST","RCL","SPGI","CRM","SBAC","SLB",
    "STX","SEE","SRE","NOW","SHW","SPG","SWKS","SJM","SNA","SOLV",
    "SO","LUV","SWK","SBUX","STT","STLD","STE","SYK","SMCI","SYF",
    "SNPS","SYY","TMUS","TROW","TTWO","TPR","TGT","TEL","TDY","TFX",
    "TER","TSLA","TXN","TXT","TMO","TJX","TSCO","TT","TDG","TRV",
    "TRMB","TFC","TYL","USB","UDR","ULTA","UNP","UAL","UPS","URI",
    "UNH","UHS","VLO","VTR","VRSN","VRSK","VZ","VRTX","VTRS","VICI",
    "V","VMC","WRB","GWW","WAB","WBA","WMT","WBD","WM","WAT",
    "WEC","WFC","WELL","WST","WDC","WRK","WY","WHR","WMB","WTW",
    "WYNN","XEL","XYL","YUM","ZBRA","ZBH","ZION","ZTS",
]


MAX_TICKERS = 700  # expanded ticker universe

# ── NASDAQ extended (beyond S&P 500 overlap) ──────────────────────────────────
_NASDAQ_EXTRA = [
    # NASDAQ-100 core (non-S&P overlap)
    "ADSK","ANSS","ASML","ATVI","AZN","BIIB","BMRN","CDNS","CPRT","CSGP",
    "CTSH","DLTR","DXCM","EA","EXC","FAST","GEHC","GILD","HON","IDXX",
    "ILMN","INTC","INTU","ISRG","KDP","KHC","KLAC","LRCX","MCHP","MDLZ",
    "MELI","MNST","MRNA","MRVL","MTCH","MU","NTES","NXPI","ODFL","ON",
    "ORLY","PAYX","PCAR","PYPL","REGN","ROST","SBUX","SGEN","SIRI","SNPS",
    "TEAM","TMUS","TXN","VRSK","VRTX","WBA","WDAY","XEL","ZM","ZS",
    # Mid-cap NASDAQ growth $3-50 range
    "AEHR","AGIO","ALKS","ALNY","AMRN","APLS","ARCT","ARDX","ARWR","AXSM",
    "BBIO","BEAM","BHVN","BLUE","BNGO","BOLT","BYND","CARA","CCXI","CERE",
    "CLDX","CLFD","CMPS","CPRX","CRSP","CTXS","CVAC","CYTK","DCPH","DNLI",
    "EDIT","EIDX","ENSG","ENTA","EPZM","ESPR","ETNB","EXAS","EXEL","FATE",
    "FOLD","FROG","FSLR","FULC","GBTC","GCBC","GEVO","GLNG","GLSI","GNMK",
    "HALO","HIMS","HOOK","HRZN","ICVX","IMVT","INMD","INVA","IOVA","IRTC",
    "ITCI","JANX","KALV","KROS","KURA","LAUR","LGND","LMNX","LNTH","LPSN",
    "LQDA","LSCC","LUNA","LYRA","MARA","MASI","MBIO","MDXG","MERC","MGNX",
    "MGTX","MIME","MIRM","MIST","MLCO","MNKD","MODN","MPWR","MRUS","MTEM",
    "NBIX","NBTX","NCNO","NKTR","NNOX","NRIX","NSTG","NTLA","NTRA","NVAX",
    "NVST","OCGN","OFIX","OMCL","OMGA","ONCT","OPEN","OPRA","ORIC","ORMP",
    "ORPH","OTRK","PACIFIC","PAYO","PBYI","PCVX","PETQ","PLRX","PNTM","POET",
    "PRAX","PRGO","PRLB","PRTA","PRVB","PTCT","PTGX","PXMD","PYXS","RGEN",
    "RNLX","RPID","RPTX","RVMD","RXMD","SAVA","SBGI","SCPH","SEER","SESN",
    "SGMO","SILK","SILO","SMAR","SMMT","SRPT","SSKN","STAA","STOK","SURF",
    "SVRA","SWAV","SYRS","TELA","TENB","TGTX","TLRY","TMDX","TNXP","TRDA",
    "TRIL","TRVI","TTEC","TYME","TZOO","VCNX","VERA","VIEW","VNDA","VNET",
    "VORB","VREX","VSAT","VXRT","WIX","WORX","WTRH","XBIT","XCUR","XENE",
    "XFOR","XNCR","XOMA","XTLB","YMAB","YTEN","ZNTL","ZSAN","ZYXI",
    # More NASDAQ mid-cap tech & growth
    "ACMR","AIOT","AKAM","ALRM","AMKR","APOG","APPN","ARQT","ASYS","AVAV",
    "AVLR","AXNX","BAND","BFAM","BIGC","BLKB","BPMC","CALX","CDMO","CELH",
    "CENTA","CERE","CGEM","CHKP","CLBT","CLOV","CMCO","CNXN","COHR","COOP",
    "COUP","CRUS","CSII","CTLP","DAVA","DBTX","DFIN","DGII","DIOD","DOCN",
    "DOMO","DSGX","DXPE","EBIX","EDAC","EFSC","EGAN","EMKR","EOLS","EPAY",
    "EXPO","EXTR","FARO","FBIZ","FCNC-A","FEAM","FIVE","FIVN","FLGT","FLIR",
    "FORM","FORR","FOSL","FROG","FSLY","FWRG","GKOS","GLBE","GOCO","GOLF",
    "GOTU","GRFS","GRWG","GSIT","GTHX","GTLS","GUYS","HAIN","HALO","HCAT",
    "HIBB","HLIO","HMST","HNST","HRTX","HSAI","HTBK","HTHI","HUBG","IDCC",
    "IIIN","IIIV","IMXI","INFA","INFU","INPX","INSM","INSP","IOSP","IPGP",
    "IRBT","IRDM","IRTC","JCOM","JNVR","JOBY","KADX","KALA","KBAL","KFRC",
    "KIDS","KNSA","KNSL","KNTK","KOPN","KRYS","KTOS","KWEB","LAKE","LBAI",
    "LCII","LGIH","LKFN","LLNW","LMAT","LMNR","LNDC","LOPE","LPSN","LQDT",
    "LSEA","LSTR","LVOX","LWLG","LYTS","MANT","MBIN","MBUU","MCBC","MCRI",
    "MGEE","MGNI","MGRC","MGTX","MLAB","MLKN","MMSI","MNRO","MOFG","MORN",
]

# ── Russell 2000 representative fallback (~500 small-caps) ────────────────────
_RUSSELL2K_FALLBACK = [
    "ACLS","ACMR","ACRX","ACST","ACTG","ACVA","ACXP","ADAP","ADEA","ADMA",
    "ADMP","ADMS","ADTN","ADVM","AEAC","AEHR","AEIS","AEON","AEYE","AEZS",
    "AFAR","AFBI","AFCG","AFRI","AGBA","AGEN","AGFS","AGIL","AGIO","AGMH",
    "AGYS","AHCO","AHPA","AHPI","AIOT","AIXI","AJRD","AKAM","AKBA","AKTS",
    "ALBT","ALCO","ALEC","ALGT","ALIM","ALKT","ALLK","ALNY","ALOT","ALRM",
    "ALRS","ALSA","ALTO","ALXO","AMAG","AMBC","AMCI","AMEH","AMGN","AMIC",
    "AMKR","AMNB","AMOT","AMPH","AMRN","AMRS","AMSC","AMSF","AMTB","AMWD",
    "ANAB","ANAT","ANCN","ANDE","ANET","ANGI","ANIP","ANKA","ANSS","ANTE",
    "ANTX","ANZU","AORT","APDN","APEI","APGE","APLD","APLE","APLS","APLT",
    "APOG","APOP","APRE","APPS","APRE","APTO","APTX","APVO","APWC","APYX",
    "AQMS","AQST","ARAV","ARCT","ARDX","AREC","ARGX","ARHS","ARID","ARIS",
    "ARIZ","ARKO","ARLS","ARMK","ARMP","ARPA","ARQT","ARQQ","ARRY","ARSD",
    "ARTE","ARTL","ARTNA","ARTW","ARWR","ARYA","ASAI","ASAX","ASGN","ASLE",
    "ASLN","ASMB","ASML","ASNS","ASPI","ASPS","ASPU","ASRT","ASST","ASTE",
    "ASTL","ASTR","ASTS","ASUR","ASYS","ATAI","ATEC","ATEN","ATEX","ATGL",
    "ATHA","ATHE","ATIP","ATLO","ATMP","ATNI","ATNM","ATRC","ATRI","ATRS",
    "ATSG","ATSP","ATUS","ATVI","ATXI","ATXS","AUBN","AUDC","AUGX","AURC",
    "AUUD","AVAH","AVAV","AVCO","AVGO","AVIR","AVNS","AVNT","AVPT","AVRO",
    "AVRX","AVTE","AVTR","AVXL","AVYA","AXDX","AXGN","AXIL","AXNX","AXSM",
    "AXTI","AZEK","AZPN","AZTA","AZUL","BAND","BANF","BANL","BANR","BANX",
    "BARK","BASH","BASI","BATL","BAYA","BBCP","BBGI","BBIO","BBSI","BCAB",
    "BCAL","BCBP","BCEL","BCML","BCOV","BCOW","BCPC","BCRX","BCSA","BCSF",
    "BCYC","BDGE","BDRX","BDSX","BDTX","BEEM","BENF","BFAM","BFIN","BFLY",
    "BFRI","BGCP","BGFV","BGRY","BHVN","BIMI","BIOL","BIOX","BIVI","BJRI",
    "BKKT","BKNG","BKTI","BKYI","BLBD","BLBX","BLCO","BLDE","BLFS","BLFY",
    "BLKB","BLMS","BLND","BLNK","BLPH","BLRX","BLSA","BLTE","BLUR","BLVD",
    "BMBL","BMNM","BMRA","BMRC","BMRN","BMTC","BNGO","BNIX","BNRG","BNSO",
    "BNTC","BNTX","BOCH","BOKF","BOLT","BPMC","BPTH","BRDG","BRDS","BREZ",
    "BRID","BRKL","BRKR","BRLI","BRMK","BROG","BRTX","BRVI","BSAC","BSBK",
    "BSET","BSGM","BSQR","BSRR","BSVN","BSYN","BTAI","BTBT","BTCS","BTMD",
    "BTRN","BTRS","BTTX","BTNB","BTWN","BTXN","BUKS","BURL","BYFC","BYND",
    "BYSI","BZFD","CALB","CALC","CALT","CALX","CAMP","CANO","CAPS","CARA",
    "CARE","CARG","CARV","CASH","CASI","CASS","CATC","CATS","CBFV","CBKM",
    "CBNK","CBSH","CBST","CCBG","CCCC","CCEL","CCEP","CCIX","CCLD","CCNE",
    "CCNX","CCOJ","CCOL","CCRD","CCRN","CCSI","CCXI","CDMO","CDNA","CDNS",
    "CDRE","CDRO","CDTX","CDXC","CDXS","CELC","CELH","CEMI","CENT","CERN",
    "CERS","CERT","CEVA","CFFE","CFFI","CFFN","CFLT","CFNB","CFRX","CFVI",
    "CGBD","CGEM","CGEN","CGNT","CGRN","CGRO","CHCO","CHDN","CHEF","CHEK",
    "CHGG","CHKP","CHMG","CHPT","CHRS","CHUY","CIFR","CINF","CINT","CION",
    "CISO","CIVB","CIVG","CIZN","CJET","CKPT","CLBK","CLBT","CLCC","CLDT",
    "CLDX","CLFD","CLGN","CLIR","CLMT","CLNE","CLNN","CLOA","CLPT","CLRB",
    "CLRO","CLSD","CLSK","CLST","CLVT","CLWT","CMBT","CMCO","CMCT","CMDX",
]

# ── Universe configs ──────────────────────────────────────────────────────────
UNIVERSE_CONFIGS: dict = {
    "sp500":     {"label": "S&P 500",    "min_price": 0.0, "max_price": 1e9, "fetch": "sp500"},
    "nasdaq":    {"label": "NASDAQ",     "min_price": 0.0, "max_price": 1e9, "fetch": "nasdaq_massive"},
    "russell2k": {"label": "Russell 2K", "min_price": 0.0, "max_price": 1e9, "fetch": "russell2k_massive"},
    "all_us":    {"label": "All US",     "min_price": 0.7, "max_price": 1e9, "fetch": "all_us"},
    "split":     {"label": "Splits",     "min_price": 0.0, "max_price": 1e9, "fetch": "split"},
}


def get_nasdaq_tickers(limit: int = 700) -> list[str]:
    """NASDAQ-100 (Wikipedia) + extended NASDAQ mid-cap list."""
    tickers: list[str] = []
    # Try NASDAQ-100 from Wikipedia
    for url in [
        "https://en.wikipedia.org/wiki/Nasdaq-100",
    ]:
        try:
            tables = pd.read_html(url)
            for tbl in tables:
                cols = [str(c).lower() for c in tbl.columns]
                if any("ticker" in c or "symbol" in c for c in cols):
                    col = next(c for c in tbl.columns if "ticker" in str(c).lower() or "symbol" in str(c).lower())
                    tickers.extend([str(t).replace(".", "-") for t in tbl[col].dropna().tolist()])
                    break
            if len(tickers) >= 50:
                break
        except Exception:
            pass
    # Also pull NASDAQ-100 from GitHub CSV fallback
    if len(tickers) < 50:
        try:
            df = pd.read_csv(
                "https://raw.githubusercontent.com/datasets/nasdaq-listings/master/data/nasdaq-listed.csv"
            )
            col = next((c for c in df.columns if "symbol" in c.lower() or "ticker" in c.lower()), None)
            if col:
                tickers.extend(df[col].dropna().tolist())
        except Exception:
            pass
    # Combine with extra list and _FALLBACK NASDAQ stocks
    tickers.extend(_NASDAQ_EXTRA)
    # Also include tech-heavy stocks from _FALLBACK
    tickers.extend([t for t in _FALLBACK if t not in tickers])
    return list(dict.fromkeys([str(t).strip() for t in tickers if t]))[:limit]


def get_russell2000_tickers(limit: int = 700) -> list[str]:
    """Fetch IWM holdings (Russell 2000) from iShares, fallback to static list."""
    tickers: list[str] = []
    # iShares IWM holdings CSV
    iwm_urls = [
        "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund",
        "https://raw.githubusercontent.com/AhmedNadar/russell-2000/main/russell2000.csv",
    ]
    for url in iwm_urls:
        try:
            df = pd.read_csv(url, skiprows=9 if "ishares" in url else 0, nrows=2100)
            col = next(
                (c for c in df.columns if str(c).lower() in ("ticker", "symbol", "name")),
                None
            )
            if col is None:
                col = df.columns[0]
            raw = df[col].dropna().tolist()
            tickers = [str(t).strip().replace(".", "-") for t in raw
                       if isinstance(t, str) and t.strip() and t.strip() != "-"
                       and not any(c.isspace() and len(t) > 6 for c in t)]
            if len(tickers) >= 100:
                break
        except Exception:
            pass
    if len(tickers) < 100:
        tickers = list(_RUSSELL2K_FALLBACK)
    return list(dict.fromkeys(tickers))[:limit]


def get_tickers(limit: int = 700) -> list[str]:
    """S&P 500 tickers from Wikipedia + _FALLBACK (common stocks only, no ETFs)."""
    from data_polygon import _is_valid_stock_ticker
    sp500: list[str] = []
    for url in [
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
    ]:
        try:
            if "wikipedia" in url:
                t = pd.read_html(url, attrs={"id": "constituents"})[0]["Symbol"].tolist()
            else:
                t = pd.read_csv(url)["Symbol"].tolist()
            sp500.extend([x.replace(".", "-") for x in t])
            if len(sp500) >= 500:
                break
        except Exception:
            pass
    combined = list(dict.fromkeys(sp500 + _FALLBACK))
    combined = [t for t in combined if _is_valid_stock_ticker(t)]
    return combined[:limit]


_TICKER_CACHE_TTL = 23 * 3600  # 23 hours


def _cached_massive_tickers(cache_key: str, fetcher, fallback, limit: int) -> list[str]:
    """
    Fetch tickers from Massive API with file-based cache (TTL=23h).
    On timeout/error uses cache if available, else static fallback.
    """
    import tempfile, pickle, os as _os
    cache_path = _os.path.join(tempfile.gettempdir(), f"sachoki_tickers_{cache_key}.pkl")

    def _load_cache():
        try:
            if _os.path.exists(cache_path):
                age = time.time() - _os.path.getmtime(cache_path)
                if age < _TICKER_CACHE_TTL:
                    with open(cache_path, "rb") as f:
                        data = pickle.load(f)
                    log.info("Ticker cache hit (%s): %d tickers (age %.0fh)", cache_key, len(data), age/3600)
                    return data
        except Exception:
            pass
        return None

    def _save_cache(tickers):
        try:
            with open(cache_path, "wb") as f:
                pickle.dump(tickers, f)
        except Exception:
            pass

    from data_polygon import polygon_available
    if polygon_available():
        try:
            tickers = fetcher(limit=limit)
            _save_cache(tickers)
            return tickers
        except Exception as exc:
            log.warning("Massive fetch failed (%s) — trying cache", exc)
            cached = _load_cache()
            if cached:
                return cached[:limit]
            log.warning("No cache available — using static fallback")
    else:
        cached = _load_cache()
        if cached:
            return cached[:limit]

    return fallback()


def get_universe_tickers(universe: str = "sp500", limit: int = 10_000) -> list[str]:
    """Return ticker list for the given universe key."""
    cfg = UNIVERSE_CONFIGS.get(universe, UNIVERSE_CONFIGS["sp500"])
    fetch = cfg["fetch"]

    if fetch == "nasdaq_massive":
        from data_polygon import get_exchange_tickers
        return _cached_massive_tickers(
            "nasdaq",
            lambda limit: get_exchange_tickers("XNAS", limit=min(limit, 5_000)),
            lambda: get_nasdaq_tickers(700),
            limit,
        )

    elif fetch == "russell2k_massive":
        from data_polygon import get_all_us_tickers
        return _cached_massive_tickers(
            "russell2k",
            lambda limit: get_all_us_tickers(limit=min(limit, 5_000)),
            lambda: get_russell2000_tickers(700),
            limit,
        )

    elif fetch == "all_us":
        from data_polygon import get_all_us_tickers, polygon_available
        if not polygon_available():
            raise RuntimeError("MASSIVE_API_KEY not set — All US universe requires Massive API key")
        return _cached_massive_tickers(
            "all_us",
            lambda limit: get_all_us_tickers(limit=limit),
            lambda: list(dict.fromkeys(get_tickers(700) + get_nasdaq_tickers(700))),
            limit,
        )

    elif fetch == "split":
        from split_universe import split_service
        return split_service.get_split_tickers()[:limit]

    else:  # sp500
        return get_tickers(min(limit, 700))


# ── DB schema ─────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    """Open a DB connection with WAL mode and a generous lock timeout."""
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def _init_db() -> None:
    con = _db()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS scan_results (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id      INTEGER DEFAULT 0,
            ticker       TEXT NOT NULL,
            sig_id       INTEGER,
            sig_name     TEXT,
            pattern_3bar TEXT,
            l_signal     TEXT DEFAULT '',
            bull_score   INTEGER DEFAULT 0,
            bear_score   INTEGER DEFAULT 0,
            last_price   REAL DEFAULT 0,
            volume       INTEGER DEFAULT 0,
            change_pct   REAL DEFAULT 0,
            interval     TEXT,
            scanned_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_scan ON scan_results(interval, scanned_at DESC);

        CREATE TABLE IF NOT EXISTS scan_runs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            interval     TEXT,
            started_at   TEXT,
            completed_at TEXT,
            result_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS watchlist (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker    TEXT UNIQUE NOT NULL,
            added_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS combo_scan_results (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id    INTEGER DEFAULT 0,
            ticker     TEXT NOT NULL,
            signals    TEXT DEFAULT '',
            buy_2809   INTEGER DEFAULT 0,
            um_2809    INTEGER DEFAULT 0,
            svs_2809   INTEGER DEFAULT 0,
            conso_2809 INTEGER DEFAULT 0,
            cons_atr   INTEGER DEFAULT 0,
            bias_up    INTEGER DEFAULT 0,
            bias_down  INTEGER DEFAULT 0,
            atr_brk    INTEGER DEFAULT 0,
            bb_brk     INTEGER DEFAULT 0,
            hilo_buy   INTEGER DEFAULT 0,
            hilo_sell  INTEGER DEFAULT 0,
            rtv        INTEGER DEFAULT 0,
            preup3     INTEGER DEFAULT 0,
            preup2     INTEGER DEFAULT 0,
            preup50    INTEGER DEFAULT 0,
            preup89    INTEGER DEFAULT 0,
            sig3g      INTEGER DEFAULT 0,
            rocket     INTEGER DEFAULT 0,
            tz_sig     TEXT DEFAULT '',
            l34        INTEGER DEFAULT 0,
            l43        INTEGER DEFAULT 0,
            l64        INTEGER DEFAULT 0,
            l22        INTEGER DEFAULT 0,
            cci_ready  INTEGER DEFAULT 0,
            blue       INTEGER DEFAULT 0,
            fri34      INTEGER DEFAULT 0,
            pre_pump   INTEGER DEFAULT 0,
            bo_up      INTEGER DEFAULT 0,
            bx_up      INTEGER DEFAULT 0,
            fuchsia_rh INTEGER DEFAULT 0,
            fuchsia_rl INTEGER DEFAULT 0,
            sq         INTEGER DEFAULT 0,
            ns         INTEGER DEFAULT 0,
            nd         INTEGER DEFAULT 0,
            sig3_up    INTEGER DEFAULT 0,
            sig3_dn    INTEGER DEFAULT 0,
            wick_bull  INTEGER DEFAULT 0,
            wick_bear  INTEGER DEFAULT 0,
            cisd_seq   INTEGER DEFAULT 0,
            cisd_ppm   INTEGER DEFAULT 0,
            cisd_mpm   INTEGER DEFAULT 0,
            cisd_pmm   INTEGER DEFAULT 0,
            last_price REAL DEFAULT 0,
            volume     INTEGER DEFAULT 0,
            change_pct REAL DEFAULT 0,
            scanned_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_combo_scan
            ON combo_scan_results(scanned_at DESC);

        CREATE TABLE IF NOT EXISTS combo_scan_runs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at   TEXT,
            completed_at TEXT,
            result_count INTEGER DEFAULT 0,
            n_bars       INTEGER DEFAULT 3
        );

        CREATE TABLE IF NOT EXISTS pump_combos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            combo         TEXT NOT NULL,
            count         INTEGER,
            avg_gain_pct  REAL,
            max_gain_pct  REAL,
            win_rate      REAL,
            threshold     REAL DEFAULT 2.0,
            window        INTEGER DEFAULT 20,
            combo_len     INTEGER DEFAULT 3,
            created_at    TEXT
        );
    """)
    # Migrate: add columns if they don't exist (for older DBs)
    existing = {
        row[1]
        for row in con.execute("PRAGMA table_info(scan_results)").fetchall()
    }
    for col, defn in [
        ("scan_id",     "INTEGER DEFAULT 0"),
        ("l_signal",    "TEXT DEFAULT ''"),
        ("bull_score",  "INTEGER DEFAULT 0"),
        ("bear_score",  "INTEGER DEFAULT 0"),
        ("last_price",  "REAL DEFAULT 0"),
        ("volume",      "INTEGER DEFAULT 0"),
        ("change_pct",  "REAL DEFAULT 0"),
        # VABS signals
        ("abs_sig",     "INTEGER DEFAULT 0"),
        ("climb_sig",   "INTEGER DEFAULT 0"),
        ("load_sig",    "INTEGER DEFAULT 0"),
        ("best_sig",    "INTEGER DEFAULT 0"),
        ("strong_sig",  "INTEGER DEFAULT 0"),
        ("vbo_up",      "INTEGER DEFAULT 0"),
        ("vbo_dn",      "INTEGER DEFAULT 0"),
        ("ns",          "INTEGER DEFAULT 0"),
        ("nd",          "INTEGER DEFAULT 0"),
        ("sc",          "INTEGER DEFAULT 0"),
        ("bc",          "INTEGER DEFAULT 0"),
        ("sq",          "INTEGER DEFAULT 0"),
        # Combo signals
        ("buy_2809",    "INTEGER DEFAULT 0"),
        ("rocket",      "INTEGER DEFAULT 0"),
        ("sig3g",       "INTEGER DEFAULT 0"),
        ("rtv",         "INTEGER DEFAULT 0"),
        ("hilo_buy",    "INTEGER DEFAULT 0"),
        ("atr_brk",     "INTEGER DEFAULT 0"),
        ("bb_brk",      "INTEGER DEFAULT 0"),
        # WLNBB display
        ("vol_bucket",  "TEXT DEFAULT ''"),
        ("candle_dir",  "TEXT DEFAULT ''"),
        ("l_combo",     "TEXT DEFAULT ''"),
        # Wick
        ("wick_bull",   "INTEGER DEFAULT 0"),
    ]:
        if col not in existing:
            con.execute(f"ALTER TABLE scan_results ADD COLUMN {col} {defn}")

    # Migrate combo_scan_results: add all extra columns if missing
    existing_combo = {
        row[1]
        for row in con.execute("PRAGMA table_info(combo_scan_results)").fetchall()
    }
    for col, defn in [
        ("tz_sig",    "TEXT DEFAULT ''"),
        ("l34",       "INTEGER DEFAULT 0"),
        ("l43",       "INTEGER DEFAULT 0"),
        ("l64",       "INTEGER DEFAULT 0"),
        ("l22",       "INTEGER DEFAULT 0"),
        ("cci_ready", "INTEGER DEFAULT 0"),
        ("blue",      "INTEGER DEFAULT 0"),
        ("fri34",     "INTEGER DEFAULT 0"),
        ("pre_pump",  "INTEGER DEFAULT 0"),
        ("bo_up",     "INTEGER DEFAULT 0"),
        ("bx_up",     "INTEGER DEFAULT 0"),
        ("fuchsia_rh","INTEGER DEFAULT 0"),
        ("fuchsia_rl","INTEGER DEFAULT 0"),
        ("sq",        "INTEGER DEFAULT 0"),
        ("ns",        "INTEGER DEFAULT 0"),
        ("nd",        "INTEGER DEFAULT 0"),
        ("sig3_up",   "INTEGER DEFAULT 0"),
        ("sig3_dn",   "INTEGER DEFAULT 0"),
        ("wick_bull", "INTEGER DEFAULT 0"),
        ("wick_bear", "INTEGER DEFAULT 0"),
        ("cisd_seq",  "INTEGER DEFAULT 0"),
        ("cisd_ppm",  "INTEGER DEFAULT 0"),
        ("cisd_mpm",  "INTEGER DEFAULT 0"),
        ("cisd_pmm",  "INTEGER DEFAULT 0"),
    ]:
        if col not in existing_combo:
            con.execute(f"ALTER TABLE combo_scan_results ADD COLUMN {col} {defn}")

    con.commit()
    con.close()


# ── Extended scoring ──────────────────────────────────────────────────────────

def _ext_score(last_sig: int, wlnbb_last, vabs_last, combo_last, wick_last) -> tuple[int, int]:
    """
    Compute (bull_score, bear_score) from all engine signals on the last bar.
    Scores are capped at 10.

    Bullish weights:
      T4/T6 engulf: +2   |  other T: +1
      FRI34: +2  |  L34/L43: +1 each  |  BLUE/CCI_READY/BO_UP: +1
      BEST: +4  |  STRONG: +3  |  VBO_UP: +3
      (ABS+CLIMB+LOAD if no BEST/STRONG/VBO: up to +3)
      NS/SQ recent (embedded in BEST): +1 standalone
      ROCKET: +3  |  BUY_2809: +2  |  SIG3G: +2
      RTV/PREUP/ATR_BRK/BB_BRK/HILO_BUY: +1 each
      WICK_BULL_CONFIRM: +1

    Bearish weights:
      Z4/Z6 engulf: +2  |  other Z: +1
      L22: +2  |  L64/BO_DN/BX_DN/FUCHSIA_RH: +1
      VBO_DN: +2  |  BC: +1  |  ND: +1
      HILO_SELL/BIAS_DOWN: +1
    """
    def g(row, key, default=False):
        if row is None:
            return default
        try:
            v = row.get(key, default) if hasattr(row, 'get') else getattr(row, key, default)
            return bool(v)
        except Exception:
            return default

    bull = 0
    bear = 0

    # ── T/Z signal ────────────────────────────────────────────────────────
    if last_sig in (6, 8):           bull += 2   # T4, T6 (full engulf)
    elif 1 <= last_sig <= 11:        bull += 1   # other T
    if last_sig in (17, 19):         bear += 2   # Z4, Z6 (full engulf)
    elif 12 <= last_sig <= 25:       bear += 1   # other Z

    # ── WLNBB signals ────────────────────────────────────────────────────
    if g(wlnbb_last, "FRI34"):       bull += 2
    elif g(wlnbb_last, "L34"):       bull += 1
    if g(wlnbb_last, "L43"):         bull += 1
    if g(wlnbb_last, "BLUE"):        bull += 1
    if g(wlnbb_last, "CCI_READY"):   bull += 1
    if g(wlnbb_last, "BO_UP") or g(wlnbb_last, "BX_UP"):   bull += 1
    if g(wlnbb_last, "L22"):         bear += 2
    if g(wlnbb_last, "L64"):         bear += 1
    if g(wlnbb_last, "BO_DN") or g(wlnbb_last, "BX_DN"):   bear += 1
    if g(wlnbb_last, "FUCHSIA_RH"):  bear += 1

    # ── VABS signals (exclusive priority for combined vol signals) ────────
    if g(vabs_last, "best_sig"):
        bull += 4
    elif g(vabs_last, "strong_sig"):
        bull += 3
    elif g(vabs_last, "vbo_up"):
        bull += 3
    else:
        sub = (int(g(vabs_last, "abs_sig")) +
               int(g(vabs_last, "climb_sig")) +
               int(g(vabs_last, "load_sig")))
        bull += min(sub, 2)
    if g(vabs_last, "vbo_dn"):       bear += 2
    if g(vabs_last, "bc"):           bear += 1
    if g(vabs_last, "nd"):           bear += 1

    # ── Combo signals ─────────────────────────────────────────────────────
    if g(combo_last, "rocket"):      bull += 3
    elif g(combo_last, "buy_2809"):  bull += 2
    if g(combo_last, "sig3g"):       bull += 2
    if g(combo_last, "rtv") or g(combo_last, "preup3") or g(combo_last, "preup2"):
        bull += 1
    if g(combo_last, "atr_brk") or g(combo_last, "bb_brk"):
        bull += 1
    if g(combo_last, "hilo_buy"):    bull += 1
    if g(combo_last, "hilo_sell"):   bear += 1
    if g(combo_last, "bias_down"):   bear += 1

    # ── Wick signals ──────────────────────────────────────────────────────
    if g(wick_last, "WICK_BULL_CONFIRM"): bull += 1

    return min(bull, 10), min(bear, 10)


# ── Per-ticker processing ─────────────────────────────────────────────────────

def _scan_ticker(ticker: str, interval: str) -> dict | None:
    try:
        raw = yf.Ticker(ticker).history(
            period="90d", interval=interval, auto_adjust=True
        )
        if raw is None or raw.empty or len(raw) < 5:
            return None

        raw.columns = [str(c).lower() for c in raw.columns]
        needed = ["open", "high", "low", "close"]
        df = raw[needed + (["volume"] if "volume" in raw.columns else [])].dropna()
        if len(df) < 5:
            return None

        sigs = compute_signals(df)
        last_sig = int(sigs["sig_id"].iloc[-1])

        # WLNBB
        wlnbb_last = None
        l_sig = ""
        vol_bucket = candle_dir = l_combo = ""
        try:
            wlnbb = compute_wlnbb(df)
            wlnbb_last = dict(wlnbb.iloc[-1])
            l_sig = l_signal_label(wlnbb.iloc[-1])
            last_w = wlnbb.iloc[-1]
            _bkt_map = {0: "W", 1: "L", 2: "N", 3: "B", 4: "VB"}
            vol_bucket = _bkt_map.get(int(last_w.get("vol_bucket", 0)), "")
            candle_dir = str(last_w.get("candle_dir", ""))
            l_combo    = str(last_w.get("l_combo", "NONE"))
        except Exception:
            pass

        # VABS signals
        vabs_last = None
        abs_sig = climb_sig = load_sig = best_sig = strong_sig = False
        vbo_up = vbo_dn = ns = nd = sc = bc = sq = False
        try:
            vabs = compute_vabs(df)
            vabs_last = dict(vabs.iloc[-1])
            abs_sig   = bool(vabs_last.get("abs_sig", False))
            climb_sig = bool(vabs_last.get("climb_sig", False))
            load_sig  = bool(vabs_last.get("load_sig", False))
            best_sig  = bool(vabs_last.get("best_sig", False))
            strong_sig = bool(vabs_last.get("strong_sig", False))
            vbo_up    = bool(vabs_last.get("vbo_up", False))
            vbo_dn    = bool(vabs_last.get("vbo_dn", False))
            ns        = bool(vabs_last.get("ns", False))
            nd        = bool(vabs_last.get("nd", False))
            sc        = bool(vabs_last.get("sc", False))
            bc        = bool(vabs_last.get("bc", False))
            sq        = bool(vabs_last.get("sq", False))
        except Exception:
            pass

        # Combo signals
        combo_last = None
        buy_2809 = rocket = sig3g = rtv = hilo_buy = atr_brk = bb_brk = False
        try:
            combo = compute_combo(df)
            combo_last = dict(combo.iloc[-1])
            buy_2809 = bool(combo_last.get("buy_2809", False))
            rocket   = bool(combo_last.get("rocket", False))
            sig3g    = bool(combo_last.get("sig3g", False))
            rtv      = bool(combo_last.get("rtv", False))
            hilo_buy = bool(combo_last.get("hilo_buy", False))
            atr_brk  = bool(combo_last.get("atr_brk", False))
            bb_brk   = bool(combo_last.get("bb_brk", False))
        except Exception:
            pass

        # Wick signals
        wick_last = None
        wick_bull = False
        try:
            wick = compute_wick(df)
            wick_last = dict(wick.iloc[-1])
            wick_bull = bool(wick_last.get("WICK_BULL_CONFIRM", False))
        except Exception:
            pass

        # Extended scoring
        bull_score, bear_score = _ext_score(last_sig, wlnbb_last, vabs_last, combo_last, wick_last)

        # Skip tickers with no meaningful signal
        if last_sig == 0 and bull_score < 1 and bear_score < 1:
            return None

        last_row = df.iloc[-1]
        prev_row = df.iloc[-2] if len(df) > 1 else last_row
        last_price = float(last_row["close"])
        prev_price = float(prev_row["close"])
        change_pct = round(
            (last_price - prev_price) / prev_price * 100, 2
        ) if prev_price else 0.0
        volume = int(last_row.get("volume", 0)) if "volume" in df.columns else 0

        pat = " → ".join(sigs["sig_name"].tail(3).tolist())

        return {
            "ticker":       ticker,
            "sig_id":       last_sig,
            "sig_name":     SIG_NAMES.get(last_sig, "NONE"),
            "pattern_3bar": pat,
            "l_signal":     l_sig,
            "bull_score":   bull_score,
            "bear_score":   bear_score,
            "last_price":   round(last_price, 2),
            "volume":       volume,
            "change_pct":   change_pct,
            "interval":     interval,
            "vol_bucket":   vol_bucket,
            "candle_dir":   candle_dir,
            "l_combo":      l_combo,
            # VABS
            "abs_sig":      int(abs_sig),
            "climb_sig":    int(climb_sig),
            "load_sig":     int(load_sig),
            "best_sig":     int(best_sig),
            "strong_sig":   int(strong_sig),
            "vbo_up":       int(vbo_up),
            "vbo_dn":       int(vbo_dn),
            "ns":           int(ns),
            "nd":           int(nd),
            "sc":           int(sc),
            "bc":           int(bc),
            "sq":           int(sq),
            # Combo
            "buy_2809":     int(buy_2809),
            "rocket":       int(rocket),
            "sig3g":        int(sig3g),
            "rtv":          int(rtv),
            "hilo_buy":     int(hilo_buy),
            "atr_brk":      int(atr_brk),
            "bb_brk":       int(bb_brk),
            # Wick
            "wick_bull":    int(wick_bull),
        }
    except Exception as exc:
        log.debug("Scanner skip %s: %s", ticker, exc)
        return None


# ── Main scan ─────────────────────────────────────────────────────────────────

def get_scan_progress() -> dict:
    """Return a copy of the current scan progress state."""
    return dict(_scan_state)


def run_scan(interval: str = "1d", workers: int = 8) -> int:
    """
    Scan all tickers. Save results to SQLite incrementally.
    Returns count of results saved.
    """
    _init_db()
    tickers = get_tickers()
    now_iso = datetime.now(timezone.utc).isoformat()

    _scan_state.update({"running": True, "done": 0, "total": len(tickers),
                        "found": 0, "interval": interval})

    con = _db()
    cur = con.execute(
        "INSERT INTO scan_runs (interval, started_at) VALUES (?,?)",
        (interval, now_iso),
    )
    scan_id = cur.lastrowid
    con.commit()
    con.close()

    results = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_scan_ticker, t, interval): t for t in tickers}
        for fut in as_completed(futures):
            _scan_state["done"] += 1
            row = fut.result()
            if row is None:
                continue
            row["scan_id"]   = scan_id
            row["scanned_at"] = now_iso
            results.append(row)
            _scan_state["found"] = len(results)

            # Write incrementally every 20 results
            if len(results) % 20 == 0:
                _flush(results[-20:])

    # Final flush for remainder
    remainder = results[-(len(results) % 20) or len(results):]
    if remainder:
        _flush(remainder)

    # Update scan_run record
    con = _db()
    con.execute(
        "UPDATE scan_runs SET completed_at=?, result_count=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), len(results), scan_id),
    )
    # Remove old results for this interval (keep last 2 scan_ids)
    con.execute("""
        DELETE FROM scan_results
        WHERE interval=? AND scan_id NOT IN (
            SELECT id FROM scan_runs
            WHERE interval=?
            ORDER BY id DESC LIMIT 2
        )
    """, (interval, interval))
    con.commit()
    con.close()

    _scan_state["running"] = False
    log.info("Scan %d complete: %d results", scan_id, len(results))
    return len(results)


def _flush(rows: list[dict]) -> None:
    if not rows:
        return
    con = _db()
    con.executemany(
        "INSERT INTO scan_results "
        "(scan_id,ticker,sig_id,sig_name,pattern_3bar,l_signal,"
        " bull_score,bear_score,last_price,volume,change_pct,interval,scanned_at,"
        " vol_bucket,candle_dir,l_combo,"
        " abs_sig,climb_sig,load_sig,best_sig,strong_sig,vbo_up,vbo_dn,"
        " ns,nd,sc,bc,sq,"
        " buy_2809,rocket,sig3g,rtv,hilo_buy,atr_brk,bb_brk,wick_bull) "
        "VALUES (:scan_id,:ticker,:sig_id,:sig_name,:pattern_3bar,:l_signal,"
        " :bull_score,:bear_score,:last_price,:volume,:change_pct,:interval,:scanned_at,"
        " :vol_bucket,:candle_dir,:l_combo,"
        " :abs_sig,:climb_sig,:load_sig,:best_sig,:strong_sig,:vbo_up,:vbo_dn,"
        " :ns,:nd,:sc,:bc,:sq,"
        " :buy_2809,:rocket,:sig3g,:rtv,:hilo_buy,:atr_brk,:bb_brk,:wick_bull)",
        rows,
    )
    con.commit()
    con.close()


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_results(
    interval: str = "1d",
    limit: int = 50,
    min_bull: int = 0,
    min_bear: int = 0,
    tab: str = "all",
) -> list[dict]:
    """Return latest scan results. tab: all | bull | bear | strong | fire"""
    _init_db()
    con = _db()

    # Filter by last scan_id for this interval
    last_run = con.execute(
        "SELECT MAX(id) FROM scan_runs WHERE interval=?", (interval,)
    ).fetchone()[0]
    if last_run is None:
        con.close()
        return []

    filters = ["scan_id=?"]
    params: list = [last_run]

    if tab == "bull":
        filters.append("bull_score >= 4")
    elif tab == "bear":
        filters.append("bear_score >= 3")
    elif tab == "strong":
        filters.append("bull_score >= 6")
    elif tab == "fire":
        filters.append("bull_score >= 8")

    if min_bull > 0:
        filters.append(f"bull_score >= {int(min_bull)}")
    if min_bear > 0:
        filters.append(f"bear_score >= {int(min_bear)}")

    where = " AND ".join(filters)
    rows = con.execute(
        f"SELECT ticker,sig_id,sig_name,pattern_3bar,l_signal,"
        f"bull_score,bear_score,last_price,volume,change_pct,scanned_at,"
        f"vol_bucket,candle_dir,l_combo,"
        f"abs_sig,climb_sig,load_sig,best_sig,strong_sig,vbo_up,vbo_dn,"
        f"ns,nd,sc,bc,sq,"
        f"buy_2809,rocket,sig3g,rtv,hilo_buy,atr_brk,bb_brk,wick_bull "
        f"FROM scan_results WHERE {where} "
        f"ORDER BY bull_score DESC, sig_id DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    con.close()

    keys = [
        "ticker","sig_id","sig_name","pattern_3bar","l_signal",
        "bull_score","bear_score","last_price","volume","change_pct","scanned_at",
        "vol_bucket","candle_dir","l_combo",
        "abs_sig","climb_sig","load_sig","best_sig","strong_sig","vbo_up","vbo_dn",
        "ns","nd","sc","bc","sq",
        "buy_2809","rocket","sig3g","rtv","hilo_buy","atr_brk","bb_brk","wick_bull",
    ]
    return [dict(zip(keys, r)) for r in rows]


def get_last_scan_time(interval: str = "1d") -> str | None:
    _init_db()
    con = _db()
    row = con.execute(
        "SELECT completed_at FROM scan_runs WHERE interval=? "
        "ORDER BY id DESC LIMIT 1",
        (interval,),
    ).fetchone()
    con.close()
    return row[0] if row else None


# ── Watchlist persistence ──────────────────────────────────────────────────────

def save_watchlist(tickers: list[str]) -> None:
    _init_db()
    now = datetime.now(timezone.utc).isoformat()
    con = _db()
    con.execute("DELETE FROM watchlist")
    con.executemany(
        "INSERT OR REPLACE INTO watchlist (ticker, added_at) VALUES (?, ?)",
        [(t.upper().strip(), now) for t in tickers if t.strip()],
    )
    con.commit()
    con.close()


def load_watchlist() -> list[str]:
    _init_db()
    con = _db()
    rows = con.execute(
        "SELECT ticker FROM watchlist ORDER BY added_at"
    ).fetchall()
    con.close()
    return [r[0] for r in rows]


# ── Settings persistence ──────────────────────────────────────────────────────

def save_settings(settings: dict) -> None:
    _init_db()
    con = _db()
    con.executemany(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        [(k, str(v)) for k, v in settings.items()],
    )
    con.commit()
    con.close()


def load_settings() -> dict:
    _init_db()
    con = _db()
    rows = con.execute("SELECT key, value FROM settings").fetchall()
    con.close()
    return {r[0]: r[1] for r in rows}


# ── Combo scan (260323) ────────────────────────────────────────────────────────

_combo_state: dict = {
    "running": False,
    "done": 0,
    "total": 0,
    "found": 0,
}

_COMBO_BOOL_COLS = [
    "buy_2809", "um_2809", "svs_2809", "conso_2809",
    "cons_atr", "bias_up", "bias_down",
    "atr_brk", "bb_brk",
    "hilo_buy", "hilo_sell", "rtv",
    "preup3", "preup2", "preup50", "preup89",
    "sig3g", "rocket",
]


def get_combo_scan_progress() -> dict:
    return dict(_combo_state)


def _scan_combo_ticker(ticker: str, interval: str, n_bars: int = 3) -> dict | None:
    """Compute 260323 combo signals for the last bar (and last n_bars) of a ticker."""
    try:
        raw = yf.Ticker(ticker).history(
            period="90d", interval=interval, auto_adjust=True
        )
        if raw is None or raw.empty or len(raw) < 20:
            return None

        raw.columns = [str(c).lower() for c in raw.columns]
        needed = ["open", "high", "low", "close"]
        df = raw[needed + (["volume"] if "volume" in raw.columns else [])].dropna()
        if len(df) < 20:
            return None

        combo = compute_combo(df)
        active = last_n_active(combo, n_bars)

        # Skip tickers with no active signals
        if not any(active.values()):
            return None

        last  = df.iloc[-1]
        prev  = df.iloc[-2] if len(df) > 1 else last
        price = float(last["close"])
        prev_p = float(prev["close"])
        chg   = round((price - prev_p) / prev_p * 100, 2) if prev_p else 0.0
        vol   = int(last.get("volume", 0)) if "volume" in df.columns else 0

        # ── T/Z Signal (last bar) ─────────────────────────────────────────
        tz_sig = ""
        try:
            sigs     = compute_signals(df)
            last_sig = sigs.iloc[-1]
            if bool(last_sig["is_bull"]):
                tz_sig = str(last_sig["sig_name"])
        except Exception:
            pass

        # ── WLNBB L + FUCHSIA signals (last bar) ─────────────────────────
        l_flags: dict = {col: 0 for col in _COMBO_L_COLS}
        try:
            wlnbb  = compute_wlnbb(df)
            last_w = wlnbb.iloc[-1]
            l_flags.update({
                "l34":       int(bool(last_w.get("L34",       False))),
                "l43":       int(bool(last_w.get("L43",       False))),
                "l64":       int(bool(last_w.get("L64",       False))),
                "l22":       int(bool(last_w.get("L22",       False))),
                "cci_ready": int(bool(last_w.get("CCI_READY", False))),
                "blue":      int(bool(last_w.get("BLUE",      False))),
                "fri34":     int(bool(last_w.get("FRI34",     False))),
                "pre_pump":  int(bool(last_w.get("PRE_PUMP",  False))),
                "bo_up":     int(bool(last_w.get("BO_UP",     False))),
                "bx_up":     int(bool(last_w.get("BX_UP",     False))),
                "fuchsia_rh":int(bool(last_w.get("FUCHSIA_RH",False))),
                "fuchsia_rl":int(bool(last_w.get("FUCHSIA_RL",False))),
            })
        except Exception:
            pass

        # ── 260312 VSA signals (last bar) ─────────────────────────────────
        try:
            sq_df  = compute_sq(df)
            last_s = sq_df.iloc[-1]
            l_flags.update({
                "sq":      int(bool(last_s.get("SQ",      False))),
                "ns":      int(bool(last_s.get("NS",      False))),
                "nd":      int(bool(last_s.get("ND",      False))),
                "sig3_up": int(bool(last_s.get("SIG3_UP", False))),
                "sig3_dn": int(bool(last_s.get("SIG3_DN", False))),
            })
        except Exception:
            pass

        # ── 3112_2C wick signals (last bar) ──────────────────────────────
        try:
            wick_df = compute_wick(df)
            last_wk = wick_df.iloc[-1]
            l_flags.update({
                "wick_bull": int(bool(last_wk.get("WICK_BULL_CONFIRM", False))),
                "wick_bear": int(bool(last_wk.get("WICK_BEAR_CONFIRM", False))),
            })
        except Exception:
            pass

        # ── 250115 CISD sequences (last bar only) ─────────────────────────
        try:
            cisd_df  = compute_cisd(df)
            last_c   = cisd_df.iloc[-1]
            l_flags.update({
                "cisd_seq": int(bool(last_c["CISD_SEQ"])),
                "cisd_ppm": int(bool(last_c["CISD_PPM"])),
                "cisd_mpm": int(bool(last_c["CISD_MPM"])),
                "cisd_pmm": int(bool(last_c["CISD_PMM"])),
            })
        except Exception:
            pass

        return {
            "ticker":     ticker,
            "signals":    ",".join(active_signal_labels(active)),
            "tz_sig":     tz_sig,
            "last_price": round(price, 2),
            "volume":     vol,
            "change_pct": chg,
            **{col: int(active.get(col, False)) for col in _COMBO_BOOL_COLS},
            **l_flags,
        }
    except Exception as exc:
        log.debug("Combo skip %s: %s", ticker, exc)
        return None


def run_combo_scan(interval: str = "1d", n_bars: int = 3, workers: int = 8) -> int:
    """Scan all tickers for 260323 combo signals. Saves results to SQLite."""
    _init_db()
    tickers  = get_tickers()
    now_iso  = datetime.now(timezone.utc).isoformat()

    _combo_state.update({"running": True, "done": 0,
                         "total": len(tickers), "found": 0})

    con = _db()
    cur = con.execute(
        "INSERT INTO combo_scan_runs (started_at, n_bars) VALUES (?,?)",
        (now_iso, n_bars),
    )
    scan_id = cur.lastrowid
    con.commit()
    con.close()

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_scan_combo_ticker, t, interval, n_bars): t
            for t in tickers
        }
        for fut in as_completed(futures):
            _combo_state["done"] += 1
            row = fut.result()
            if row is None:
                continue
            row["scan_id"]    = scan_id
            row["scanned_at"] = now_iso
            results.append(row)
            _combo_state["found"] = len(results)

            if len(results) % 20 == 0:
                _flush_combo(results[-20:])

    remainder = results[-(len(results) % 20) or len(results):]
    if remainder:
        _flush_combo(remainder)

    con = _db()
    con.execute(
        "UPDATE combo_scan_runs SET completed_at=?, result_count=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), len(results), scan_id),
    )
    # Keep only last 2 scan runs
    con.execute("""
        DELETE FROM combo_scan_results
        WHERE scan_id NOT IN (
            SELECT id FROM combo_scan_runs ORDER BY id DESC LIMIT 2
        )
    """)
    con.commit()
    con.close()

    _combo_state["running"] = False
    log.info("Combo scan %d done: %d results", scan_id, len(results))
    return len(results)


def _flush_combo(rows: list[dict]) -> None:
    if not rows:
        return
    cols      = (["scan_id", "ticker", "signals", "tz_sig", "last_price", "volume",
                  "change_pct", "scanned_at"]
                 + _COMBO_BOOL_COLS + _COMBO_L_COLS)
    placeholders = ", ".join(f":{c}" for c in cols)
    col_names    = ", ".join(cols)
    con = _db()
    con.executemany(
        f"INSERT INTO combo_scan_results ({col_names}) VALUES ({placeholders})",
        rows,
    )
    con.commit()
    con.close()


def get_combo_results(
    signal_filter: str = "all",
    limit: int = 200,
) -> list[dict]:
    """Return latest combo scan results, optionally filtered by signal column."""
    _init_db()
    con = _db()

    last_run = con.execute(
        "SELECT MAX(id) FROM combo_scan_runs"
    ).fetchone()[0]
    if last_run is None:
        con.close()
        return []

    where = "scan_id=?"
    params: list = [last_run]
    if signal_filter != "all" and signal_filter in _COMBO_BOOL_COLS:
        where += f" AND {signal_filter}=1"

    cols = (["ticker", "signals", "tz_sig", "last_price", "volume", "change_pct",
             "scanned_at"]
            + _COMBO_BOOL_COLS + _COMBO_L_COLS)
    col_str = ", ".join(cols)

    rows = con.execute(
        f"SELECT {col_str} FROM combo_scan_results "
        f"WHERE {where} ORDER BY scanned_at DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    con.close()

    return [dict(zip(cols, r)) for r in rows]


def get_last_combo_scan_time() -> str | None:
    _init_db()
    con = _db()
    row = con.execute(
        "SELECT completed_at FROM combo_scan_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    return row[0] if row else None
