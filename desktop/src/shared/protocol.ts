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
export interface PermissionState { mode: "ask" | "full_access"; approved_tools: string[] }
export interface ProviderProfile { provider:"openai-compatible"|"anthropic";model:string;judgeModel:string;baseUrl:string;hasApiKey:boolean }
export interface TranscriptEntry { sequence: number; role: "user" | "assistant" | "tool"; text: string; timestamp: string | null; tool_calls: Array<{call_id:string;name:string;argument_keys:string[]}>; tool_results: Array<{call_id:string;content:string;is_error:boolean}> }

export interface NovalDesktopApi {
  chooseWorkspace(): Promise<string | null>;
  getWorkspace(): Promise<string | null>;
  listSessions(): Promise<SessionInfo[]>;
  createSession(options?: Record<string, unknown>): Promise<{session: SessionInfo; permissions: PermissionState}>;
  resumeSession(sessionId: string): Promise<{session: SessionInfo; permissions: PermissionState}>;
  renameSession(sessionId: string, title: string): Promise<SessionInfo>;
  transcript(sessionId: string, afterSequence?: number): Promise<{entries: TranscriptEntry[]; next_sequence:number; has_more:boolean}>;
  startTurn(sessionId: string, text: string): Promise<Record<string, unknown>>;
  cancelTurn(sessionId: string): Promise<boolean>;
  setPermissionMode(sessionId: string, mode: "ask" | "full_access"): Promise<PermissionState>;
  resolvePermission(permissionRequestId: string, decision: "allow_once" | "allow_session" | "deny"): Promise<void>;
  onEvent(listener: (event: SidecarEvent) => void): () => void;
  appInfo(): Promise<{desktopVersion:string;coreVersion:string;protocolVersion:number}>;
  getProviderProfile():Promise<ProviderProfile|null>;
  saveProviderProfile(profile:Omit<ProviderProfile,"hasApiKey">&{apiKey?:string}):Promise<ProviderProfile>;
}
