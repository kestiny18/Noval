import {expect,test,_electron as electron} from "@playwright/test";
import {mkdtemp,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import path from "node:path";

test("launches the real Electron host with workspace gating and hardened renderer",async()=>{
  const userData=await mkdtemp(path.join(tmpdir(),"noval-desktop-e2e-"));
  const root=path.resolve(import.meta.dirname,"..");
  const executablePath=path.join(root,"node_modules","electron","dist",process.platform==="win32"?"electron.exe":"electron");
  const application=await electron.launch({executablePath,args:[".",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py"}});
  const page=await application.firstWindow();
  try{
    await expect(page.getByRole("button",{name:/choose workspace/i})).toBeVisible();
    await expect(page.getByText(/No remote telemetry/i)).toBeVisible();
    expect(await page.evaluate(()=>({node:(window as any).require,api:Boolean(window.noval)}))).toEqual({node:undefined,api:true});
  }finally{
    const process=application.process();
    const exited=new Promise<void>(resolve=>{if(process.exitCode!==null)resolve();else process.once("exit",()=>resolve())});
    await page.close();await exited;await rm(userData,{recursive:true,force:true});
  }
});
