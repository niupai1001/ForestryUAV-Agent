# Kernel selection record

Candidate: OpenHands SDK 1.47.0, installed in a clean Python 3.12 container.

The candidate is adopted only if all engineering checks pass, at least eight of
nine Qwen runs pass, its completed-run count is not lower than the current loop,
and it neither performs unauthorized actions nor reports nonexistent artifacts.
At most two targeted adaptation rounds are allowed.

This is a historical decision record. The probe implementation and candidate
dependency were removed after the gate failed; PydanticAI is the production
kernel.

## 2026-09-13 decision

**Decision: retain and refactor the existing model loop. Do not add OpenHands to
the production dependency set.**

The clean candidate container installed `openhands-sdk==1.47.0` on Python 3.12
without `openhands-tools` or `openhands-workspace`; `pip check` passed. Custom
tools, streamed tool arguments/results, failure observations, custom prompts, and
file-backed conversation events worked through public SDK interfaces.

The nine real `qwen3.5:4b` runs produced:

| Task | Passed | Result |
|---|---:|---|
| list mixed directory and read config | 3/3 | Passed |
| merge CSV files and create output | 3/3 | Passed; one run needed SDK corrective feedback after an empty model response |
| diagnose, copy, repair, run, and verify script | 1/3 | Failed the required output-path check twice; one failure exhausted all 32 iterations |
| **Total** | **7/9** | Below the required 8/9 threshold |

One targeted adaptation clarified that Python runs from the workspace root and
that the exact requested output path must be verified. The repair task then
passed 0/3; one run triggered stuck detection and another exhausted 32
iterations. This did not change the failing condition and is sufficient to stop
the candidate evaluation before the two-round maximum.

Raw JSON event traces remain under the ignored `evaluation/results/` directory.
The first failed trace is especially material: the model correctly found
`result.txt` at the workspace root instead of `mixed/result.txt`, yet still used
the finish action and reported the task complete. The SDK therefore did not meet
the repository's evidence-grounded completion gate. Since the absolute adoption
threshold failed, a comparative score cannot make the candidate eligible.
