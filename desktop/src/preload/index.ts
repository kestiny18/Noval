import { contextBridge, ipcRenderer } from "electron";
import type { NovalDesktopApi, SidecarEvent } from "../shared/protocol.js";

const api: NovalDesktopApi = {
  chooseWorkspace:()=>ipcRenderer.invoke("noval:choose-workspace"), getWorkspace:()=>ipcRenderer.invoke("noval:get-workspace"),
  listProjects:()=>ipcRenderer.invoke("noval:list-projects"),projectSessions:path=>ipcRenderer.invoke("noval:project-sessions",path),activateProject:path=>ipcRenderer.invoke("noval:activate-project",path),removeProject:path=>ipcRenderer.invoke("noval:remove-project",path),revealProject:path=>ipcRenderer.invoke("noval:reveal-project",path),
  listSessions:()=>ipcRenderer.invoke("noval:list-sessions"), createSession:options=>ipcRenderer.invoke("noval:create-session",options),
  resumeSession:id=>ipcRenderer.invoke("noval:resume-session",id), renameSession:(id,title)=>ipcRenderer.invoke("noval:rename-session",id,title),
  transcript:(id,after)=>ipcRenderer.invoke("noval:transcript",id,after), replayEvents:(id,after)=>ipcRenderer.invoke("noval:events",id,after), startTurn:(id,text)=>ipcRenderer.invoke("noval:start-turn",id,text),
  transcriptHistory:(id,before)=>ipcRenderer.invoke("noval:transcript-history",id,before),
  copyText:text=>ipcRenderer.invoke("noval:copy-text",text),
  cancelTurn:id=>ipcRenderer.invoke("noval:cancel-turn",id), setPermissionMode:(id,mode)=>ipcRenderer.invoke("noval:permission-mode",id,mode),
  revokeTool:(id,tool)=>ipcRenderer.invoke("noval:permission-revoke",id,tool),resetPermissions:id=>ipcRenderer.invoke("noval:permission-reset",id),
  resolvePermission:(id,decision)=>ipcRenderer.invoke("noval:permission-resolve",id,decision),
  onEvent:listener=>{const handler=(_event:Electron.IpcRendererEvent,value:SidecarEvent)=>listener(value);ipcRenderer.on("noval:event",handler);return()=>ipcRenderer.removeListener("noval:event",handler);},
  appInfo:()=>ipcRenderer.invoke("noval:app-info"),
  getAppearance:()=>ipcRenderer.invoke("noval:get-appearance"),
  saveAppearance:value=>ipcRenderer.invoke("noval:save-appearance",value),
  getProviderProfile:()=>ipcRenderer.invoke("noval:get-provider-profile"),
  saveProviderProfile:profile=>ipcRenderer.invoke("noval:save-provider-profile",profile),
};
contextBridge.exposeInMainWorld("noval",Object.freeze(api));
