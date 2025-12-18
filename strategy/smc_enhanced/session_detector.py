"""
Session Detector Module
Trading Session Analysis for Inducement Weighting

Key Concepts:
- London (3:00-12:00 GMT) = Highest inducement reliability (85%)
- New York (13:00-22:00 GMT) = High inducement reliability (80%)
- Asian (00:00-09:00 GMT) = Medium inducement reliability (60%)
- Overlap (13:00-17:00 GMT) = Maximum reliability (95%)
"""

from datetime import datetime, timezone
import pytz


class SessionDetector:
    """
    Detect current trading session and weight inducement accordingly.
    
    From Guardeer + Trading Best Practices:
    - London session inducement = institutional trap setups
    - NY session inducement = major reversals
    - Asian session = range-bound (lower reliability)
    - Overlap = cleanest setups
    """
    
    def __init__(self):
        self.gmt = pytz.timezone('GMT')
        self.sessions = {
            'ASIAN': {
                'start': 0,   # 00:00 GMT
                'end': 9,     # 09:00 GMT
                'reliability': 0.60,
                'description': 'Asian Session - Range-bound'
            },
            'LONDON': {
                'start': 3,   # 03:00 GMT (London open)
                'end': 12,    # 12:00 GMT
                'reliability': 0.85,
                'description': 'London Session - Institutional'
            },
            'NEW_YORK': {
                'start': 13,  # 13:00 GMT (NY open)
                'end': 22,    # 22:00 GMT (NY close)
                'reliability': 0.80,
                'description': 'New York Session - Major moves'
            },
            'OVERLAP': {
                'start': 13,  # 13:00 GMT (London+NY overlap)
                'end': 17,    # 17:00 GMT (London close)
                'reliability': 0.95,
                'description': 'London-NY Overlap - Maximum liquidity'
            }
        }
    
    def get_current_session(self, current_time=None):
        """
        Get current trading session based on GMT time.
        
        Args:
            current_time: datetime object (optional, uses now() if not provided)
        
        Returns:
            dict with session info
        """
        try:
            if current_time is None:
                current_time = datetime.now(self.gmt)
            elif current_time.tzinfo is None:
                # If timezone-naive, assume it's in GMT
                current_time = self.gmt.localize(current_time)
            else:
                # Convert to GMT
                current_time = current_time.astimezone(self.gmt)
            
            hour = current_time.hour
            
            # Check for overlap first (highest priority)
            overlap = self.sessions['OVERLAP']
            if overlap['start'] <= hour < overlap['end']:
                return {
                    'session': 'OVERLAP',
                    'reliability': overlap['reliability'],
                    'description': overlap['description'],
                    'hour_gmt': hour,
                    'confidence_multiplier': 1.3  # 30% boost for overlap
                }
            
            # Check London
            london = self.sessions['LONDON']
            if london['start'] <= hour < london['end']:
                return {
                    'session': 'LONDON',
                    'reliability': london['reliability'],
                    'description': london['description'],
                    'hour_gmt': hour,
                    'confidence_multiplier': 1.15  # 15% boost
                }
            
            # Check New York
            ny = self.sessions['NEW_YORK']
            if ny['start'] <= hour < ny['end']:
                return {
                    'session': 'NEW_YORK',
                    'reliability': ny['reliability'],
                    'description': ny['description'],
                    'hour_gmt': hour,
                    'confidence_multiplier': 1.10  # 10% boost
                }
            
            # Default to Asian
            asian = self.sessions['ASIAN']
            return {
                'session': 'ASIAN',
                'reliability': asian['reliability'],
                'description': asian['description'],
                'hour_gmt': hour,
                'confidence_multiplier': 0.85  # 15% penalty
            }
            
        except Exception as e:
            print(f"⚠️ Error detecting session: {e}")
            return {
                'session': 'UNKNOWN',
                'reliability': 0.70,
                'description': 'Unknown session',
                'hour_gmt': 0,
                'confidence_multiplier': 1.0
            }
    
    def weight_inducement(self, inducement_data, current_time=None):
        """
        Apply session-based weighting to inducement detection.
        
        Args:
            inducement_data: dict from InducementDetector
            current_time: datetime (optional)
        
        Returns:
            dict with weighted inducement
        """
        try:
            if not inducement_data.get('inducement'):
                return inducement_data
            
            session = self.get_current_session(current_time)
            
            # Apply session multiplier to confidence
            base_confidence = inducement_data.get('confidence', 'MEDIUM')
            reliability = session['reliability']
            
            # Determine weighted confidence
            if reliability >= 0.90:
                weighted_confidence = 'VERY_HIGH'
            elif reliability >= 0.80:
                weighted_confidence = 'HIGH'
            elif reliability >= 0.70:
                weighted_confidence = 'MEDIUM'
            else:
                weighted_confidence = 'LOW'
            
            # Update inducement data
            inducement_data['session'] = session['session']
            inducement_data['session_reliability'] = reliability
            inducement_data['weighted_confidence'] = weighted_confidence
            inducement_data['confidence_multiplier'] = session['confidence_multiplier']
            inducement_data['session_description'] = session['description']
            
            return inducement_data
            
        except Exception as e:
            print(f"⚠️ Error weighting inducement: {e}")
            return inducement_data
