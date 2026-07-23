import { z } from "zod";

export const PROTOCOL_VERSION = 1 as const;
export const ErrorSchema = z.object({ code: z.string(), safe_message: z.string(), retryable: z.boolean() });
export const ResponseSchema = z.object({
  protocol_version: z.literal(PROTOCOL_VERSION), kind: z.literal("response"), request_id: z.string().nullable(), ok: z.boolean(),
  result: z.unknown().optional(), error: ErrorSchema.optional(),
});
export const EventSchema = z.object({
  protocol_version: z.literal(PROTOCOL_VERSION), kind: z.literal("event"), event_id: z.string(), event: z.string(), payload: z.record(z.unknown()),
});
export const EnvelopeSchema = z.union([ResponseSchema, EventSchema]);
export type SidecarEvent = z.infer<typeof EventSchema>;

export interface SessionInfo {
  session_id: string; workdir: string; provider: string; model: string; is_open: boolean; title: string | null; message_count: number;
}
export interface ProjectInfo { path:string;name:string;active:boolean }
export interface PermissionState { mode: "ask" | "full_access"; approved_tools: string[] }
export interface ProviderProfile { provider:"openai-compatible"|"anthropic";model:string;judgeModel:string;baseUrl:string;hasApiKey:boolean }
export interface TranscriptEntry { sequence: number; role: "user" | "assistant" | "tool"; text: string; timestamp: string | null; tool_calls: Array<{call_id:string;name:string;argument_keys:string[]}>; tool_results: Array<{call_id:string;content:string;is_error:boolean}> }
export interface CompletionCriterion { criterion_id:string;status:"passed"|"failed"|"missing"|"stale"|"unknown";source:string|null;observed_at:string|null }
export interface CompletionReport { goal_id:string;status:"completed"|"incomplete"|"uncertain";evaluated_at:string;criteria:CompletionCriterion[];semantic?:{status:string;summary?:string}|null }
export interface TurnResult { status:string;completion:CompletionReport|null;error?:{code?:string;safe_message?:string}|null }
export interface RuntimeEvent { sequence:number;type:string;payload:Record<string,unknown>;session_id:string;turn_id:string|null;timestamp:string }
export interface EventPage { events:RuntimeEvent[];next_sequence:number;gap_detected:boolean;has_more:boolean }
export type RuntimeConnectionState = "connected"|"recovering"|"disconnected";

export interface NovalDesktopApi {
  chooseWorkspace(): Promise<string | null>;
  getWorkspace(): Promise<string | null>;
  listProjects():Promise<ProjectInfo[]>;
  projectSessions(path:string):Promise<SessionInfo[]>;
  activateProject(path:string):Promise<string>;
  removeProject(path:string):Promise<ProjectInfo[]>;
  revealProject(path:string):Promise<void>;
  listSessions(): Promise<SessionInfo[]>;
  createSession(options?: Record<string, unknown>): Promise<{session: SessionInfo; permissions: PermissionState}>;
  resumeSession(sessionId: string): Promise<{session: SessionInfo; permissions: PermissionState}>;
  renameSession(sessionId: string, title: string): Promise<SessionInfo>;
  transcript(sessionId: string, afterSequence?: number): Promise<{entries: TranscriptEntry[]; next_sequence:number; has_more:boolean}>;
  copyText(text:string):Promise<void>;
  replayEvents(sessionId:string,afterSequence?:number):Promise<EventPage>;
  startTurn(sessionId: string, text: string): Promise<TurnResult>;
  cancelTurn(sessionId: string): Promise<boolean>;
  setPermissionMode(sessionId: string, mode: "ask" | "full_access"): Promise<PermissionState>;
  revokeTool(sessionId:string,toolName:string):Promise<PermissionState>;
  resetPermissions(sessionId:string):Promise<PermissionState>;
  resolvePermission(permissionRequestId: string, decision: "allow_once" | "allow_session" | "deny"): Promise<void>;
  onEvent(listener: (event: SidecarEvent) => void): () => void;
  appInfo(): Promise<{desktopVersion:string;coreVersion:string;protocolVersion:number}>;
  getProviderProfile():Promise<ProviderProfile|null>;
  saveProviderProfile(profile:Omit<ProviderProfile,"hasApiKey">&{apiKey?:string}):Promise<ProviderProfile>;
}
