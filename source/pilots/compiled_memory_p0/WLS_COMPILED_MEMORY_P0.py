from __future__ import annotations
import argparse,json,re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

BUDGET={"weekly_hours_max":4,"monthly_api_usd_max":20,"scope":30,"r_loop_n":2,"expand":False}
SOURCES=[('ARCH-001', 'wls_architecture', 'WLS Canonical Genome', 'https://github.com/sangziwang91-design/workstation-living-system-private/blob/main/LIVING_SYSTEM_GENOME.md', 2, 'current'), ('ARCH-002', 'wls_architecture', 'WLS Current State', 'https://github.com/sangziwang91-design/workstation-living-system-private/blob/main/CURRENT_STATE.yaml', 1, 'current_with_known_stale_sections'), ('ARCH-003', 'wls_architecture', 'Repo-Native Current Chain', 'https://github.com/sangziwang91-design/workstation-living-system-private/blob/main/.evolution/CURRENT_CHAIN.json', 1, 'current'), ('ARCH-004', 'wls_architecture', 'Canonical LivingSystem Runtime', 'https://github.com/sangziwang91-design/workstation-living-system-private/blob/main/source/src/wls/runtime.py', 0, 'current'), ('ARCH-005', 'wls_architecture', 'WLS Stores and MemoryStore', 'https://github.com/sangziwang91-design/workstation-living-system-private/blob/main/source/src/wls/stores.py', 0, 'current'), ('STATUS-001', 'map_007_13_14_15', 'MAP-000 Internal Master Architecture', 'https://app.notion.com/p/cc1edc0027684bdf9b4b5125c3073905', 3, 'current_semantic'), ('STATUS-002', 'map_007_13_14_15', 'Province 007 Engineering Practice', 'https://app.notion.com/p/35640ff6ad628166a0a6d29dd3d63ddc', 3, 'current_semantic'), ('STATUS-003', 'map_007_13_14_15', '13 Engineering Defect Pattern Library', 'https://app.notion.com/p/36b40ff6ad62818abee9dccf8e063768', 3, 'current_semantic'), ('STATUS-004', 'map_007_13_14_15', '14 SZ System Factory', 'https://app.notion.com/p/36b40ff6ad62810389b7c7b3c27edfcb', 3, 'current_semantic'), ('STATUS-005', 'map_007_13_14_15', '15 SZ Runtime Core', 'https://app.notion.com/p/36e40ff6ad62812cba73cf557d6b0f73', 3, 'current_semantic'), ('EXP-001', 'key_experiment', 'EXP-082 Repo-Native Model Labor and WLS Evolution Chain', 'https://app.notion.com/p/38940ff6ad6281b6bd69d700c9322d77', 3, 'current'), ('EXP-002', 'key_experiment', 'EXP-080E Three Hardest-Evidence Run Archive', 'https://app.notion.com/p/37a40ff6ad628192a9eeed5a6225c3d4', 4, 'historical_evidence'), ('EXP-003', 'key_experiment', 'EXP-080F BRC1-8 DeepSeek N100 Live Validation', 'https://app.notion.com/p/37f40ff6ad6281a6a6d6ebedbecd3117', 4, 'historical_evidence'), ('EXP-004', 'key_experiment', 'EXP-080G BRC1-8 N100 Final Package', 'https://app.notion.com/p/38040ff6ad628156a483e6aa32f6e922', 4, 'historical_evidence'), ('EXP-005', 'key_experiment', 'BRC1-8 DeepSeek N>500 Five-System Sync', 'https://app.notion.com/p/37f40ff6ad6281d2aa4cca68246e9a0d', 4, 'historical_evidence'), ('PAPER-001', 'paper_evidence', 'Paper Submission Status Record', 'https://app.notion.com/p/37240ff6ad6281c38c8dfc5d7335e579', 3, 'current_semantic'), ('PAPER-002', 'paper_evidence', 'EXP-076N Testing-as-Runtime Manuscript Submission', 'https://app.notion.com/p/1078fe45ccc0489d8e6388250cc0aac6', 4, 'historical_evidence'), ('PAPER-003', 'paper_evidence', 'EXP-076J Five-Paper Literature Mapping', 'https://app.notion.com/p/36c40ff6ad62817ea31ac723db544a70', 4, 'historical_plan'), ('PAPER-004', 'paper_evidence', 'EXP-076L Publishable Paper Architecture', 'https://app.notion.com/p/36f40ff6ad6281758e15f12b5e191a5f', 4, 'historical_plan'), ('PAPER-005', 'paper_evidence', 'EXP-076I Personal Paper Production Base', 'https://app.notion.com/p/36c40ff6ad6281039868ce24062cda46', 4, 'historical_evidence'), ('HANDOFF-001', 'engineering_handoff', 'PR 9 Activate Repo-Native Evolution Chain', 'https://github.com/sangziwang91-design/workstation-living-system-private/pull/9', 1, 'merged'), ('HANDOFF-002', 'engineering_handoff', 'PR 11 Finalize Repo-Native Evolution Chain State', 'https://github.com/sangziwang91-design/workstation-living-system-private/pull/11', 1, 'merged'), ('HANDOFF-003', 'engineering_handoff', 'PR 13 Establish One Project Root', 'https://github.com/sangziwang91-design/workstation-living-system-private/pull/13', 1, 'merged'), ('HANDOFF-004', 'engineering_handoff', 'PR 16 Local Runner Migration', 'https://github.com/sangziwang91-design/workstation-living-system-private/pull/16', 1, 'open_draft'), ('HANDOFF-005', 'engineering_handoff', 'PR 8 ET004 Persistent Goal Continuity', 'https://github.com/sangziwang91-design/workstation-living-system-private/pull/8', 1, 'open_draft_behind_main'), ('OLD-001', 'obsolete_conflict', 'PR 10 Superseded Evolution Completion', 'https://github.com/sangziwang91-design/workstation-living-system-private/pull/10', 1, 'superseded'), ('OLD-002', 'obsolete_conflict', 'PR 12 Superseded Provider Hub', 'https://github.com/sangziwang91-design/workstation-living-system-private/pull/12', 1, 'superseded'), ('OLD-003', 'obsolete_conflict', 'PR 14 Blocked Provider Hub Rebuild', 'https://github.com/sangziwang91-design/workstation-living-system-private/pull/14', 1, 'closed_blocked'), ('OLD-004', 'obsolete_conflict', '2026-05-29 13/14/15 Current-State Archive', 'https://app.notion.com/p/36f40ff6ad6281d38ffee6816adcb8a6', 5, 'historical_only'), ('OLD-005', 'obsolete_conflict', '2026-05-29 Runtime Protocol Archive', 'https://app.notion.com/p/36f40ff6ad62818a86b6fc64739eed97', 5, 'historical_only')]
CLAIMS=[('C-001', 'canonical_runtime', 'source/src/wls/runtime.py::LivingSystem', 'VERIFIED', ['ARCH-003', 'ARCH-004'], 'current_state', 'The sole canonical runtime is source/src/wls/runtime.py::LivingSystem.', ['A parallel brain module could have been added later.', 'A documentation file could name a different runtime.']), ('C-002', 'engineering_truth', 'GITHUB_AND_RUNTIME_EVIDENCE', 'VERIFIED', ['ARCH-003', 'EXP-001'], 'current_state', 'GitHub and runtime evidence are the engineering truth source.', ['Notion contains newer prose.', 'A chat may report a completed change.']), ('C-003', 'notion_role', 'LONG_HORIZON_SEMANTIC_AND_EXPERIMENT_GOVERNANCE', 'VERIFIED', ['ARCH-003', 'EXP-001'], 'current_state', 'Notion is a long-horizon semantic and experiment-governance source and cannot override current engineering state.', ['Notion pages are more comprehensive.', 'Notion timestamps can be newer.']), ('C-004', 'repo_native_chain_status', 'ACTIVE_CI_VERIFIED', 'VERIFIED', ['ARCH-003', 'HANDOFF-001', 'HANDOFF-002'], 'current_state', 'The repo-native evolution chain status is ACTIVE_CI_VERIFIED.', ['The bootstrap could remain pending.', 'A failed Windows job could invalidate the chain.']), ('C-005', 'active_code_worker', 'chatgpt_interactive', 'VERIFIED', ['ARCH-003'], 'current_state', 'The sole active code worker in the current registry is chatgpt_interactive.', ['Claude Code is installed locally.', 'Other providers are listed as candidates.']), ('C-006', 'other_provider_status', 'PENDING_NOT_ACTIVATED', 'VERIFIED', ['ARCH-003', 'EXP-001'], 'current_state', 'Other provider admission is PENDING_NOT_ACTIVATED.', ['The user can manually use Claude or Codex.', 'Provider Hub branches exist.']), ('C-007', 'current_evolution_target', 'EVOLUTION-TARGET-004 / PR 8 / DRAFT_PULL_REQUEST_IN_FLIGHT_AND_BEHIND_MAIN', 'VERIFIED', ['ARCH-003', 'HANDOFF-005'], 'current_state', 'The chain-owned current target is ET004 Persistent Goal System and Long-Horizon Continuity in PR 8; duplicate ET004 implementation is prohibited.', ['PR 16 is newer.', 'Compiled Memory is the current conversation task.']), ('C-008', 'memory_authority', 'MemoryStore', 'VERIFIED', ['ARCH-002', 'ARCH-005'], 'current_state', 'MemoryStore remains the sole WLS memory authority; CausalMemoryIndex is an index and validity layer.', ['Compiled Memory sounds like another memory store.', 'Obsidian will hold compiled notes.']), ('C-009', 'project_package_root', 'repository root / source/src/wls', 'VERIFIED', ['HANDOFF-003'], 'current_state', 'The repository has one project root and the sole package root is source/src/wls.', ['Historical root/src/wls existed.', 'Provider Hub branches were based on older layout.']), ('C-010', 'local_runner_pr', 'PR 16 / open draft / verification pending', 'VERIFIED', ['HANDOFF-004'], 'current_state', 'PR 16 is an open draft for Windows self-hosted runner migration and must not merge before local checks finish.', ['Static diff is mergeable.', 'The runner migration is operationally urgent.']), ('C-011', 'compiled_memory_maturity', 'P0_SANDBOX_READ_ONLY', 'INFERENCE', ['ARCH-001', 'ARCH-003', 'STATUS-003', 'STATUS-004'], 'pilot', 'P0 is a sandboxed read-only compiled-memory pilot, not a proven second brain or production memory system.', ['It has a complete manifest and validators.', 'It can answer all fixture questions.'])]
QUESTIONS=[('T1-Q01', 'C-001', ['What is the canonical WLS runtime?', 'WLS 唯一 canonical runtime 是什么？'], 'source/src/wls/runtime.py::LivingSystem'), ('T1-Q02', 'C-002', ['What is the engineering truth source?', '工程事实源是什么？'], 'GITHUB_AND_RUNTIME_EVIDENCE'), ('T1-Q03', 'C-003', ['What role does Notion have?', 'Notion 在 WLS 中是什么角色？'], 'LONG_HORIZON_SEMANTIC_AND_EXPERIMENT_GOVERNANCE'), ('T1-Q04', 'C-004', ['What is the repo-native chain status?', 'repo-native 演化链当前状态？'], 'ACTIVE_CI_VERIFIED'), ('T1-Q05', 'C-005', ['Who is the active code worker?', '当前唯一 active code worker 是谁？'], 'chatgpt_interactive'), ('T1-Q06', 'C-006', ['Are other providers activated?', 'Claude Gemini DeepSeek 等是否已激活？'], 'PENDING_NOT_ACTIVATED'), ('T1-Q07', 'C-007', ['What is the current evolution target?', '当前 evolution target 和 PR 是什么？'], 'EVOLUTION-TARGET-004 / PR 8 / DRAFT_PULL_REQUEST_IN_FLIGHT_AND_BEHIND_MAIN'), ('T1-Q08', 'C-008', ['What is the sole memory authority?', 'WLS 的唯一记忆权威是什么？'], 'MemoryStore'), ('T1-Q09', 'C-009', ['What are the project and package roots?', '仓库唯一项目根和包根是什么？'], 'repository root / source/src/wls'), ('T1-Q10', 'C-010', ['What is the state of PR 16?', 'PR 16 当前能否合并？'], 'PR 16 / open draft / verification pending')]
CONFLICTS=[('X-001', 'ARCH-001', 'C-007', None, 'stale_current_state'), ('X-002', 'ARCH-002', 'C-004', None, 'stale_branch_state'), ('X-003', 'OLD-001', None, 'HANDOFF-002', 'superseded'), ('X-004', 'OLD-002', None, 'OLD-003', 'superseded'), ('X-005', 'OLD-004', None, 'ARCH-003', 'temporal_authority')]
CURRENT={"current","current_semantic","merged","open_draft","open_draft_behind_main","current_with_known_stale_sections"}
FORBIDDEN={"second brain complete","production proven","fully autonomous","completely solved","global first","industry standard","不可替代","完全解决","已证明意识","已实现 agi"}

def smap():
 return {x[0]:{"id":x[0],"category":x[1],"title":x[2],"url":x[3],"rank":x[4],"lifecycle":x[5]} for x in SOURCES}

def cmap():
 return {x[0]:{"id":x[0],"key":x[1],"value":x[2],"status":x[3],"source_ids":x[4],"scope":x[5],"statement":x[6],"counterexamples":x[7]} for x in CLAIMS}

def toks(s):
 return {x.lower() for x in re.findall(r"[A-Za-z0-9_.:/#-]+|[\u4e00-\u9fff]{2,}",s) if x.strip()}

def url_ok(u):
 p=urlparse(u); return p.scheme=="https" and p.netloc in {"github.com","app.notion.com"} and bool(p.path.strip("/"))

def answer(q):
 qt=toks(q); best=None
 for x in QUESTIONS:
  z=set()
  for v in x[2]: z|=toks(v)
  score=len(qt&z)/max(1,len(qt|z))
  if best is None or score>best[0]: best=(score,x)
 if not best or best[0]<=0:return {"status":"UNKNOWN","answer":None,"sources":[]}
 c=cmap()[best[1][1]]; s=smap()
 return {"status":c["status"],"answer":c["value"],"claim_id":c["id"],"sources":[s[i]["url"] for i in c["source_ids"]],"score":round(best[0],6)}

def search_sources(q):
 qt=toks(q); out=[]
 for s in smap().values():
  score=len(qt&toks(s["title"]))/max(1,len(qt|toks(s["title"])))
  if score: out.append({"source_id":s["id"],"title":s["title"],"lifecycle":s["lifecycle"],"eligible_for_current":s["lifecycle"] in CURRENT,"score":round(score,6),"url":s["url"]})
 return sorted(out,key=lambda x:x["score"],reverse=True)

def current_claims():
 return [c for c in cmap().values() if c["scope"]=="current_state"]

def check_sources():
 s=smap(); e=[]; verified=[c for c in cmap().values() if c["status"]=="VERIFIED"]
 for c in verified:
  if not c["source_ids"]:e.append({"claim_id":c["id"],"error":"VERIFIED_WITHOUT_SOURCE"});continue
  for i in c["source_ids"]:
   if i not in s:e.append({"claim_id":c["id"],"source_id":i,"error":"UNKNOWN_SOURCE"})
   elif not url_ok(s[i]["url"]):e.append({"claim_id":c["id"],"source_id":i,"error":"UNRESOLVABLE_URL"})
 n=sum(len(c["source_ids"]) for c in verified)
 return {"test":"T3","verified_claims":len(verified),"source_bindings":n,"valid_source_bindings":n-len(e),"coverage":1 if not n else (n-len(e))/n,"errors":e,"passed":not e}

def check_stale():
 s=smap(); ids=set(cmap()); yes=[]; no=[]
 for i,old,cc,cs,t in CONFLICTS:
  stale=s.get(old); current=(cc in ids) if cc else (cs in s)
  signal=bool(stale) and (stale["lifecycle"] in {"superseded","historical_only","closed_blocked","current_with_known_stale_sections"} or t in {"stale_current_state","stale_branch_state"})
  (yes if signal and current else no).append({"conflict_id":i,"reason":t} if signal and current else i)
 rate=len(yes)/(len(yes)+len(no))
 return {"test":"T2","detected":yes,"missed":no,"detection_rate":rate,"threshold":.8,"passed":rate>=.8}

def inspect_ceiling(items):
 e=[]
 for c in items:
  st=c.get("statement","").lower()
  if c.get("status")=="VERIFIED" and not c.get("source_ids"):e.append({"claim_id":c.get("id"),"error":"UNSOURCED_VERIFIED"})
  for p in FORBIDDEN:
   if p.lower() in st:e.append({"claim_id":c.get("id"),"error":"CLAIM_CEILING","phrase":p})
 return e

def check_claim_ceiling():
 e=inspect_ceiling(list(cmap().values()))
 return {"test":"T4","errors":e,"unsourced_verified_count":sum(x["error"]=="UNSOURCED_VERIFIED" for x in e),"passed":not e}

def check_canonical_conflict():
 s=smap();e=[]; keys=Counter(c["key"] for c in current_claims())
 e += [{"key":k,"error":"DUPLICATE_CURRENT_KEY"} for k,n in keys.items() if n>1]
 for c in current_claims():
  for i in c["source_ids"]:
   if s[i]["lifecycle"] in {"superseded","historical_only","closed_blocked"}:e.append({"claim_id":c["id"],"source_id":i,"error":"HISTORICAL_SOURCE_PROMOTED"})
 old=search_sources("provider hub superseded blocked")
 bad=[x for x in old if x["source_id"] in {"OLD-001","OLD-002","OLD-003"} and x["eligible_for_current"]]
 if bad:e.append({"error":"OBSOLETE_PLAN_RESURRECTED","results":bad})
 return {"test":"T5","errors":e,"obsolete_results":old,"passed":not e}

def check_state_recovery():
 r=[];ok=0
 for qid,cid,variants,expected in QUESTIONS:
  a=answer(variants[1]); passed=a.get("answer")==expected and a.get("status")=="VERIFIED" and bool(a.get("sources"));ok+=passed
  r.append({"question_id":qid,"query":variants[1],"expected":expected,"actual":a.get("answer"),"sources":a.get("sources",[]),"passed":bool(passed)})
 er=(len(QUESTIONS)-ok)/len(QUESTIONS)
 return {"test":"T1","questions":len(QUESTIONS),"correct":ok,"errors":len(QUESTIONS)-ok,"error_rate":er,"threshold":"<0.10","results":r,"passed":er<.10}

def render():
 s=smap();lines=["# WLS Compiled Memory P0 CURRENT_STATE","","> GENERATED READ-ONLY PROJECTION. GitHub/runtime current-state anchors override this file.","> Notion is read-only semantic context. This file never writes back or replaces WLS MemoryStore.",""]
 for c in current_claims():
  links=" · ".join(f"[{i}]({s[i]['url']})" for i in c["source_ids"])
  lines += [f"## {c['key']}","",f"- **Value:** `{c['value']}`",f"- **Evidence:** `{c['status']}`",f"- **Claim:** {c['statement']}",f"- **Sources:** {links}",""]
 lines += ["## Claim Ceiling","","- P0 is a sandboxed read-only projection, not a new system.","- Passing fixture tests does not establish real-host utility, scale, or external validation.","- Any VERIFIED item without resolvable sources must be downgraded.",""]
 return "\n".join(lines)

def run(out):
 assert BUDGET["scope"]==30 and len(SOURCES)==30 and set(Counter(x[1] for x in SOURCES).values())=={5} and not BUDGET["expand"]
 assert all(len(c[7])>=BUDGET["r_loop_n"] for c in CLAIMS)
 out=Path(out);(out/"compiled").mkdir(parents=True,exist_ok=True);(out/"reports").mkdir(parents=True,exist_ok=True)
 (out/"compiled/CURRENT_STATE.md").write_text(render(),encoding="utf-8")
 report={"schema_version":"1.0","phase":"P0","scope":30,"budget":BUDGET,"tests":{"T1":check_state_recovery(),"T2":check_stale(),"T3":check_sources(),"T4":check_claim_ceiling(),"T5":check_canonical_conflict()}}
 assert inspect_ceiling([{"id":"MUTANT","statement":"silent upgrade","status":"VERIFIED","source_ids":[]}])[0]["error"]=="UNSOURCED_VERIFIED"
 report["passed"]=all(x["passed"] for x in report["tests"].values())
 report["claim_ceiling"]="P0 fixture and sandbox validation only; not real-host longitudinal proof, not a production second brain, and not authority over WLS current state."
 (out/"reports/P0_ACCEPTANCE.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 return report

def main():
 p=argparse.ArgumentParser();p.add_argument("--output-root",default=str(Path(__file__).resolve().parent/"generated"));p.add_argument("--query");p.add_argument("--self-test",action="store_true");a=p.parse_args()
 if a.query:print(json.dumps(answer(a.query),ensure_ascii=False,indent=2));return 0
 r=run(a.output_root);print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r["passed"] else 1
if __name__=="__main__":raise SystemExit(main())
