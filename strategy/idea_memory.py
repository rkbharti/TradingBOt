import json
import os
from datetime import datetime, timedelta

class IdeaMemory:
    """
    Long-Term Memory for the Trading Bot.
    Prevents 'Revenge Trading' and repeated losses on the same setup.
    Persists data to 'ideamemory.json' to survive restarts.
    """
    def __init__(self, expiry_minutes=30, memory_file='ideamemory.json'):
        self.expiry_minutes = expiry_minutes
        self.memory_file = memory_file
        self.short_term_memory = {}
        self.load_memory()  # <--- Loads previous state on startup
        self.save_memory()
    def load_memory(self):
        """Load memory from file so we don't forget losses on restart"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, 'r') as f:
                    data = json.load(f)
                    # Convert string timestamps back to datetime objects
                    current_time = datetime.now()
                    self.short_term_memory = {}
                    
                    for key, value in data.items():
                        # Handle potential missing keys in old JSON versions
                        if 'block_until' in value:
                            block_until = datetime.fromisoformat(value['block_until'])
                            # Only keep memories that haven't expired yet
                            if block_until > current_time:
                                self.short_term_memory[key] = {
                                    'block_until': block_until,
                                    'reason': value.get('reason', 'Blocked')
                                }
                print(f"🧠 IdeaMemory loaded: {len(self.short_term_memory)} active blocks found.")
            except Exception as e:
                print(f"⚠️ Failed to load IdeaMemory: {e}")
                self.short_term_memory = {}

    def save_memory(self):
        """Save current memory to file immediately"""
        try:
            # Convert datetime objects to string for JSON serialization
            serializable_memory = {}
            for key, value in self.short_term_memory.items():
                serializable_memory[key] = {
                    'block_until': value['block_until'].isoformat(),
                    'reason': value['reason']
                }
            
            with open(self.memory_file, 'w') as f:
                json.dump(serializable_memory, f, indent=4)
        except Exception as e:
            print(f"⚠️ Failed to save IdeaMemory: {e}")

    def _get_key(self, direction, zone, session):
        """Create a unique key for the trade setup"""
        zone = str(zone).upper()
        if zone not in ["PREMIUM", "DISCOUNT", "EQ", "POI"]:
            zone = "EQ"

        return f"{direction}_{zone}_{session}"


    def mark_result(self, direction, zone, session, outcome):
        """
        Record the outcome of a trade.
        - WIN: Good job, keep trading this setup.
        - LOSS: Ouch. Block this specific setup for 30 mins.
        """
        # Handle simple calls (direction, outcome) for legacy compatibility
        if session in ['WIN', 'LOSS']: 
            outcome = session
            session = 'UNKNOWN'
            zone = 'UNKNOWN'

        key = self._get_key(direction, zone, session)

        if outcome == 'LOSS':
            # PAIN LOGIC: If we lose, stop doing this specific thing for a while
            block_until = datetime.now() + timedelta(minutes=self.expiry_minutes)
            self.short_term_memory[key] = {
                'block_until': block_until,
                'reason': f"Lost {direction} in {zone} ({session})"
            }
            print(f"🧠 MEMORY BLOCK: Blocking {key} until {block_until.strftime('%H:%M')}")
            self.save_memory()  # <--- SAVE IMMEDIATELY
            
        elif outcome == 'WIN':
            # PLEASURE LOGIC: If we win, clear any blocks (reinforce behavior)
            if key in self.short_term_memory:
                del self.short_term_memory[key]
                print(f"🧠 MEMORY CLEAR: {key} is safe to trade again.")
                self.save_memory()  # <--- SAVE IMMEDIATELY

    def is_allowed(self, direction, zone, session, zone_bucket=None):
        """
        Check if we are allowed to take this trade.
        Returns: True (Allowed) or False (Blocked)
        """
        # zone_bucket is accepted to match main.py signature but ignored for the key
        # logic to ensure we block the whole context, not just the specific pip range.
        key = self._get_key(direction, zone, session)
        
        if key in self.short_term_memory:
            memory = self.short_term_memory[key]
            if datetime.now() < memory['block_until']:
                # Still hurting from the last loss
                return False
            else:
                # Time has healed the wound, forget it
                del self.short_term_memory[key]
                self.save_memory()
                return True
        
        return True

    def reset_all(self, reason="context_change"):
        """Clear all memory (Manual reset or major context shift)"""
        self.short_term_memory.clear()
        self.save_memory()