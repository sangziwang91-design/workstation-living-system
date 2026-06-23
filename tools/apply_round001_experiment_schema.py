from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "source" / "src" / "wls" / "db.py"


def replace_once(text: str, old: str, new: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise SystemExit("expected database schema anchor not found")
    return text.replace(old, new, 1)


text = DB.read_text(encoding="utf-8")
text = replace_once(text, "SCHEMA_VERSION = 1", "SCHEMA_VERSION = 2")
anchor = '''                CREATE TABLE IF NOT EXISTS cycles (
                    cycle_id TEXT PRIMARY KEY,
'''
insert = '''                CREATE TABLE IF NOT EXISTS skill_experiments (
                    experiment_id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL,
                    skill_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    baseline_json TEXT NOT NULL,
                    result_json TEXT,
                    artifact_path TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(skill_id) REFERENCES skills(skill_id)
                );
                CREATE INDEX IF NOT EXISTS idx_skill_experiments_skill
                    ON skill_experiments(skill_id, started_at DESC);

                CREATE TABLE IF NOT EXISTS recovery_experiments (
                    experiment_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    status TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    baseline_json TEXT NOT NULL,
                    result_json TEXT,
                    artifact_path TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(candidate_id) REFERENCES evolution_candidates(candidate_id)
                );
                CREATE INDEX IF NOT EXISTS idx_recovery_experiments_candidate
                    ON recovery_experiments(candidate_id, started_at DESC);

                CREATE TABLE IF NOT EXISTS cycles (
                    cycle_id TEXT PRIMARY KEY,
'''
text = replace_once(text, anchor, insert)
DB.write_text(text, encoding="utf-8")
print("Round 001 experiment schema migration applied.")
