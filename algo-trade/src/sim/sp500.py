# file: src/sim/sp500.py
"""
S&P 500 constituent universe for replay-mode mover ranking.

Tickers use Yahoo Finance conventions (class shares use '-', e.g. BRK-B, BF-B).
This is reference data only — no logic. Update when constituents change.

Source: Curated from S&P 500 constituents as of mid-2025 (Wikipedia/SPDR holdings).
All 503 listed securities represented (some companies have multiple share classes).
"""

SP500_SYMBOLS: list[str] = [
    # A
    "A", "AAPL", "ABBV", "ABNB", "ABT", "ACGL", "ACN", "ADBE", "ADI", "ADM",
    "ADP", "ADSK", "AEE", "AEP", "AES", "AFL", "AIG", "AIZ", "AJG", "AKAM",
    "ALB", "ALGN", "ALL", "ALLE", "AMAT", "AMCR", "AMD", "AME", "AMGN", "AMP",
    "AMT", "AMZN", "ANET", "ANSS", "AON", "AOS", "APA", "APD", "APH", "APTV",
    "ARE", "ATO", "AVB", "AVGO", "AVY", "AWK", "AXON", "AXP", "AZO",
    # B
    "BA", "BAC", "BALL", "BAX", "BBY", "BDX", "BEN", "BF-B", "BG", "BIIB",
    "BK", "BKNG", "BKR", "BLK", "BMY", "BR", "BRK-B", "BRO", "BSX", "BX",
    # C
    "C", "CAG", "CAH", "CARR", "CAT", "CB", "CBOE", "CBRE", "CCI", "CCL",
    "CDNS", "CDW", "CE", "CEG", "CF", "CFG", "CHD", "CHRW", "CHTR", "CI",
    "CINF", "CL", "CLX", "CMCSA", "CME", "CMG", "CMI", "CMS", "CNC", "CNP",
    "COF", "COP", "COST", "CPAY", "CPB", "CPRT", "CPT", "CRL", "CRM", "CSCO",
    "CSGP", "CSX", "CTAS", "CTLT", "CTRA", "CTSH", "CTVA", "CVS", "CVX",
    # D
    "DAL", "DAY", "DD", "DE", "DECK", "DEI", "DFS", "DG", "DGX", "DHI",
    "DHR", "DIS", "DLR", "DLTR", "DOC", "DOV", "DOW", "DPZ", "DRI", "DTE",
    "DUK", "DVA", "DVN",
    # E
    "EA", "EBAY", "ECL", "ED", "EFX", "EG", "EIX", "EL", "ELV", "EMN",
    "EMR", "ENPH", "EOG", "EPAM", "EQIX", "EQR", "EQT", "ES", "ESS", "ETN",
    "ETR", "EVRG", "EW", "EXC", "EXPD", "EXPE", "EXR",
    # F
    "F", "FANG", "FAST", "FCX", "FDS", "FDX", "FE", "FFIV", "FI", "FICO",
    "FIS", "FITB", "FLT", "FMC", "FOX", "FOXA", "FRT", "FSLR", "FTNT", "FTV",
    # G
    "GD", "GDDY", "GE", "GEHC", "GEN", "GEV", "GILD", "GIS", "GL", "GLW",
    "GM", "GNRC", "GOOGL", "GOOG", "GPC", "GPN", "GRMN", "GS", "GWW",
    # H
    "HAL", "HAS", "HBAN", "HCA", "HD", "HES", "HIG", "HII", "HLT", "HOLX",
    "HON", "HPE", "HPQ", "HRL", "HSIC", "HST", "HSY", "HUBB", "HUM", "HWM",
    # I
    "IBM", "ICE", "IDXX", "IEX", "IFF", "INCY", "INTC", "INTU", "INVH",
    "IP", "IPG", "IQV", "IR", "IRM", "ISRG", "IT", "ITW", "IVZ",
    # J
    "JBHT", "JBL", "JCI", "JKHY", "JNJ", "JNPR", "JPM",
    # K
    "K", "KDP", "KEY", "KEYS", "KHC", "KIM", "KKR", "KLAC", "KMB", "KMI",
    "KMX", "KO", "KR",
    # L
    "L", "LDOS", "LEN", "LH", "LHX", "LIN", "LKQ", "LLY", "LMT", "LNT",
    "LOW", "LRCX", "LULU", "LUV", "LVS", "LW", "LYB", "LYV",
    # M
    "MA", "MAA", "MAR", "MAS", "MCD", "MCHP", "MCK", "MCO", "MDLZ", "MDT",
    "MET", "META", "MGM", "MHK", "MKC", "MKTX", "MLM", "MMC", "MMM", "MNST",
    "MO", "MOH", "MOS", "MPC", "MPWR", "MRK", "MRNA", "MRO", "MS", "MSCI",
    "MSFT", "MSI", "MTB", "MTCH", "MTD",
    # N
    "NCLH", "NDAQ", "NEE", "NEM", "NFLX", "NI", "NKE", "NOC", "NOW", "NRG",
    "NSC", "NTAP", "NTRS", "NUE", "NVDA", "NVR", "NWS", "NWSA",
    # O
    "O", "ODFL", "OKE", "OMC", "ON", "ORCL", "ORLY", "OXY",
    # P
    "PANW", "PARA", "PAYC", "PAYX", "PCAR", "PCG", "PEAK", "PEG", "PEP",
    "PFE", "PFG", "PG", "PGR", "PH", "PHM", "PKG", "PLD", "PM", "PNC",
    "PNR", "PNW", "PODD", "POOL", "PPG", "PPL", "PRU", "PSA", "PSX", "PTC",
    "PWR", "PXD",
    # Q
    "QCOM", "QRVO",
    # R
    "RCL", "REG", "REGN", "RF", "RJF", "RL", "RMD", "ROK", "ROL", "ROP",
    "ROST", "RSG", "RTX",
    # S
    "SBAC", "SBUX", "SEDG", "SHW", "SJM", "SLB", "SMCI", "SNA", "SNPS",
    "SO", "SPG", "SPGI", "SRE", "STE", "STLD", "STT", "STX", "STZ", "SWK",
    "SWKS", "SYF", "SYK", "SYY",
    # T
    "T", "TAP", "TDG", "TDY", "TECH", "TEL", "TER", "TFC", "TFX", "TGT",
    "TJX", "TMO", "TMUS", "TPR", "TRGP", "TRMB", "TROW", "TRV", "TSCO",
    "TSLA", "TSN", "TT", "TTWO", "TXN", "TXT", "TYL",
    # U
    "UAL", "UDR", "UHS", "ULTA", "UNH", "UNP", "UPS", "URI", "USB",
    # V
    "V", "VFC", "VICI", "VLO", "VMC", "VRSK", "VRSN", "VRTX", "VTR", "VTRS",
    "VZ",
    # W
    "WAB", "WAT", "WBA", "WBD", "WDAY", "WDC", "WEC", "WELL", "WFC", "WHR",
    "WM", "WMB", "WMT", "WRB", "WRK", "WST", "WTW", "WY",
    # X
    "XEL", "XOM", "XRAY", "XYL",
    # Y
    "YUM",
    # Z
    "ZBH", "ZBRA", "ZTS",
]
