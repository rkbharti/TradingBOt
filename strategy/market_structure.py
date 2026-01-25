import pandas as pd
import numpy as np
from typing import List, Dict, Optional


class MarketStructureDetector:
    """SMC-oriented Market Structure Engine (Inducement-first).

    Responsibilities:
    - Detect classic 5-bar fractals (swing highs/lows)
    - Identify internal pullbacks and label the first Inducement (IDM) candidate
    - Confirm IDM via wick-based sweep detection within a look-forward window
    - Determine structure confirmation (MSS/CHOCH/BOS) only after IDM is swept

    Notes:
    - Deterministic and timeframe-agnostic. Accepts pandas.DataFrame with columns
      ['time','open','high','low','close'].
    - Does NOT log to stdout; functions return structured dicts and reason codes.
    - Default sweep look-forward window = 12 bars (parameterizable).
    """

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy() if df is not None else pd.DataFrame()
        # Default look-forward window for sweep detection
        self.default_look_forward = 12

    # ---------------------- Helpers / Defaults ----------------------
    def _default_analysis(self) -> Dict:
        return {
            'is_idm_present': False,
            'idm_bar_index': None,
            'idm_price': None,
            'idm_type': None,  # 'bullish' or 'bearish'
            'is_idm_swept': False,
            'idm_sweep_bar_index': None,
            'idm_sweep_price': None,
            'structure_confirmed': False,
            'mss_or_choch': 'NONE',
            'bos_or_sweep_occurred': False,
            'bos_level': None,
            'reason_code': 'INSUFFICIENT_DATA'
        }

    # ---------------------- Fractal Detection ----------------------
    def detect_fractals(self) -> Dict[str, List[Dict]]:
        """Detect classic 5-bar fractals.

        Returns:
            {'swing_highs': [{'bar': int, 'price': float}, ...],
             'swing_lows': [{'bar': int, 'price': float}, ...]}

        Uses 0-based integer bar indices (positions within self.df).
        """
        if self.df is None or len(self.df) < 5:
            return {'swing_highs': [], 'swing_lows': []}

        highs = self.df['high'].values
        lows = self.df['low'].values
        swing_highs = []
        swing_lows = []
        # 5-bar fractal: i is a fractal if it's extreme compared to two bars on each side
        for i in range(2, len(self.df) - 2):
            if (
                highs[i] > highs[i - 1]
                and highs[i] > highs[i - 2]
                and highs[i] > highs[i + 1]
                and highs[i] > highs[i + 2]
            ):
                swing_highs.append({'bar': i, 'price': float(highs[i])})
            if (
                lows[i] < lows[i - 1]
                and lows[i] < lows[i - 2]
                and lows[i] < lows[i + 1]
                and lows[i] < lows[i + 2]
            ):
                swing_lows.append({'bar': i, 'price': float(lows[i])})

        return {'swing_highs': swing_highs, 'swing_lows': swing_lows}

    # ---------------------- Trend / Pullback Identification ----------------------
    def determine_trend_from_fractals(self, swing_highs: List[Dict], swing_lows: List[Dict]) -> str:
        """Simple trend determination from the last two available fractals.

        Returns: 'UPTREND', 'DOWNTREND', or 'NEUTRAL'
        """
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return 'NEUTRAL'

        last_high = swing_highs[-1]['price']
        prev_high = swing_highs[-2]['price']
        last_low = swing_lows[-1]['price']
        prev_low = swing_lows[-2]['price']

        if last_high > prev_high and last_low > prev_low:
            return 'UPTREND'
        if last_high < prev_high and last_low < prev_low:
            return 'DOWNTREND'
        return 'NEUTRAL'

    def identify_internal_pullback(self, swing_highs: List[Dict], swing_lows: List[Dict]) -> Optional[Dict]:
        """Identify the first valid internal pullback (IDM candidate).

        Heuristic used (deterministic and conservative):
        - For UPTREND: look for the most recent swing_low that is a higher low vs previous swing_low
          (i.e., an internal pullback that did NOT make a lower low).
        - For DOWNTREND: look for the most recent swing_high that is a lower high vs previous swing_high
          (i.e., an internal pullback that did NOT make a higher high).
        - For NEUTRAL: return None.

        Returns a dict: {'type': 'bullish'|'bearish', 'bar': int, 'price': float}
        or None if no candidate found.
        """
        trend = self.determine_trend_from_fractals(swing_highs, swing_lows)
        if trend == 'NEUTRAL':
            return None

        if trend == 'UPTREND':
            # Need at least two swing lows
            if len(swing_lows) < 2:
                return None
            # Examine swing_lows from most recent backwards, find the first that is a higher low
            for i in range(len(swing_lows) - 1, 0, -1):
                cur = swing_lows[i]
                prev = swing_lows[i - 1]
                if cur['price'] > prev['price']:
                    return {'type': 'bullish', 'bar': cur['bar'], 'price': cur['price']}
            return None

        # DOWNTREND
        if len(swing_highs) < 2:
            return None
        for i in range(len(swing_highs) - 1, 0, -1):
            cur = swing_highs[i]
            prev = swing_highs[i - 1]
            if cur['price'] < prev['price']:
                return {'type': 'bearish', 'bar': cur['bar'], 'price': cur['price']}
        return None

    def label_idm(self) -> Dict:
        """Public helper: detect fractals and select IDM candidate.

        Returns consolidated IDM data and presence flag.
        """
        fractals = self.detect_fractals()
        swing_highs = fractals['swing_highs']
        swing_lows = fractals['swing_lows']

        if not swing_highs or not swing_lows:
            return {**self._default_analysis(), 'reason_code': 'INSUFFICIENT_FRACTALS'}

        idm = self.identify_internal_pullback(swing_highs, swing_lows)
        if idm is None:
            out = self._default_analysis()
            out.update({'reason_code': 'NO_IDM'})
            return out

        out = self._default_analysis()
        out.update(
            {
                'is_idm_present': True,
                'idm_bar_index': int(idm['bar']),
                'idm_price': float(idm['price']),
                'idm_type': idm['type'],
                'reason_code': 'IDM_PRESENT',
            }
        )
        return out

    # ---------------------- Sweep Detection ----------------------
    def is_wick_sweep(self, target_price: float, start_bar: int, look_forward: Optional[int] = None) -> Dict:
        """Detect if any wick pierces beyond target_price in the forward window.

        Args:
            target_price: price to test for being swept
            start_bar: bar index from which to begin looking (inclusive)
            look_forward: number of bars to scan forward (if None uses default)

        Returns:
            {'is_sweep': bool, 'sweep_bar_index': int|None, 'sweep_price': float|None, 'sweep_wick_type': 'upper'|'lower'|None}
        """
        if look_forward is None:
            look_forward = self.default_look_forward
        n = len(self.df)
        if start_bar is None or start_bar < 0 or start_bar >= n:
            return {'is_sweep': False, 'sweep_bar_index': None, 'sweep_price': None, 'sweep_wick_type': None}

        end = min(n, start_bar + 1 + look_forward)
        highs = self.df['high'].values
        lows = self.df['low'].values

        for idx in range(start_bar + 1, end):
            # Upper wick sweep: high > target_price
            if highs[idx] > target_price:
                return {'is_sweep': True, 'sweep_bar_index': int(idx), 'sweep_price': float(highs[idx]), 'sweep_wick_type': 'upper'}
            # Lower wick sweep: low < target_price
            if lows[idx] < target_price:
                return {'is_sweep': True, 'sweep_bar_index': int(idx), 'sweep_price': float(lows[idx]), 'sweep_wick_type': 'lower'}

        return {'is_sweep': False, 'sweep_bar_index': None, 'sweep_price': None, 'sweep_wick_type': None}

    def confirm_idm_sweep(self, idm_bar_index: int, idm_price: float, idm_type: str, look_forward: Optional[int] = None) -> Dict:
        """Confirm whether the provided IDM candidate was swept.

        - For a 'bullish' idm (in an uptrend) the sweep is a lower wick that pierces idm_price.
        - For a 'bearish' idm (in a downtrend) the sweep is an upper wick that pierces idm_price.

        Returns structured dict with boolean and sweep info and a reason_code.
        """
        out = {'is_idm_swept': False, 'idm_sweep_bar_index': None, 'idm_sweep_price': None, 'reason_code': 'NO_SWEEP'}
        if idm_bar_index is None:
            out['reason_code'] = 'NO_IDM_INDEX'
            return out

        sweep = self.is_wick_sweep(target_price=idm_price, start_bar=idm_bar_index, look_forward=look_forward)
        if not sweep['is_sweep']:
            out['reason_code'] = 'IDM_NOT_SWEPT'
            return out

        # Validate sweep direction matches expectation
        if idm_type == 'bullish' and sweep['sweep_wick_type'] == 'lower':
            out.update({'is_idm_swept': True, 'idm_sweep_bar_index': sweep['sweep_bar_index'], 'idm_sweep_price': sweep['sweep_price'], 'reason_code': 'IDM_WICK_SWEEP'})
            return out
        if idm_type == 'bearish' and sweep['sweep_wick_type'] == 'upper':
            out.update({'is_idm_swept': True, 'idm_sweep_bar_index': sweep['sweep_bar_index'], 'idm_sweep_price': sweep['sweep_price'], 'reason_code': 'IDM_WICK_SWEEP'})
            return out

        # Sweep occurred but in the opposite direction; treat as not a valid IDM sweep
        out['reason_code'] = 'SWEEP_WRONG_DIRECTION'
        return out

    # ---------------------- Structure Confirmation After IDM ----------------------
    def determine_structure_after_idm(self, idm_info: Dict, look_forward: Optional[int] = None) -> Dict:
        """After IDM sweep is confirmed, determine whether structure (MSS/CHOCH/BOS) is confirmed.

        Conservative approach used:
        - If idm_info['is_idm_swept'] is False -> not confirmed
        - If swept: look forward from sweep bar and detect a Break Of Structure (BOS)
          defined as a close beyond the last fractal extreme in the opposite direction.
        - If BOS occurs and fractal ordering indicates a CHOCH, label CHOCH else MSS.

        Returns dict with structure_confirmed (bool), mss_or_choch, bos_or_sweep_occurred (bool), bos_level
        """
        out = {'structure_confirmed': False, 'mss_or_choch': 'NONE', 'bos_or_sweep_occurred': False, 'bos_level': None, 'reason_code': 'IDM_NOT_SWEPT'}
        if not idm_info.get('is_idm_swept', False):
            return out

        # Gather fractals to determine last extremes
        fractals = self.detect_fractals()
        swing_highs = fractals['swing_highs']
        swing_lows = fractals['swing_lows']
        n = len(self.df)

        sweep_bar = idm_info.get('idm_sweep_bar_index')
        if sweep_bar is None:
            out['reason_code'] = 'NO_SWEEP_BAR'
            return out

        # Determine target BOS level depending on IDM type
        if idm_info.get('idm_type') == 'bullish':
            # After low sweep, look for bullish BOS: close > most recent swing high
            if not swing_highs:
                out['reason_code'] = 'NO_SWING_HIGHS'
                return out
            last_high = swing_highs[-1]['price']
            # Look forward after sweep bar for a close > last_high
            end = min(n, sweep_bar + 1 + (look_forward or self.default_look_forward))
            closes = self.df['close'].values
            for idx in range(sweep_bar + 1, end):
                if closes[idx] > last_high:
                    # BOS bullish detected
                    out.update({'structure_confirmed': True, 'bos_or_sweep_occurred': True, 'bos_level': float(last_high)})
                    # Determine CHOCH vs MSS: compare last_low to prev_low if available
                    if len(swing_lows) >= 2 and swing_lows[-1]['price'] <= swing_lows[-2]['price']:
                        out['mss_or_choch'] = 'CHOCH_BULLISH'
                    else:
                        out['mss_or_choch'] = 'MSS_BULLISH'
                    out['reason_code'] = 'STRUCTURE_CONFIRMED'
                    return out
            out['reason_code'] = 'NO_BOS_AFTER_SWEEP'
            return out

        # bearish idm
        if not swing_lows:
            out['reason_code'] = 'NO_SWING_LOWS'
            return out
        last_low = swing_lows[-1]['price']
        end = min(n, sweep_bar + 1 + (look_forward or self.default_look_forward))
        closes = self.df['close'].values
        for idx in range(sweep_bar + 1, end):
            if closes[idx] < last_low:
                out.update({'structure_confirmed': True, 'bos_or_sweep_occurred': True, 'bos_level': float(last_low)})
                if len(swing_highs) >= 2 and swing_highs[-1]['price'] >= swing_highs[-2]['price']:
                    out['mss_or_choch'] = 'CHOCH_BEARISH'
                else:
                    out['mss_or_choch'] = 'MSS_BEARISH'
                out['reason_code'] = 'STRUCTURE_CONFIRMED'
                return out
        out['reason_code'] = 'NO_BOS_AFTER_SWEEP'
        return out

    # ---------------------- Consolidated API ----------------------
    def get_idm_state(self, look_forward: Optional[int] = None) -> Dict:
        """Primary method to be used by the permission gate (main state machine).

        Returns a dict with explicit fields required by the calling code:
        - is_idm_present, idm_bar_index, idm_price, idm_type
        - is_idm_swept, idm_sweep_bar_index, idm_sweep_price
        - structure_confirmed, mss_or_choch, bos_or_sweep_occurred, bos_level
        - reason_code (one of the structured reason codes)
        """
        # Validate data sufficiency
        if self.df is None or len(self.df) < 5:
            out = self._default_analysis()
            out['reason_code'] = 'INSUFFICIENT_DATA'
            return out

        # Step 1: detect fractals and label IDM
        label = self.label_idm()
        if not label.get('is_idm_present'):
            # label contains reason_code 'NO_IDM' or 'INSUFFICIENT_FRACTALS'
            return label

        idm_bar = label['idm_bar_index']
        idm_price = label['idm_price']
        idm_type = label['idm_type']

        # Step 2: confirm IDM sweep (look-forward)
        sweep_info = self.confirm_idm_sweep(idm_bar_index=idm_bar, idm_price=idm_price, idm_type=idm_type, look_forward=look_forward)

        out = {**label}
        out.update(sweep_info)

        if not sweep_info.get('is_idm_swept'):
            # attach reason and return
            out['structure_confirmed'] = False
            out['mss_or_choch'] = 'NONE'
            out['bos_or_sweep_occurred'] = False
            out['bos_level'] = None
            # reason_code already set by confirm_idm_sweep
            return out

        # Step 3: determine structure after confirmed IDM sweep
        idm_info = {
            'is_idm_swept': bool(sweep_info.get('is_idm_swept', False)),
            'idm_sweep_bar_index': sweep_info.get('idm_sweep_bar_index'),
            'idm_type': idm_type,
        }
         # day 5 for checking the integration working preopely for oeping mt5 or not 
        structure_info = self.determine_structure_after_idm(idm_info, look_forward=look_forward)

        out.update(structure_info)
        return out