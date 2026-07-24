# Noval Desktop preview

The Desktop is an isolated first-party host. It does not replace or modify the
Noval Python execution kernel.

```powershell
cd desktop
npm install
npm test
npm run typecheck
npm run dev
```

Build the unsigned Windows x64 preview:

```powershell
npm run package:win
```

Production packages contain an embedded Python sidecar and never fall back to
system Python. Desktop preferences use Electron's platform `userData` path for
workspace and appearance state only. Provider Profiles, Connections,
Configured Models, and credentials remain owned by Core under
`~/.noval/settings.json`.

The Models page treats credentials as write-only and never stores them in
Electron preferences. A credential entered there is written by Core as
plaintext in the user-local settings file; prefer the configured environment
variable when practical.
