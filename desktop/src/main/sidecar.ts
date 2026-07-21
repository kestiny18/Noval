import { ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { randomUUID } from "node:crypto";
import { EventEmitter } from "node:events";
import { app } from "electron";
import path from "node:path";
import { EnvelopeSchema, PROTOCOL_VERSION, SidecarEvent } from "../shared/protocol.js";

type Pending = { resolve: (value: unknown) => void; reject: (reason: Error) => void; timer: NodeJS.Timeout };

export class SidecarSupervisor extends EventEmitter {
  private child?: ChildProcessWithoutNullStreams;
  private pending = new Map<string, Pending>();
  private coreVersion = "unknown";

  async start(settingsPath?: string, extraEnv:Record<string,string>={}): Promise<void> {
    if (this.child) return;
    const dev = !app.isPackaged;
    const executable = dev ? (process.env.NOVAL_PYTHON ?? "py") : path.join(process.resourcesPath, "sidecar", "noval-sidecar", "noval-sidecar.exe");
    const args = dev ? ["-m", "desktop.sidecar.noval_sidecar"] : [];
    this.child = spawn(executable, args, { cwd: dev ? path.resolve(app.getAppPath(), "..") : process.resourcesPath, windowsHide: true, env: {...process.env,...extraEnv, PYTHONUNBUFFERED:"1"} });
    const lines = createInterface({input: this.child.stdout, crlfDelay: Infinity});
    lines.on("line", line => this.consume(line));
    this.child.stderr.on("data", data => this.emit("diagnostic", String(data).slice(0, 2000)));
    this.child.once("exit", () => { this.child = undefined; this.failPending("Sidecar exited."); this.emit("exit"); });
    const hello = await this.request<Record<string, unknown>>("system.hello", {});
    this.coreVersion = String(hello.core_version ?? "unknown");
    await this.request("runtime.start", {settings_path: settingsPath ?? null});
  }

  request<T=unknown>(method: string, params: Record<string, unknown>, timeoutMs = 30000): Promise<T> {
    if (!this.child) return Promise.reject(new Error("Sidecar is not running."));
    const requestId = randomUUID();
    const line = JSON.stringify({protocol_version:PROTOCOL_VERSION,kind:"request",request_id:requestId,method,params});
    return new Promise<T>((resolve,reject) => {
      const timer = setTimeout(() => { this.pending.delete(requestId); reject(new Error("Sidecar request timed out.")); }, timeoutMs);
      this.pending.set(requestId,{resolve:resolve as (value:unknown)=>void,reject,timer});
      this.child!.stdin.write(line + "\n");
    });
  }

  private consume(line: string): void {
    let parsed: ReturnType<typeof EnvelopeSchema.parse>;
    try { parsed = EnvelopeSchema.parse(JSON.parse(line)); } catch { this.emit("protocol-error"); return; }
    if (parsed.kind === "event") { this.emit("event", parsed as SidecarEvent); return; }
    if (!parsed.request_id) return;
    const pending = this.pending.get(parsed.request_id); if (!pending) return;
    clearTimeout(pending.timer); this.pending.delete(parsed.request_id);
    if (parsed.ok) pending.resolve(parsed.result); else pending.reject(new Error(parsed.error?.safe_message ?? "Sidecar request failed."));
  }

  private failPending(message: string): void { for (const [,p] of this.pending) { clearTimeout(p.timer); p.reject(new Error(message)); } this.pending.clear(); }
  getCoreVersion(): string { return this.coreVersion; }
  async stop(): Promise<void> { if (!this.child) return; this.child.stdin.end(); this.child.kill(); this.child=undefined; this.failPending("Sidecar stopped."); }
}
