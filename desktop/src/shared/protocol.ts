import { z } from "zod";

export const PROTOCOL_VERSION = 2 as const;
export const API_SCHEMA_VERSION = 2 as const;
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
  schema_version: 2; session_id: string; workdir: string; persistence:"default"|"memory"|"persistent";
  selected_model_id: string; selected_judge_model_id: string;
  active_model_id: string|null; active_judge_model_id: string|null;
  is_open: boolean; title: string | null; message_count: number; last_active:string|null;
}
export interface ProjectInfo { path:string;name:string;active:boolean }
export interface PermissionState { mode: "ask" | "full_access"; approved_tools: string[] }
export interface AppearancePreferences {theme:"system"|"light"|"dark";density:"comfortable"|"compact"}
export interface AppInfo {desktopVersion:string;coreVersion:string;protocolVersion:number}
export interface ProviderModelInfo {id:string;label:string}
export interface ProviderProfileInfo {
  schema_version:2;id:string;label:string;kind:"builtin"|"custom";models:ProviderModelInfo[];
  default_model:string|null;adapter:string|null;requires_base_url:boolean;
}
export interface ConnectionInfo {
  id:string;revision:number;label:string;profile_id:string;adapter:string;base_url:string;
  api_key_env:string;api_key_configured:boolean;credential_available:boolean;
}
export interface ConfiguredModelInfo {id:string;label:string;connection_id:string;model:string}
export interface ModelConfigurationInfo {
  schema_version:2;revision:number;connections:ConnectionInfo[];
  configured:ConfiguredModelInfo[];default_model_id:string;
}
export interface ConnectionUpsert {
  schema_version:2;expected_configuration_revision:number;connection_id?:string|null;
  expected_connection_revision?:number|null;label:string;profile_id:string;
  base_url?:string|null;api_key_env?:string|null;api_key?:string;clear_api_key?:boolean;
}
export interface ConfiguredModelUpsert {
  schema_version:2;expected_configuration_revision:number;configured_model_id?:string|null;
  label:string;connection_id:string;model:string;
}
export interface TranscriptEntry { sequence: number; role: "user" | "assistant" | "tool"; text: string; timestamp: string | null; tool_calls: Array<{call_id:string;name:string;argument_keys:string[]}>; tool_results: Array<{call_id:string;content:string;is_error:boolean}> }
export interface TranscriptHistoryPage {entries:TranscriptEntry[];previous_sequence:number|null;has_more:boolean}
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
  selectSessionModels(sessionId:string,modelId:string,judgeModelId:string):Promise<SessionInfo>;
  transcript(sessionId: string, afterSequence?: number): Promise<{entries: TranscriptEntry[]; next_sequence:number; has_more:boolean}>;
  transcriptHistory(sessionId:string,beforeSequence?:number):Promise<TranscriptHistoryPage>;
  copyText(text:string):Promise<void>;
  replayEvents(sessionId:string,afterSequence?:number):Promise<EventPage>;
  startTurn(sessionId: string, text: string): Promise<TurnResult>;
  cancelTurn(sessionId: string): Promise<boolean>;
  setPermissionMode(sessionId: string, mode: "ask" | "full_access"): Promise<PermissionState>;
  revokeTool(sessionId:string,toolName:string):Promise<PermissionState>;
  resetPermissions(sessionId:string):Promise<PermissionState>;
  resolvePermission(permissionRequestId: string, decision: "allow_once" | "allow_session" | "deny"): Promise<void>;
  onEvent(listener: (event: SidecarEvent) => void): () => void;
  appInfo(): Promise<AppInfo>;
  getAppearance():Promise<AppearancePreferences>;
  saveAppearance(value:AppearancePreferences):Promise<AppearancePreferences>;
  listProviderProfiles():Promise<ProviderProfileInfo[]>;
  getModelConfiguration():Promise<ModelConfigurationInfo>;
  upsertConnection(value:ConnectionUpsert):Promise<ModelConfigurationInfo>;
  deleteConnection(connectionId:string,expectedRevision:number):Promise<ModelConfigurationInfo>;
  upsertConfiguredModel(value:ConfiguredModelUpsert):Promise<ModelConfigurationInfo>;
  deleteConfiguredModel(configuredModelId:string,expectedRevision:number):Promise<ModelConfigurationInfo>;
  setDefaultModel(configuredModelId:string,expectedRevision:number):Promise<ModelConfigurationInfo>;
}
