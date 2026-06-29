import json
import glob
from collections import Counter

def analyze_audits():
    log_files = glob.glob("logs/decisions/audit*.jsonl")
    if not log_files:
        print("No audit logs found.")
        return

    for file_path in log_files:
        print(f"\n--- Analyzing {file_path} ---")
        reasons = Counter()
        total_cycles = 0
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        total_cycles += 1
                        
                        action = record.get("action", "")
                        reason = record.get("reason", "Unknown")
                        
                        if action == "NO_ACTION" or action == "":
                            reasons[reason] += 1
                            
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
            
        print(f"Total Evaluation Cycles: {total_cycles}")
        print("Top Reasons for NO_ACTION:")
        for reason, count in reasons.most_common(15):
            pct = (count / total_cycles) * 100 if total_cycles > 0 else 0
            print(f"  [{count:5d} | {pct:5.1f}%] {reason}")

if __name__ == "__main__":
    analyze_audits()
