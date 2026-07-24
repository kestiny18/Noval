import { app } from "electron";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

export interface AppearancePreferences {theme:"system"|"light"|"dark";density:"comfortable"|"compact"}
interface Stored { workspace?:string; workspaces?:string[]; hiddenWorkspaces?:string[]; appearance?:Partial<AppearancePreferences> }

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
  private async persist():Promise<void>{await mkdir(path.dirname(this.file),{recursive:true});await writeFile(this.file,JSON.stringify(this.data,null,2),{encoding:"utf8",mode:0o600})}
}
