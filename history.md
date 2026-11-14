# History: Learning Factory

Short entries with insights and fun facts about building this with Claude Code, for the POC presentation.

## 2026-11-14 · From an idea to a running factory in one evening

- **Brainstorm first, then build.** The lab platform was designed in conversation: Ilia described the KodeKloud experience he wanted, Claude looked at a live KodeKloud lab in Chrome and reverse-engineered it from the network traffic (per-session host, `/check_config`, `/heart_beat`, ttyd for terminals, code-server for VS Code labs).
- **A reviewer read the design before Ilia did.** A fresh agent tried to plan from the document alone and returned 8 questions; 7 were settled by the factory itself and recorded as revertible decisions; only one (the server size for VS Code weeks) went to Ilia.
- **The factory is the talk made real.** The roles and loop follow Ilia's "Software Factory" deck: a manager on a strong model, workers on cheaper ones, a hidden exam written before the code, a second opinion from another AI company, triggers (hourly, failed build, nightly), and guardrails ("never touch the owner's other services").
- **Memory lives in the repo, not in a session.** `docs/factory/STATE.md` plus claim comments on issues mean "continue the factory" works from any session, on the laptop or in the cloud.
- **Reuse:** the issue claim lock came from the e-sign project unchanged except one label; its 33 tests passed on the first run.
