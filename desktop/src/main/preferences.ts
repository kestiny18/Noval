import { app } from "electron";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

export interface AppearancePreferences {theme:"system"|"light"|"dark";density:"comfortable"|"compact"}
export type LanguagePreference="zh-CN"|"en";
export interface DesktopPreferencesSnapshot {appearance:AppearancePreferences;language:LanguagePreference;sidebarWidth:number}
interface Stored {
  workspace?:string;workspaces?:string[];hiddenWorkspaces?:string[];
  appearance?:Partial<AppearancePreferences>;language?:LanguagePreference;sidebarWidth?:number;
}

export const DEFAULT_SIDEBAR_WIDTH=278;
export const MIN_SIDEBAR_WIDTH=220;
export const MAX_SIDEBAR_WIDTH=480;

export class Preferences {
  private readonly file=path.join(app.getPath("userData"),"desktop-settings.json");
  private data:Stored={};
  async load():Promise<void>{
    let raw:Record<string,unknown>;
    try{
      raw=JSON.parse(await readFile(this.file,"utf8")) as Record<string,unknown>;
    }catch{this.data={};return}
    this.data={
      workspace:typeof raw.workspace==="string"?raw.workspace:undefined,
      workspaces:Array.isArray(raw.workspaces)?raw.workspaces.filter((value):value is string=>typeof value==="string"):undefined,
      hiddenWorkspaces:Array.isArray(raw.hiddenWorkspaces)?raw.hiddenWorkspaces.filter((value):value is string=>typeof value==="string"):undefined,
      appearance:raw.appearance&&typeof raw.appearance==="object"?raw.appearance as Partial<AppearancePreferences>:undefined,
      language:raw.language==="zh-CN"||raw.language==="en"?raw.language:undefined,
      sidebarWidth:typeof raw.sidebarWidth==="number"&&Number.isFinite(raw.sidebarWidth)?clampSidebarWidth(raw.sidebarWidth):undefined,
    };
    if("provider" in raw||"encryptedApiKey" in raw)await this.persist();
    if(this.data.workspace&&!this.data.workspaces)this.data.workspaces=[this.data.workspace];
  }
  workspace():string|null{return this.data.workspace??null}
  workspaces(discovered:string[]=[]):string[]{const hidden=new Set(this.data.hiddenWorkspaces??[]);return [...new Set([...(this.data.workspaces??(this.data.workspace?[this.data.workspace]:[])),...discovered])].filter(item=>!hidden.has(item))}
  async synchronizeWorkspaces(discovered:string[]):Promise<string[]>{const merged=this.workspaces(discovered);if(JSON.stringify(merged)!==JSON.stringify(this.data.workspaces??[])){this.data.workspaces=merged;await this.persist()}return merged}
  async setWorkspace(value:string):Promise<void>{this.data.workspace=value;this.data.workspaces=this.workspaces().includes(value)?this.workspaces():[...this.workspaces(),value];this.data.hiddenWorkspaces=(this.data.hiddenWorkspaces??[]).filter(item=>item!==value);await this.persist()}
  async removeWorkspace(value:string):Promise<void>{this.data.workspaces=this.workspaces().filter(item=>item!==value);this.data.hiddenWorkspaces=[...new Set([...(this.data.hiddenWorkspaces??[]),value])];if(this.data.workspace===value)delete this.data.workspace;await this.persist()}
  appearance():AppearancePreferences{const theme=this.data.appearance?.theme,density=this.data.appearance?.density;return {theme:theme==="light"||theme==="dark"?theme:"system",density:density==="compact"?"compact":"comfortable"}}
  async setAppearance(value:AppearancePreferences):Promise<void>{this.data.appearance=value;await this.persist()}
  language():LanguagePreference{return this.data.language??systemLanguage(app.getLocale())}
  async setLanguage(value:LanguagePreference):Promise<void>{this.data.language=value;await this.persist()}
  sidebarWidth():number{return clampSidebarWidth(this.data.sidebarWidth??DEFAULT_SIDEBAR_WIDTH)}
  async setSidebarWidth(value:number):Promise<void>{this.data.sidebarWidth=clampSidebarWidth(value);await this.persist()}
  snapshot():DesktopPreferencesSnapshot{return {appearance:this.appearance(),language:this.language(),sidebarWidth:this.sidebarWidth()}}
  private async persist():Promise<void>{await mkdir(path.dirname(this.file),{recursive:true});await writeFile(this.file,JSON.stringify(this.data,null,2),{encoding:"utf8",mode:0o600})}
}

export function systemLanguage(locale:string):LanguagePreference{
  return locale.trim().toLowerCase().startsWith("zh")?"zh-CN":"en";
}

export function clampSidebarWidth(value:number):number{
  return Math.min(MAX_SIDEBAR_WIDTH,Math.max(MIN_SIDEBAR_WIDTH,Math.round(value)));
}
