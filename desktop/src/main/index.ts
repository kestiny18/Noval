import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { writeFile } from "node:fs/promises";
import { mkdir, writeFile as writeTextFile } from "node:fs/promises";
import { Preferences, ProviderProfile } from "./preferences.js";
import { SidecarSupervisor } from "./sidecar.js";
import { PROTOCOL_VERSION } from "../shared/protocol.js";

const dirname = path.dirname(fileURLToPath(import.meta.url));
const sidecar = new SidecarSupervisor();
let mainWindow: BrowserWindow | null = null;
let workspace: string | null = null;
let preferences:Preferences;

async function startRuntime():Promise<void>{
  const profile=preferences.profile(),apiKey=preferences.apiKey();
  if(!profile){await sidecar.start();return}
  const settingsPath=path.join(app.getPath("userData"),"runtime-settings.json");
  await mkdir(path.dirname(settingsPath),{recursive:true});
  await writeTextFile(settingsPath,JSON.stringify({provider:profile.provider,model:profile.model,judge_model:profile.judgeModel,base_url:profile.baseUrl||"https://api.openai.com/v1",anthropic_base_url:profile.provider==="anthropic"?profile.baseUrl:"",api_key_env:"NOVAL_DESKTOP_API_KEY"},null,2),{encoding:"utf8",mode:0o600});
  await sidecar.start(settingsPath,apiKey?{NOVAL_DESKTOP_API_KEY:apiKey}:{});
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
  ipcMain.handle("noval:start-turn",(_e,id:string,text:string)=>sidecar.request("turn.start",{session_id:id,text},600000));
  ipcMain.handle("noval:cancel-turn",async(_e,id:string)=>(await sidecar.request<{cancelled:boolean}>("turn.cancel",{session_id:id})).cancelled);
  ipcMain.handle("noval:permission-mode",(_e,id:string,mode:string)=>sidecar.request("session.permission_mode",{session_id:id,mode}));
  ipcMain.handle("noval:permission-resolve",async(_e,id:string,decision:string)=>{await sidecar.request("permission.resolve",{permission_request_id:id,decision});});
  ipcMain.handle("noval:app-info",()=>({desktopVersion:app.getVersion(),coreVersion:sidecar.getCoreVersion(),protocolVersion:PROTOCOL_VERSION}));
  ipcMain.handle("noval:get-provider-profile",()=>preferences.profile());
  ipcMain.handle("noval:save-provider-profile",async(_e,value:Omit<ProviderProfile,"hasApiKey">&{apiKey?:string})=>{
    if(!value||!['openai-compatible','anthropic'].includes(value.provider)||!value.model?.trim()||!value.judgeModel?.trim())throw new Error("Provider and model values are required.");
    await preferences.setProfile({provider:value.provider,model:value.model.trim(),judgeModel:value.judgeModel.trim(),baseUrl:String(value.baseUrl??"").trim()},value.apiKey?.trim());
    await sidecar.stop();await startRuntime();if(workspace)await sidecar.request("workspace.select",{workdir:workspace});return preferences.profile();
  });
  ipcMain.handle("noval:open-external",async(_e,url:string)=>{if(/^https:\/\/(github\.com|docs\.noval\.)/i.test(url)) await shell.openExternal(url);});
}

app.whenReady().then(async()=>{
  preferences=new Preferences();await preferences.load();workspace=preferences.workspace();registerIpc(); await startRuntime();if(workspace){try{await sidecar.request("workspace.select",{workdir:workspace})}catch{workspace=null}}sidecar.on("event",value=>mainWindow?.webContents.send("noval:event",value)); await createWindow();
});
app.on("window-all-closed",()=>app.quit());
app.on("before-quit",()=>{void sidecar.stop();});
