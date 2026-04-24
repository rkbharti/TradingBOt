# strategy/chart_objects.py

"""
This module translates SMC analysis results into chart object representations
for downstream visualization (e.g., overlays, highlight zones, order blocks, etc).
"""

def build_chart_objects(smc_state, zones, ltf_pois, current_price):
    """
    Converts SMC analysis state and zone/POI data into standardized chart objects.
    Returns a dict structured as:
    {
      "structure_lines": [],
      "fvg_zones": [],
      "order_blocks": [],
      "entry_zones": [],
      "sl_tp_boxes": []
    }
    """

    chart_objects = {
        "structure_lines": [],
        "fvg_zones": [],
        "order_blocks": [],
        "entry_zones": [],
        "sl_tp_boxes": []
    }
    #day 9
    # STRUCTURE LINES (e.g., IDM highs/lows, trendlines)
    try:
        idm_high = smc_state.get("idm_high")
        idm_low = smc_state.get("idm_low")
        if idm_high is not None and idm_low is not None:
            chart_objects["structure_lines"].append({
                "type": "range",
                "top": idm_high,
                "bottom": idm_low
            })
        # Add other structure-based lines as needed (eg. MSB)
        msb = smc_state.get("structure_break")
        if msb:
            chart_objects["structure_lines"].append({
                "type": "msb",
                "price": msb
            })
    except Exception:
        pass

    # FVG ZONES (Fair Value Gaps)
    try:
        fvg_list = smc_state.get("fvgs", []) or []
        for fvg in fvg_list:
            chart_objects["fvg_zones"].append({
                "top": fvg.get("top"),
                "bottom": fvg.get("bottom"),
                "direction": fvg.get("direction", None)
            })
    except Exception:
        pass

    # ORDER BLOCKS
    try:
        ob_list = smc_state.get("obs", []) or []
        for ob in ob_list:
            chart_objects["order_blocks"].append({
                "top": ob.get("top"),
                "bottom": ob.get("bottom"),
                "direction": ob.get("direction", None),
                "strength": ob.get("strength", None)
            })
    except Exception:
        pass

    # ENTRY ZONES (eg. current strike/entry zone or recommended zones)
    try:
        if zones:
            for zn_key, zdata in (zones.items() if isinstance(zones, dict) else []):
                if not isinstance(zdata, dict):
                    continue
                entry_zone = {
                    "label": zn_key,
                    "top": zdata.get("top"),
                    "bottom": zdata.get("bottom"),
                }
                chart_objects["entry_zones"].append(entry_zone)
    except Exception:
        pass

    # SL/TP BOXES (Stop Loss / Take Profit suggestion overlays)
    try:
        # If ltf_pois has signals for extreme POI, build a box around it
        extreme_poi = ltf_pois.get("extreme_poi") if isinstance(ltf_pois, dict) else None
        if extreme_poi:
            chart_objects["sl_tp_boxes"].append({
                "type": "extreme_poi",
                "top": extreme_poi.get("top"),
                "bottom": extreme_poi.get("bottom")
            })
        # SL/TP suggestions from smc_state
        sl = smc_state.get("suggested_sl")
        tp = smc_state.get("suggested_tp")
        if sl and tp:
            chart_objects["sl_tp_boxes"].append({
                "type": "suggested_sl_tp",
                "sl": sl,
                "tp": tp,
                "entry": current_price
            })
    except Exception:
        pass

    return chart_objects