## What this changes

## How it was checked

- [ ] `pytest` passes
- [ ] `tsc --noEmit` passes (if the app changed)
- [ ] Evals run, more than once (if a prompt, tool description, memory or voice changed)
- [ ] CHANGELOG.md updated

## Safety

- [ ] No path around the permission gate; new tools have truthful `risk`, `read_only` and `reads_untrusted`
- [ ] Nothing personal in tests, fixtures or screenshots
