import { app, safeStorage } from "electron";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

export interface ProviderProfile { provider:"openai-compatible"|"anthropic"; model:string; judgeModel:string; baseUrl:string; hasApiKey:boolean }
export interface AppearancePreferences {theme:"system"|"light"|"dark";density:"comfortable"|"compact"}
interface Stored { workspace?:string; workspaces?:string[]; hiddenWorkspaces?:string[]; provider?:Omit<ProviderProfile,"hasApiKey">; encryptedApiKey?:string; appearance?:Partial<AppearancePreferences> }

export class Preferences {
  private readonly file=path.join(app.getPath("userData"),"desktop-settings.json");
  private data:Stored={};
  async load():Promise<void>{try{this.data=JSON.parse(await readFile(this.file,"utf8")) as Stored}catch{this.data={}}if(this.data.workspace&&!this.data.workspaces)this.data.workspaces=[this.data.workspace]}
  workspace():string|null{return this.data.workspace??null}
  workspaces(discovered:string[]=[]):string[]{const hidden=new Set(this.data.hiddenWorkspaces??[]);return [...new Set([...(this.data.workspaces??(this.data.workspace?[this.data.workspace]:[])),...discovered])].filter(item=>!hidden.has(item))}
  async synchronizeWorkspaces(discovered:string[]):Promise<string[]>{const merged=this.workspaces(discovered);if(JSON.stringify(merged)!==JSON.stringify(this.data.workspaces??[])){this.data.workspaces=merged;await this.persist()}return merged}
  async setWorkspace(value:string):Promise<void>{this.data.workspace=value;this.data.workspaces=this.workspaces().includes(value)?this.workspaces():[...this.workspaces(),value];this.data.hiddenWorkspaces=(this.data.hiddenWorkspaces??[]).filter(item=>item!==value);await this.persist()}
  async removeWorkspace(value:string):Promise<void>{this.data.workspaces=this.workspaces().filter(item=>item!==value);this.data.hiddenWorkspaces=[...new Set([...(this.data.hiddenWorkspaces??[]),value])];if(this.data.workspace===value)delete this.data.workspace;await this.persist()}
  profile():ProviderProfile|null{return this.data.provider?{...this.data.provider,hasApiKey:Boolean(this.data.encryptedApiKey)}:null}
  appearance():AppearancePreferences{const theme=this.data.appearance?.theme,density=this.data.appearance?.density;return {theme:theme==="light"||theme==="dark"?theme:"system",density:density==="compact"?"compact":"comfortable"}}
  apiKey():string|null{if(!this.data.encryptedApiKey||!safeStorage.isEncryptionAvailable())return null;try{return safeStorage.decryptString(Buffer.from(this.data.encryptedApiKey,"base64"))}catch{return null}}
  async setProfile(value:Omit<ProviderProfile,"hasApiKey">,apiKey?:string):Promise<void>{this.data.provider=value;if(apiKey){if(!safeStorage.isEncryptionAvailable())throw new Error("Secure credential storage is unavailable.");this.data.encryptedApiKey=safeStorage.encryptString(apiKey).toString("base64")}await this.persist()}
  async setAppearance(value:AppearancePreferences):Promise<void>{this.data.appearance=value;await this.persist()}
  private async persist():Promise<void>{await mkdir(path.dirname(this.file),{recursive:true});await writeFile(this.file,JSON.stringify(this.data,null,2),{encoding:"utf8",mode:0o600})}
}
