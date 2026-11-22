# ordnig – architecture (arc42)

| # | Chapter |
| --- | --- |
| 1 | [Introduction and goals](01-introduction-and-goals.md) |
| 2 | [Constraints](02-constraints.md) |
| 3 | [Context and scope](03-context-and-scope.md) |
| 4 | [Solution strategy](04-solution-strategy.md) |
| 5 | [Building blocks](05-building-blocks.md) |
| 6 | [Runtime view](06-runtime-view.md) |
| 7 | [Deployment](07-deployment.md) |
| 8 | [Crosscutting concepts](08-concepts.md) |
| 9 | [Architecture decisions](09-decisions.md) |
| 10 | [Quality requirements](10-quality.md) |
| 11 | [Risks and technical debt](11-risks.md) |
| 12 | [Glossary](12-glossary.md) |

## Open questions for review

Marked in the chapters as **Qn**. Answer here or in the chapter; settled ones move
into the text or an ADR.

| Q | Topic | Chapter |
| --- | --- | --- |
| Q2 | Conversion log stores container paths | [8](08-concepts.md#q2) |
| Q3 | Lock: NAS mounted via NFS or SMB on the PC? | [7](07-deployment.md#q3) |
| Q4 | Where `/report` and `manifest.sha256` live | [7](07-deployment.md#q4) |
| Q5 | How runs are started and who is notified | [7](07-deployment.md#q5) |
| Q6 | GPU as the reason for the PC | [7](07-deployment.md#q6) |
| Q7 | `check`/`verify` also scan `00_Eingang` | [6](06-runtime-view.md#q7) |
| Q8 | Thresholds for `sort` | [8](08-concepts.md#q8) |
| Q9 | LLM fallback | [4](04-solution-strategy.md#q9) |
| Q10 | Tax year of annual insurance bills | [6](06-runtime-view.md#q10) |
| Q11 | HEIC and Outlook `.msg` in the inbox | [3](03-context-and-scope.md#q11) |
| Q12 | Portal downloads: part of ordnig? | [3](03-context-and-scope.md#q12) |
| Q13 | Backup and repair outside ordnig | [3](03-context-and-scope.md#q13) |
| Q14 | Tax copies reported as duplicates | [8](08-concepts.md#q14) |
| Q15 | ADRs to accept | [9](09-decisions.md#q15) |
