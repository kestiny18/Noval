import {mkdtemp,readFile,rm,writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import path from "node:path";
import {afterEach,beforeEach,expect,it,vi} from "vitest";

const state=vi.hoisted(()=>({userData:""}));
vi.mock("electron",()=>({app:{getPath:()=>state.userData}}));

import {Preferences} from "./preferences";

beforeEach(async()=>{state.userData=await mkdtemp(path.join(tmpdir(),"noval-preferences-"))});
afterEach(async()=>{await rm(state.userData,{recursive:true,force:true})});

it("removes legacy Desktop-owned provider credentials while keeping UI preferences",async()=>{
 const file=path.join(state.userData,"desktop-settings.json");
 await writeFile(file,JSON.stringify({
  workspace:"C:/workspace",
  appearance:{theme:"dark",density:"compact"},
  provider:{provider:"anthropic",model:"legacy"},
  encryptedApiKey:"legacy-ciphertext",
 }),"utf8");

 const preferences=new Preferences();
 await preferences.load();

 const stored=JSON.parse(await readFile(file,"utf8"));
 expect(stored).toEqual({
  workspace:"C:/workspace",
  appearance:{theme:"dark",density:"compact"},
 });
 expect(preferences.appearance()).toEqual({theme:"dark",density:"compact"});
});
