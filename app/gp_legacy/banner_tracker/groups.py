EXPECTED_DEALERS = {
    "R1": ["CA1", "CA2", "CA3", "CA4", "CA5", "CA6", "CA7", "CA8", "CA9", "PTM6"],
    "R2": ["BTI6", "CPH2", "KDL1", "KHT3", "KRV5", "KSV5", "KTB1", "PKB4", "PMR5", "SVA7", "SVR4", "TAK2"],
    "R3": ["CUK7", "KKG3", "KPS7", "KPT2", "KSM1", "KSP3", "KTR4", "PNP6", "SRA5", "TPG2"],
    "R4": ["BTB1", "BVL6", "KCG4", "KTL6", "MRS5", "PLN4", "PUS5", "VVG1"],
    "R5": ["BMC2", "CHK6", "KRG7", "KTH2", "KTM8", "PPR8", "PPT3", "PUK7", "SRP1"],
    "R6": ["BTY8", "KCM1", "MMT3", "STT4", "TKM2"],
    "R7": ["BKE7", "KRT1", "KSA5", "MDK2", "RTK4", "SNL6", "STR3"],
    "R8": ["AVG4", "CKS1", "PVH3", "SMR5"],
}

EXPECTED_REGION_DEALERS = {
    (region, dealer)
    for region, dealers in EXPECTED_DEALERS.items()
    for dealer in dealers
}

# Authoritative lookup used by imports, database repair, and Excel exports.
# Historical spreadsheets are not trusted for Region because many rows use
# XLOOKUP formulas whose cached results are blank or zero.
DEALER_TO_REGION = {
    dealer: region
    for region, dealers in EXPECTED_DEALERS.items()
    for dealer in dealers
}


def normalized_dealer(value: object) -> str:
    """Return the normalized dealer code used by the 65-group deployment."""
    return str(value or "").strip().upper()


def region_for_dealer(value: object) -> str:
    """Return R1..R8 for a valid dealer, otherwise an empty string."""
    return DEALER_TO_REGION.get(normalized_dealer(value), "")
