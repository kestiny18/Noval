import { contextBridge, ipcRenderer } from "electron";
import type { NovalDesktopApi, SidecarEvent } from "../shared/protocol.js";

const api: NovalDesktopApi = {
  chooseWorkspace:()=>ipcRenderer.invoke("noval:choose-workspace"), getWorkspace:()=>ipcRenderer.invoke("noval:get-workspace"),
  listSessions:()=>ipcRenderer.invoke("noval:list-sessions"), createSession:options=>ipcRenderer.invoke("noval:create-session",options),
  resumeSession:id=>ipcRenderer.invoke("noval:resume-session",id), renameSession:(id,title)=>ipcRenderer.invoke("noval:rename-session",id,title),
  transcript:(id,after)=>ipcRenderer.invoke("noval:transcript",id,after), startTurn:(id,text)=>ipcRenderer.invoke("noval:start-turn",id,text),
  cancelTurn:id=>ipcRenderer.invoke("noval:cancel-turn",id), setPermissionMode:(id,mode)=>ipcRenderer.invoke("noval:permission-mode",id,mode),
  resolvePermission:(id,decision)=>ipcRenderer.invoke("noval:permission-resolve",id,decision),
  onEvent:listener=>{const handler=(_event:Electron.IpcRendererEvent,value:SidecarEvent)=>listener(value);ipcRenderer.on("noval:event",handler);return()=>ipcRenderer.removeListener("noval:event",handler);},
  appInfo:()=>ipcRenderer.invoke("noval:app-info"),
  getProviderProfile:()=>ipcRenderer.invoke("noval:get-provider-profile"),
  saveProviderProfile:profile=>ipcRenderer.invoke("noval:save-provider-profile",profile),
};
contextBridge.exposeInMainWorld("noval",Object.freeze(api));
