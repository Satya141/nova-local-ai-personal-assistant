# Contributing to NOVA

Thanks for helping. NOVA can touch people's files and accounts, so a few rules come before features.

## Before you start

- Read [AGENTS.md](AGENTS.md): the layout, the commands, and the rules that must hold. The most important: the model never executes anything, permissions come from tool metadata, and safety checks live in code, not in prompts.
- Read [docs/architecture.md](docs/architecture.md) before changing how the pieces fit together.
- For a large change, open an issue first so we can agree on the approach.

## Setup

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
pnpm --dir apps/desktop tauri dev
```

## Checks before a pull request

```powershell
cd backend; .venv\Scripts\python -m pytest
pnpm --dir apps/desktop exec tsc --noEmit
```

If you changed a prompt, a tool description, the memory pipeline or anything in `nova/voice`, also run the evals against the real models (more than once; model output varies):

```powershell
cd backend; .venv\Scripts\python -m evals.run
```

## In your change

- Tests for every behaviour change. A new tool needs tests for its matching and refusal logic; anything touching the permission gate needs an agent-loop test.
- A truthful `risk`, `read_only` and `reads_untrusted` on every new tool (see "Adding a tool" in AGENTS.md).
- An entry in [CHANGELOG.md](CHANGELOG.md).
- Never test app control or voice on someone's real windows or room; use the sandboxed stand-ins and evals.

Security problems go through [SECURITY.md](SECURITY.md), not public issues.

By contributing, you agree that your contribution is licensed under the [MIT License](LICENSE).
