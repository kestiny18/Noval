import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, shell } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import { mkdir, writeFile as writeTextFile } from "node:fs/promises";
import { AppearancePreferences, Preferences, ProviderProfile } from "./preferences.js";
import { sendToRenderer } from "./renderer-events.js";
import { SidecarSupervisor } from "./sidecar.js";
import { PROTOCOL_VERSION } from "../shared/protocol.js";

const dirname = path.dirname(fileURLToPath(import.meta.url));
const sidecar = new SidecarSupervisor();
let mainWindow: BrowserWindow | null = null;
let workspace: string | null = null;
let preferences:Preferences;
let recovering=false;

interface CoreProject {workdir:string;available:boolean}
interface RuntimeConfiguration {provider:"openai-compatible"|"anthropic";model:string;judge_model:string;base_url:string;api_key_configured:boolean}
async function projectPaths():Promise<string[]>{const result=await sidecar.request<{projects:CoreProject[]}>("workspace.list",{});return (await preferences.synchronizeWorkspaces(result.projects.filter(item=>item.available).map(item=>item.workdir))).filter(value=>existsSync(value))}
async function projectList(){return (await projectPaths()).map(value=>({path:value,name:path.basename(value),active:value===workspace}))}
async function requireProject(value:string):Promise<string>{const match=(await projectPaths()).find(item=>item===value);if(!match)throw new Error("The project is not registered in Noval Desktop.");return match}
async function effectiveProfile():Promise<ProviderProfile>{const value=await sidecar.request<RuntimeConfiguration>("runtime.configuration",{});return {provider:value.provider,model:value.model,judgeModel:value.judge_model,baseUrl:value.base_url,hasApiKey:value.api_key_configured}}

function publishHostState(state:"connected"|"recovering"|"disconnected",detail?:string):void{
  sendToRenderer(mainWindow,"noval:event",{protocol_version:PROTOCOL_VERSION,kind:"event",event_id:`host-${Date.now()}`,event:"host.connection",payload:{state,detail}});
}

async function startRuntime():Promise<void>{
  const profile=preferences.profile(),apiKey=preferences.apiKey();
  if(!profile){await sidecar.start(process.env.NOVAL_SETTINGS_PATH);return}
  const settingsPath=path.join(app.getPath("userData"),"runtime-settings.json");
  await mkdir(path.dirname(settingsPath),{recursive:true});
  await writeTextFile(settingsPath,JSON.stringify({provider:profile.provider,model:profile.model,judge_model:profile.judgeModel,base_url:profile.baseUrl||"https://api.openai.com/v1",anthropic_base_url:profile.provider==="anthropic"?profile.baseUrl:"",api_key_env:"NOVAL_DESKTOP_API_KEY"},null,2),{encoding:"utf8",mode:0o600});
  await sidecar.start(settingsPath,apiKey?{NOVAL_DESKTOP_API_KEY:apiKey}:{});
}

async function recoverRuntime():Promise<void>{
  if(recovering)return;recovering=true;publishHostState("recovering");
  for(let attempt=1;attempt<=3;attempt++){
    await new Promise(resolve=>setTimeout(resolve,Math.min(500*2**(attempt-1),2000)));
    try{await startRuntime();if(workspace)await sidecar.request("workspace.select",{workdir:workspace});publishHostState("connected");recovering=false;return}catch{await sidecar.stop()}
  }
  recovering=false;publishHostState("disconnected","Automatic recovery failed. Restart Noval to try again.");
}

async function createWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
    width: 1280, height: 820, minWidth: 920, minHeight: 640, show: false,
    backgroundColor: "#10110f", titleBarStyle: "hiddenInset",
    autoHideMenuBar:true,
    webPreferences: { preload: path.join(dirname,"../preload/index.cjs"), contextIsolation:true, nodeIntegration:false, sandbox:true },
  });
  mainWindow.webContents.setWindowOpenHandler(() => ({action:"deny"}));
  mainWindow.webContents.on("will-navigate", event => event.preventDefault());
  mainWindow.webContents.on("will-attach-webview", event => event.preventDefault());
  mainWindow.once("closed",()=>{mainWindow=null});
  const devUrl = process.env.NOVAL_DEV_SERVER_URL;
  if (devUrl) await mainWindow.loadURL(devUrl); else await mainWindow.loadFile(path.join(dirname,"../renderer/index.html"));
  mainWindow.once("ready-to-show",async()=>{
    mainWindow?.show();
    const screenshotPath=process.env.NOVAL_SCREENSHOT_PATH;
    if(screenshotPath&&mainWindow){
      await new Promise(resolve=>setTimeout(resolve,500));
      const image=await mainWindow.webContents.capturePage();
      await writeFile(screenshotPath,image.toPNG());
      app.quit();
    }
  });
}

function registerIpc(): void {
  ipcMain.handle("noval:choose-workspace", async () => {
    const choice = await dialog.showOpenDialog(mainWindow!, {properties:["openDirectory"],title:"Choose a Noval workspace"});
    if (choice.canceled || !choice.filePaths[0]) return null;
    workspace = choice.filePaths[0]; await preferences.setWorkspace(workspace);await sidecar.request("workspace.select",{workdir:workspace}); return workspace;
  });
  ipcMain.handle("noval:get-workspace",()=>workspace);
  ipcMain.handle("noval:list-projects",()=>projectList());
  ipcMain.handle("noval:project-sessions",async(_e,value:string)=>((await sidecar.request<{sessions:unknown[]}>("workspace.sessions",{workdir:await requireProject(value)})).sessions));
  ipcMain.handle("noval:activate-project",async(_e,value:string)=>{workspace=await requireProject(value);await preferences.setWorkspace(workspace);await sidecar.request("workspace.select",{workdir:workspace});return workspace});
  ipcMain.handle("noval:remove-project",async(_e,value:string)=>{await requireProject(value);await preferences.removeWorkspace(value);const remaining=await projectPaths();workspace=remaining[0]??null;if(workspace){await preferences.setWorkspace(workspace);await sidecar.request("workspace.select",{workdir:workspace})}return projectList()});
  ipcMain.handle("noval:reveal-project",async(_e,value:string)=>{const error=await shell.openPath(await requireProject(value));if(error)throw new Error("The project could not be opened in File Explorer.")});
  ipcMain.handle("noval:list-sessions",async()=>((await sidecar.request<{sessions:unknown[]}>("session.list",{})).sessions));
  ipcMain.handle("noval:create-session",(_e,options={})=>sidecar.request("session.create",{options}));
  ipcMain.handle("noval:resume-session",(_e,id:string)=>sidecar.request("session.resume",{session_id:id}));
  ipcMain.handle("noval:rename-session",(_e,id:string,title:string)=>sidecar.request("session.rename",{session_id:id,title}));
  ipcMain.handle("noval:transcript",(_e,id:string,after=0)=>sidecar.request("session.transcript",{session_id:id,after_sequence:after,limit:100}));
  ipcMain.handle("noval:transcript-history",(_e,id:string,before?:number)=>sidecar.request("session.transcript_history",{session_id:id,before_sequence:before,limit:24}));
  ipcMain.handle("noval:copy-text",(_e,text:string)=>{if(typeof text!=="string")throw new Error("Only text can be copied.");clipboard.writeText(text)});
  ipcMain.handle("noval:events",(_e,id:string,after=0)=>sidecar.request("session.events",{session_id:id,after_sequence:after,limit:100}));
  ipcMain.handle("noval:start-turn",(_e,id:string,text:string)=>sidecar.request("turn.start",{session_id:id,text},600000));
  ipcMain.handle("noval:cancel-turn",async(_e,id:string)=>(await sidecar.request<{cancelled:boolean}>("turn.cancel",{session_id:id})).cancelled);
  ipcMain.handle("noval:permission-mode",(_e,id:string,mode:string)=>sidecar.request("session.permission_mode",{session_id:id,mode}));
  ipcMain.handle("noval:permission-revoke",(_e,id:string,toolName:string)=>sidecar.request("session.revoke_tool",{session_id:id,tool_name:toolName}));
  ipcMain.handle("noval:permission-reset",(_e,id:string)=>sidecar.request("session.reset_permissions",{session_id:id}));
  ipcMain.handle("noval:permission-resolve",async(_e,id:string,decision:string)=>{await sidecar.request("permission.resolve",{permission_request_id:id,decision});});
  ipcMain.handle("noval:app-info",()=>({desktopVersion:app.getVersion(),coreVersion:sidecar.getCoreVersion(),protocolVersion:PROTOCOL_VERSION}));
  ipcMain.handle("noval:get-appearance",()=>preferences.appearance());
  ipcMain.handle("noval:save-appearance",async(_e,value:AppearancePreferences)=>{
    if(!value||!["system","light","dark"].includes(value.theme)||!["comfortable","compact"].includes(value.density))throw new Error("Appearance settings are invalid.");
    await preferences.setAppearance(value);return preferences.appearance();
  });
  ipcMain.handle("noval:get-provider-profile",()=>effectiveProfile());
  ipcMain.handle("noval:save-provider-profile",async(_e,value:Omit<ProviderProfile,"hasApiKey">&{apiKey?:string})=>{
    if(!value||!['openai-compatible','anthropic'].includes(value.provider)||!value.model?.trim()||!value.judgeModel?.trim())throw new Error("Provider and model values are required.");
    await preferences.setProfile({provider:value.provider,model:value.model.trim(),judgeModel:value.judgeModel.trim(),baseUrl:String(value.baseUrl??"").trim()},value.apiKey?.trim());
    await sidecar.stop();await startRuntime();if(workspace)await sidecar.request("workspace.select",{workdir:workspace});return effectiveProfile();
  });
  ipcMain.handle("noval:open-external",async(_e,url:string)=>{if(/^https:\/\/(github\.com|docs\.noval\.)/i.test(url)) await shell.openExternal(url);});
}

app.whenReady().then(async()=>{
  Menu.setApplicationMenu(null);preferences=new Preferences();await preferences.load();workspace=preferences.workspace();registerIpc();sidecar.on("event",value=>sendToRenderer(mainWindow,"noval:event",value));sidecar.on("exit",()=>void recoverRuntime());await startRuntime();const projects=await projectPaths();if(!workspace||!projects.includes(workspace))workspace=projects[0]??null;if(workspace){await preferences.setWorkspace(workspace);await sidecar.request("workspace.select",{workdir:workspace})}await createWindow();publishHostState("connected");
});
app.on("window-all-closed",()=>app.quit());
app.on("before-quit",()=>{void sidecar.stop();});
