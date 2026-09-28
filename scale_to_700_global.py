#!/usr/bin/env python3
"""
Scale Aeon Nimbus to 700 curated global companies.

Geographic distribution:
- Asia: 200 (China 50, Japan 50, Hong Kong 30, Southeast Asia 70)
- Europe: 150 (UK 40, Germany 30, France 25, Nordics 20, Other 35)
- USA: 200 (Mega caps 50, Tech 50, Finance 30, Healthcare 30, Consumer/Industrial 40)
- Africa + Emerging: 117 (existing)

Total: ~667 companies

Requires: FMP_API_KEY in .env ($14/month)
Token cost: ~500 tokens/company × 550 new = ~275k tokens ($0.75 at Sonnet rates)
Runtime: ~2 hours for bulk ingestion + enrichment
"""

import os
import time
from pathlib import Path
import requests
from aeon_nimbus import db as D

API_KEY = os.getenv("FMP_API_KEY")
if not API_KEY:
    print("⚠️  Set FMP_API_KEY in .env first:")
    print("   Get key from https://financialmodelingprep.com/developer/docs")
    print("   Cost: $14/month for Professional plan")
    exit(1)

BASE_URL = "https://financialmodelingprep.com/api/v3"

# Curated company list by region
COMPANIES = {
    "China": [
        "BABA", "TCEHY", "BIDU", "PDD", "JD", "NTES", "BEKE", "LI", "NIO", "XPEV",
        "BYD", "002594.SZ", "600519.SS", "000858.SZ", "002415.SZ",  # BYD, Kweichow Moutai, etc.
        "BILI", "TME", "YUMC", "MNSO", "ATHM", "VNET", "DADA", "MOGU", "VIPS", "WB",
        "700.HK", "9988.HK", "3690.HK", "1810.HK", "2015.HK",  # Tencent, Alibaba HK listings
        "TAL", "EDU", "GOTU", "GSX", "COE", "IQ", "HUYA", "DOYU", "MOMO", "YY",
    ],

    "Japan": [
        "TM", "SONY", "9984.T", "6758.T", "7203.T", "6861.T", "6902.T", "8035.T",  # Toyota, Sony, SoftBank, etc.
        "MUFG", "SMFG", "8306.T", "8316.T", "8411.T",  # Mitsubishi UFJ, Sumitomo Mitsui
        "9983.T", "4063.T", "9432.T", "4502.T", "4503.T",  # Fast Retailing, NTT, Takeda
        "6954.T", "6971.T", "6981.T", "7974.T", "8267.T",  # Fanuc, Kyocera, Murata, Nintendo, Aeon
        "HMC", "NSANY", "SNE", "FUJHY", "MZDAY", "HTHIY", "SNEJF", "DNPLY",
    ],

    "Hong Kong": [
        "0005.HK", "0001.HK", "0011.HK", "0002.HK", "0003.HK",  # HSBC, CKH, Hang Seng
        "1299.HK", "1398.HK", "3988.HK", "0939.HK", "2318.HK",  # AIA, ICBC, CCB, Ping An
        "0016.HK", "0012.HK", "0027.HK", "1113.HK", "0688.HK",  # Sun Hung Kai, Henderson
        "0388.HK", "0669.HK", "0992.HK", "1044.HK", "0823.HK",  # HKEX, Techtronic
        "0386.HK", "0017.HK", "0066.HK", "0101.HK", "0144.HK",  # China Mobile, NWD, MTR
        "1177.HK", "0941.HK", "2382.HK", "6862.HK", "9618.HK",  # Sino Biopharmaceutical
    ],

    "Southeast Asia": [
        "GRAB", "SE", "BBCA.JK", "TLKM.JK", "ASII.JK", "BBRI.JK", "BMRI.JK",  # Indonesia
        "SM.PS", "JFC.PS", "MBT.PS", "BDO.PS", "SMPH.PS", "AC.PS", "MEG.PS",  # Philippines
        "CPALL.BK", "PTT.BK", "AOT.BK", "ADVANC.BK", "KBANK.BK", "SCB.BK",  # Thailand
        "DBS.SI", "OCBC.SI", "UOB.SI", "Z74.SI", "C52.SI", "BN4.SI",  # Singapore
        "MAYBANK.KL", "CIMB.KL", "TENAGA.KL", "PETGAS.KL", "PBBANK.KL",  # Malaysia
        "VNM.HN", "VHM.HN", "VIC.HN", "HPG.HN", "GAS.HN",  # Vietnam
        "GOTO.JK", "BUKA.JK", "TPIA.JK", "UNTR.JK", "INDF.JK", "GGRM.JK",
        "ALI.PS", "GLO.PS", "TEL.PS", "BLOOM.PS", "CNVRG.PS",
        "TRUE.BK", "INTUCH.BK", "BBL.BK", "TOP.BK", "PTTEP.BK",
    ],

    "UK": [
        "HSBA.L", "SHEL.L", "AZN.L", "ULVR.L", "DGE.L", "BP.L", "GSK.L", "RIO.L",
        "BATS.L", "LSEG.L", "REL.L", "NG.L", "PRU.L", "BARC.L", "LLOY.L", "VOD.L",
        "CRH.L", "TSCO.L", "IMB.L", "STAN.L", "RKT.L", "ANTO.L", "GLEN.L", "BHP.L",
        "AAL.L", "EXPN.L", "RTO.L", "SDR.L", "LGEN.L", "BNZL.L", "AUTO.L", "SGE.L",
        "SBRY.L", "MKS.L", "SMDS.L", "PSON.L", "CRDA.L", "BA.L", "IHG.L", "ENT.L",
    ],

    "Germany": [
        "VOW3.DE", "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "MBG.DE", "BMW.DE", "BAS.DE",
        "MUV2.DE", "DBK.DE", "DAI.DE", "ADS.DE", "LIN.DE", "EOAN.DE", "HEN3.DE", "DB1.DE",
        "FRE.DE", "IFX.DE", "BEI.DE", "VOW.DE", "CON.DE", "FME.DE", "HEI.DE", "RWE.DE",
        "MRK.DE", "PAH3.DE", "ZAL.DE", "SHL.DE", "PUM.DE", "CBK.DE",
    ],

    "France": [
        "MC.PA", "OR.PA", "SAN.PA", "TTE.PA", "AIR.PA", "SU.PA", "BNP.PA", "CA.PA",
        "AI.PA", "RI.PA", "SAF.PA", "CS.PA", "DSY.PA", "BN.PA", "EN.PA", "EL.PA",
        "DG.PA", "CAP.PA", "VIV.PA", "ORA.PA", "KER.PA", "RMS.PA", "SGO.PA", "STM.PA",
        "VIE.PA",
    ],

    "Nordics": [
        "NVO", "ASML", "SPOT", "ERIC.ST", "VOLV-B.ST", "SEB-A.ST", "ABB.ST", "SAND.ST",
        "NESTE.HE", "NOKIA.HE", "SAMPO.HE", "FORTUM.HE", "ORSTED.CO", "NOVO-B.CO",
        "MAERSK-B.CO", "DSV.CO", "COLO-B.CO", "DANSKE.CO", "EQNR.OL", "DNB.OL",
    ],

    "Other Europe": [
        "NESN.SW", "ROG.SW", "NOVN.SW", "UHR.SW", "ABBN.SW", "ZURN.SW", "SLHN.SW",  # Switzerland
        "ASML.AS", "PHIA.AS", "HEIA.AS", "INGA.AS", "ADYEN.AS",  # Netherlands
        "RACE.MI", "UCG.MI", "ISP.MI", "ENI.MI", "ENEL.MI", "G.MI",  # Italy
        "ITX.MC", "SAN.MC", "BBVA.MC", "IBE.MC", "TEF.MC", "REP.MC",  # Spain
        "EDP.LS", "GALP.LS", "BCP.LS",  # Portugal
        "GLPG.BR", "ABI.BR", "KBC.BR",  # Belgium
        "MT.AS", "REN.LS",  # Luxembourg, others
    ],

    "USA_Mega": [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK.B", "V", "UNH",
        "JNJ", "WMT", "JPM", "XOM", "LLY", "PG", "MA", "HD", "CVX", "ABBV",
        "MRK", "COST", "AVGO", "PEP", "KO", "ADBE", "CRM", "TMO", "MCD", "CSCO",
        "ACN", "NFLX", "ABT", "NKE", "DHR", "VZ", "TXN", "ORCL", "DIS", "NEE",
        "PM", "CMCSA", "WFC", "BMY", "UNP", "RTX", "AMD", "QCOM", "T", "INTC",
    ],

    "USA_Tech": [
        "NOW", "CRM", "SNOW", "DDOG", "MDB", "NET", "CRWD", "ZS", "OKTA", "TEAM",
        "WDAY", "VEEV", "PANW", "FTNT", "ZM", "DOCU", "TWLO", "SHOP", "SQ", "PYPL",
        "UBER", "LYFT", "ABNB", "DASH", "COIN", "RBLX", "U", "PATH", "BILL", "S",
        "PLTR", "AFRM", "SOFI", "HOOD", "RIVN", "LCID", "PTON", "PINS", "SNAP", "ROKU",
        "TTD", "MELI", "ETSY", "EBAY", "BKNG", "EXPE", "TRVG", "CPNG", "DKNG", "PENN",
    ],

    "USA_Finance": [
        "BAC", "C", "GS", "MS", "SCHW", "AXP", "BLK", "SPGI", "CB", "MMC",
        "ICE", "CME", "MCO", "AON", "TFC", "USB", "PNC", "COF", "BK", "STT",
        "AIG", "MET", "PRU", "ALL", "TRV", "AFL", "HIG", "CINF", "PFG", "L",
    ],

    "USA_Healthcare": [
        "ISRG", "DHR", "SYK", "BSX", "MDT", "EW", "ZBH", "BAX", "BDX", "ALGN",
        "IDXX", "DXCM", "RMD", "PODD", "HOLX", "GMED", "TFX", "COO", "WST", "STE",
        "CVS", "CI", "HUM", "ELV", "CNC", "ANTM", "MOH", "THC", "HCA", "UHS",
    ],

    "USA_Consumer_Industrial": [
        "SBUX", "CMG", "YUM", "DPZ", "QSR", "MCD", "DNKN", "WEN", "TXRH", "DRI",
        "BA", "LMT", "GD", "NOC", "RTX", "HON", "CAT", "DE", "EMR", "ETN",
        "GE", "MMM", "ITW", "ROK", "PH", "AME", "DOV", "FTV", "IR", "XYL",
        "FDX", "UPS", "DAL", "UAL", "AAL", "LUV", "JBLU", "ALK", "SAVE", "HA",
    ],
}

def fetch_company_profile(symbol):
    """Fetch company profile from FMP API."""
    url = f"{BASE_URL}/profile/{symbol}?apikey={API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 0:
                return data[0]
    except Exception as e:
        print(f"  ⚠️  Error fetching {symbol}: {e}")
    return None

def fetch_financial_statements(symbol):
    """Fetch income statement, balance sheet, cash flow from FMP."""
    statements = {}
    endpoints = {
        "income": f"{BASE_URL}/income-statement/{symbol}?limit=5&apikey={API_KEY}",
        "balance": f"{BASE_URL}/balance-sheet-statement/{symbol}?limit=5&apikey={API_KEY}",
        "cashflow": f"{BASE_URL}/cash-flow-statement/{symbol}?limit=5&apikey={API_KEY}",
    }

    for stmt_type, url in endpoints.items():
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                statements[stmt_type] = response.json()
        except Exception as e:
            print(f"    ⚠️  {stmt_type} failed: {e}")

    return statements

def ingest_company(symbol, region):
    """Ingest one company: profile + financials."""
    print(f"  Fetching {symbol}...", end=" ")

    profile = fetch_company_profile(symbol)
    if not profile:
        print("❌ Profile not found")
        return False

    statements = fetch_financial_statements(symbol)
    if not any(statements.values()):
        print("❌ No financials")
        return False

    # Save to data/extracted/{slug}.json
    slug = profile.get("symbol", symbol).lower().replace(".", "_")
    extracted_path = Path("data/extracted") / f"{slug}.json"
    extracted_path.parent.mkdir(parents=True, exist_ok=True)

    import json
    with open(extracted_path, "w") as f:
        json.dump({
            "profile": profile,
            "financials": statements,
            "source": "FMP_API",
            "region": region,
        }, f, indent=2)

    print(f"✓ Saved to {extracted_path}")
    return True

def main():
    print("=" * 70)
    print("AEON NIMBUS: SCALE TO 700 GLOBAL COMPANIES")
    print("=" * 70)
    print()

    total = sum(len(tickers) for tickers in COMPANIES.values())
    print(f"📊 Target: {total} companies across {len(COMPANIES)} regions")
    print()

    for region, tickers in COMPANIES.items():
        print(f"\n{region} ({len(tickers)} companies):")
        print("-" * 70)

        success = 0
        for ticker in tickers:
            if ingest_company(ticker, region):
                success += 1
            time.sleep(0.3)  # Rate limiting

        print(f"\n  ✅ {success}/{len(tickers)} companies ingested")

    print()
    print("=" * 70)
    print("✅ BULK INGESTION COMPLETE")
    print()
    print("Next steps:")
    print("  1. Run auto-enrichment: curl -X POST http://localhost:5174/api/bulk/enrich")
    print("  2. Rebuild platform: python3 build_platform.py")
    print("  3. Check quality: curl http://localhost:5174/api/bulk/quality-report")
    print("=" * 70)

if __name__ == "__main__":
    main()
