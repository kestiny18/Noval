import {mkdtemp,readFile,rm,writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import path from "node:path";
import {afterEach,beforeEach,expect,it,vi} from "vitest";

const state=vi.hoisted(()=>({userData:"",locale:"en-US"}));
vi.mock("electron",()=>({app:{getPath:()=>state.userData,getLocale:()=>state.locale}}));

import {MAX_SIDEBAR_WIDTH,MIN_SIDEBAR_WIDTH,Preferences,systemLanguage} from "./preferences";

beforeEach(async()=>{state.userData=await mkdtemp(path.join(tmpdir(),"noval-preferences-"));state.locale="en-US"});
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

it("uses Chinese only for a zh system locale until the user chooses",async()=>{
 state.locale="zh-Hans-CN";
 const preferences=new Preferences();
 await preferences.load();

 expect(preferences.language()).toBe("zh-CN");
 expect(systemLanguage("en-CN")).toBe("en");

 await preferences.setLanguage("en");
 state.locale="zh-CN";
 const restored=new Preferences();
 await restored.load();
 expect(restored.language()).toBe("en");
});

it("clamps and persists the project sidebar width",async()=>{
 const preferences=new Preferences();
 await preferences.load();
 expect(preferences.sidebarWidth()).toBe(278);

 await preferences.setSidebarWidth(MAX_SIDEBAR_WIDTH+200);
 expect(preferences.sidebarWidth()).toBe(MAX_SIDEBAR_WIDTH);

 await preferences.setSidebarWidth(MIN_SIDEBAR_WIDTH-200);
 const restored=new Preferences();
 await restored.load();
 expect(restored.sidebarWidth()).toBe(MIN_SIDEBAR_WIDTH);
 expect(restored.snapshot()).toEqual({
  appearance:{theme:"system",density:"comfortable"},
  language:"en",
  sidebarWidth:MIN_SIDEBAR_WIDTH,
 });
});
