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
system Python. Desktop preferences use Electron's platform `userData` path;
Core data remains under `~/.noval`.
