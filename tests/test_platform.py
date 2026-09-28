from build_platform import assemble


def test_assemble_full_universe_with_deep_companies():
    p = assemble()
    assert p["counts"]["total"] >= 30
    assert p["counts"]["deep"] >= 20  # every extracted company + the flagship

    # the flagship is now on the source-linked extracted path like its peers:
    # 5 years, an indicative DCF, and real KPI values (not just names)
    scom = next(c for c in p["companies"] if c["ticker"] == "SCOM")
    assert scom["deep"] is not None
    assert len(scom["deep"]["years"]) == 5
    assert scom["deep"]["dcf"]["base"]["value"] is not None
    assert scom["deep"]["rating"]["stance"] in {"Buy", "Accumulate", "Hold", "Reduce", "Under review", "NR"}
    assert len(scom["deep"]["sector_kpis"]) >= 1  # KPI values populated for the flagship too

    # an extracted company now has real 5-year financials + KPI values
    mtn = next(c for c in p["companies"] if c["ticker"] == "MTN")
    assert mtn["deep"] is not None
    assert len(mtn["deep"]["years"]) == 5
    assert all(v is not None for v in mtn["deep"]["revenue"])
    assert mtn["deep"]["keystats"]["ev_ebitda"] is not None
    assert len(mtn["deep"]["sector_kpis"]) >= 1  # KPI values, not just names
