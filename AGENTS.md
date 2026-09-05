# TodoClock engineering rules

- Target Python 3.9.8, Pillow 9.0.0.dev0 and requests 2.26.0 on ARMv7 Kindle Oasis 1. Do not add native dependencies.
- Keep rendering pure: no network or device commands from renderers. Keep platform calls in device/lifecycle modules.
- Never edit rootfs, boot hooks, system service configuration, or stop powerd. No automatic SSH, deployment or firmware changes.
- Probe capabilities before taking input or changing device state. Journal original state before mutations; restoration must be idempotent and validate process identity.
- Never log HTTP headers, credentials, task bodies, account identifiers or raw network exceptions. Runtime data and secrets must remain untracked.
- Use bounded requests, atomic persistence and explicit stale/error states. Never fabricate successful synchronization.
- Use standard-library unittest and deterministic mocks. Run `python -m unittest discover -s tests -v`, `python tools/check.py`, and render all simulator pages before packaging.
- Document device-only checks as unverified until actual Kindle evidence is available. PC simulation is not hardware validation.
- Keep shell files LF, POSIX sh compatible. Package only an explicit allowlist of runtime files; never include state or local configuration.
