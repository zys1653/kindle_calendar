# TodoClock engineering rules

- Target Python 3.9.8, Pillow 9.0.0.dev0 and requests 2.26.0 on ARMv7 Kindle Oasis 1. Do not add native dependencies.
- Keep rendering pure: no network or device commands from renderers. Keep platform calls in device/lifecycle modules.
- Never edit rootfs, boot hooks, system service configuration, or stop powerd. No automatic SSH, deployment or firmware changes.
- Probe capabilities before taking input or changing device state. Journal original state before mutations; restoration must be idempotent and validate process identity.
- Never log HTTP headers, credentials, task bodies, account identifiers or raw network exceptions. Runtime data and secrets must remain untracked.
- Use bounded requests, atomic persistence and explicit stale/error states. Never fabricate successful synchronization.
- Use standard-library unittest and deterministic mocks. Run `python -m unittest discover -s tests -v` and `python tools/check.py` before packaging. Pure unit tests remain required.
- Preserve simulator and preview functionality, but do not run Tk smoke tests, simulator sessions, preview exports or visual acceptance checks after changes. Starting with 0.1.4, the user performs simulation and visual acceptance. Record these checks as not run; do not present older previews as the current release.
- Document device-only checks as unverified until actual Kindle evidence is available. PC simulation is not hardware validation.
- Keep shell files LF, POSIX sh compatible. Package only an explicit allowlist of runtime files; never include state or local configuration.
