import json
import sqlite3
from pathlib import Path
from datetime import datetime

def generate_audit(db_path: str, output_path: str):
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    
    audit = {
        "generated_at": datetime.utcnow().isoformat(),
        "campaign_summary": {},
        "capability_summary": {},
        "evidence_stats": {}
    }
    
    # Campaign stats
    rounds = db.execute("SELECT status, COUNT(*) as n FROM campaign_rounds GROUP BY status").fetchall()
    audit["campaign_summary"]["round_status_counts"] = {row["status"]: row["n"] for row in rounds}
    
    # Evidence stats
    evidence = db.execute("SELECT source_type, COUNT(*) as n FROM evidence GROUP BY source_type").fetchall()
    audit["evidence_stats"]["source_type_counts"] = {row["source_type"]: row["n"] for row in evidence}
    
    # Skill stats
    skills = db.execute("SELECT status, COUNT(*) as n FROM skills GROUP BY status").fetchall()
    audit["capability_summary"]["skill_status_counts"] = {row["status"]: row["n"] for row in skills}
    
    with open(output_path, "w") as f:
        json.dump(audit, f, indent=2)
    
    print(f"Audit report generated: {output_path}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python3 generate_epoch_audit.py <db_path> <output_path>")
    else:
        generate_audit(sys.argv[1], sys.argv[2])
