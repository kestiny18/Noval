import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { writeFile } from "node:fs/promises";
import { mkdir, writeFile as writeTextFile } from "node:fs/promises";
import { Preferences, ProviderProfile } from "./preferences.js";
import { SidecarSupervisor } from "./sidecar.js";
import { PROTOCOL_VERSION } from "../shared/protocol.js";
import { DiagnosticBuffer } from "./diagnostics.js";

const dirname = path.dirname(fileURLToPath(import.meta.url));
const sidecar = new SidecarSupervisor();
let mainWindow: BrowserWindow | null = null;
let workspace: string | null = null;
let preferences:Preferences;
const diagnostics=new DiagnosticBuffer();
let recovering=false;

function publishHostState(state:"connected"|"recovering"|"disconnected",detail?:string):void{
  mainWindow?.webContents.send("noval:event",{protocol_version:PROTOCOL_VERSION,kind:"event",event_id:`host-${Date.now()}`,event:"host.connection",payload:{state,detail}});
}

async function startRuntime():Promise<void>{
  const profile=preferences.profile(),apiKey=preferences.apiKey();
  if(!profile){await sidecar.start();return}
  const settingsPath=path.join(app.getPath("userData"),"runtime-settings.json");
  await mkdir(path.dirname(settingsPath),{recursive:true});
  await writeTextFile(settingsPath,JSON.stringify({provider:profile.provider,model:profile.model,judge_model:profile.judgeModel,base_url:profile.baseUrl||"https://api.openai.com/v1",anthropic_base_url:profile.provider==="anthropic"?profile.baseUrl:"",api_key_env:"NOVAL_DESKTOP_API_KEY"},null,2),{encoding:"utf8",mode:0o600});
  await sidecar.start(settingsPath,apiKey?{NOVAL_DESKTOP_API_KEY:apiKey}:{});
}

async function recoverRuntime():Promise<void>{
  if(recovering)return;recovering=true;publishHostState("recovering");diagnostics.add("host","Sidecar recovery started.");
  for(let attempt=1;attempt<=3;attempt++){
    await new Promise(resolve=>setTimeout(resolve,Math.min(500*2**(attempt-1),2000)));
    try{await startRuntime();if(workspace)await sidecar.request("workspace.select",{workdir:workspace});diagnostics.add("host",`Sidecar recovered on attempt ${attempt}.`);publishHostState("connected");recovering=false;return}catch(error){diagnostics.add("host",`Recovery attempt ${attempt} failed: ${error instanceof Error?error.message:"unknown error"}`);await sidecar.stop()}
  }
  recovering=false;publishHostState("disconnected","Automatic recovery failed. Restart Noval to try again.");
}

async function createWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
    width: 1280, height: 820, minWidth: 920, minHeight: 640, show: false,
    backgroundColor: "#10110f", titleBarStyle: "hiddenInset",
    webPreferences: { preload: path.join(dirname,"../preload/index.cjs"), contextIsolation:true, nodeIntegration:false, sandbox:true },
  });
  mainWindow.webContents.setWindowOpenHandler(() => ({action:"deny"}));
  mainWindow.webContents.on("will-navigate", event => event.preventDefault());
  mainWindow.webContents.on("will-attach-webview", event => event.preventDefault());
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
  ipcMain.handle("noval:list-sessions",async()=>((await sidecar.request<{sessions:unknown[]}>("session.list",{})).sessions));
  ipcMain.handle("noval:create-session",(_e,options={})=>sidecar.request("session.create",{options}));
  ipcMain.handle("noval:resume-session",(_e,id:string)=>sidecar.request("session.resume",{session_id:id}));
  ipcMain.handle("noval:rename-session",(_e,id:string,title:string)=>sidecar.request("session.rename",{session_id:id,title}));
  ipcMain.handle("noval:transcript",(_e,id:string,after=0)=>sidecar.request("session.transcript",{session_id:id,after_sequence:after,limit:100}));
  ipcMain.handle("noval:events",(_e,id:string,after=0)=>sidecar.request("session.events",{session_id:id,after_sequence:after,limit:100}));
  ipcMain.handle("noval:start-turn",(_e,id:string,text:string)=>sidecar.request("turn.start",{session_id:id,text},600000));
  ipcMain.handle("noval:cancel-turn",async(_e,id:string)=>(await sidecar.request<{cancelled:boolean}>("turn.cancel",{session_id:id})).cancelled);
  ipcMain.handle("noval:permission-mode",(_e,id:string,mode:string)=>sidecar.request("session.permission_mode",{session_id:id,mode}));
  ipcMain.handle("noval:permission-revoke",(_e,id:string,toolName:string)=>sidecar.request("session.revoke_tool",{session_id:id,tool_name:toolName}));
  ipcMain.handle("noval:permission-reset",(_e,id:string)=>sidecar.request("session.reset_permissions",{session_id:id}));
  ipcMain.handle("noval:permission-resolve",async(_e,id:string,decision:string)=>{await sidecar.request("permission.resolve",{permission_request_id:id,decision});});
  ipcMain.handle("noval:app-info",()=>({desktopVersion:app.getVersion(),coreVersion:sidecar.getCoreVersion(),protocolVersion:PROTOCOL_VERSION}));
  ipcMain.handle("noval:get-provider-profile",()=>preferences.profile());
  ipcMain.handle("noval:save-provider-profile",async(_e,value:Omit<ProviderProfile,"hasApiKey">&{apiKey?:string})=>{
    if(!value||!['openai-compatible','anthropic'].includes(value.provider)||!value.model?.trim()||!value.judgeModel?.trim())throw new Error("Provider and model values are required.");
    await preferences.setProfile({provider:value.provider,model:value.model.trim(),judgeModel:value.judgeModel.trim(),baseUrl:String(value.baseUrl??"").trim()},value.apiKey?.trim());
    await sidecar.stop();await startRuntime();if(workspace)await sidecar.request("workspace.select",{workdir:workspace});return preferences.profile();
  });
  ipcMain.handle("noval:open-external",async(_e,url:string)=>{if(/^https:\/\/(github\.com|docs\.noval\.)/i.test(url)) await shell.openExternal(url);});
  ipcMain.handle("noval:export-diagnostics",async()=>{const target=await dialog.showSaveDialog(mainWindow!,{title:"Export Noval diagnostics",defaultPath:`noval-diagnostics-${new Date().toISOString().slice(0,10)}.json`,filters:[{name:"JSON",extensions:["json"]}]});if(target.canceled||!target.filePath)return null;const payload={schema_version:1,exported_at:new Date().toISOString(),desktop_version:app.getVersion(),core_version:sidecar.getCoreVersion(),protocol_version:PROTOCOL_VERSION,platform:process.platform,logs:diagnostics.snapshot()};await writeTextFile(target.filePath,JSON.stringify(payload,null,2),"utf8");return target.filePath;});
}

app.whenReady().then(async()=>{
  preferences=new Preferences();await preferences.load();workspace=preferences.workspace();registerIpc();sidecar.on("event",value=>mainWindow?.webContents.send("noval:event",value));sidecar.on("diagnostic",value=>diagnostics.add("sidecar",String(value)));sidecar.on("protocol-error",()=>diagnostics.add("sidecar","Invalid protocol envelope received."));sidecar.on("exit",()=>void recoverRuntime());await startRuntime();if(workspace){try{await sidecar.request("workspace.select",{workdir:workspace})}catch{workspace=null}}await createWindow();publishHostState("connected");
});
app.on("window-all-closed",()=>app.quit());
app.on("before-quit",()=>{void sidecar.stop();});
