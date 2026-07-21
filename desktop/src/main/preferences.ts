import { app, safeStorage } from "electron";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

export interface ProviderProfile { provider:"openai-compatible"|"anthropic"; model:string; judgeModel:string; baseUrl:string; hasApiKey:boolean }
interface Stored { workspace?:string; provider?:Omit<ProviderProfile,"hasApiKey">; encryptedApiKey?:string }

export class Preferences {
  private readonly file=path.join(app.getPath("userData"),"desktop-settings.json");
  private data:Stored={};
  async load():Promise<void>{try{this.data=JSON.parse(await readFile(this.file,"utf8")) as Stored}catch{this.data={}}}
  workspace():string|null{return this.data.workspace??null}
  async setWorkspace(value:string):Promise<void>{this.data.workspace=value;await this.persist()}
  profile():ProviderProfile|null{return this.data.provider?{...this.data.provider,hasApiKey:Boolean(this.data.encryptedApiKey)}:null}
  apiKey():string|null{if(!this.data.encryptedApiKey||!safeStorage.isEncryptionAvailable())return null;try{return safeStorage.decryptString(Buffer.from(this.data.encryptedApiKey,"base64"))}catch{return null}}
  async setProfile(value:Omit<ProviderProfile,"hasApiKey">,apiKey?:string):Promise<void>{this.data.provider=value;if(apiKey){if(!safeStorage.isEncryptionAvailable())throw new Error("Secure credential storage is unavailable.");this.data.encryptedApiKey=safeStorage.encryptString(apiKey).toString("base64")}await this.persist()}
  private async persist():Promise<void>{await mkdir(path.dirname(this.file),{recursive:true});await writeFile(this.file,JSON.stringify(this.data,null,2),{encoding:"utf8",mode:0o600})}
}
