# Task19 delivery matrix

| Area | Candidate state | Required proof before acceptance |
|---|---|---|
| Survival configuration | Implemented with explicit bounded defaults | focused tests + full local gate |
| Persistent goals / ET004 | Main-based candidate with task-spec execution and negative controls | ET004 verifier + full pytest |
| Outcome provenance | Current/reused/recovered/no-outcome labels implemented | focused provenance tests + regression |
| Goal counterfactual | Write-free cognitive comparison implemented | attribution tests + ET004 report |
| Provider Hub | CLI-only, candidate-only, secret/SSRF/concurrency hardening | focused security tests; no planner attachment |
| Packaging | Root-only build and clean-install verifier | exactly one Wheel + pip check + outside-repo smoke |
| Reliability | Non-idle 100-cycle workload with three runtime restarts | exact-head local Windows report |
| Task20 | Blocked | owner acceptance of Task19 |

No row in this matrix is a merge authorization. Candidate code remains `UNVERIFIED` until the exact-head Windows/local gate passes and the result is independently reviewed.
