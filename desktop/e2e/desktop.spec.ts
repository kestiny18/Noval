import {expect,test,_electron as electron} from "@playwright/test";
import {mkdir,mkdtemp,rm,writeFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import {tmpdir} from "node:os";
import path from "node:path";

test("launches the real Electron host with a persistent single-page project shell",async()=>{
  const userData=await mkdtemp(path.join(tmpdir(),"noval-desktop-e2e-"));
  const settingsPath=path.join(userData,"noval-settings.json");await writeFile(settingsPath,JSON.stringify({sessions_dir:path.join(userData,"sessions")}),"utf8");
  const root=path.resolve(import.meta.dirname,"..");
  const executablePath=path.join(root,"node_modules","electron","dist",process.platform==="win32"?"electron.exe":"electron");
  const application=await electron.launch({executablePath,args:[".",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py",NOVAL_SETTINGS_PATH:settingsPath}});
  const page=await application.firstWindow();
  try{
    await expect(page.getByRole("button",{name:/add project/i})).toBeVisible();
    await expect(page.getByText(/添加一个项目以开始使用 Noval/i)).toBeVisible();
    await expect(page.getByRole("button",{name:/settings/i})).toBeVisible();
    expect(await page.evaluate(()=>({node:(window as any).require,api:Boolean(window.noval)}))).toEqual({node:undefined,api:true});
  }finally{
    const process=application.process();
    const exited=new Promise<void>(resolve=>{if(process.exitCode!==null)resolve();else process.once("exit",()=>resolve())});
    await page.close();await exited;await rm(userData,{recursive:true,force:true});
  }
});

test("uses folder state and hover actions for a persisted project",async()=>{
  const userData=await mkdtemp(path.join(tmpdir(),"noval-desktop-tree-e2e-"));
  const settingsPath=path.join(userData,"noval-settings.json");await writeFile(settingsPath,JSON.stringify({sessions_dir:path.join(userData,"sessions")}),"utf8");
  const projectPath=path.join(userData,"sample-project");await mkdir(projectPath);
  await writeFile(path.join(userData,"desktop-settings.json"),JSON.stringify({workspace:projectPath,workspaces:[projectPath]}),"utf8");
  const root=path.resolve(import.meta.dirname,".."),executablePath=path.join(root,"node_modules","electron","dist",process.platform==="win32"?"electron.exe":"electron");
  const application=await electron.launch({executablePath,args:[".",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py",NOVAL_SETTINGS_PATH:settingsPath}});const page=await application.firstWindow();
  try{const project=page.getByRole("button",{name:"sample-project",exact:true});await expect(project).toBeVisible();await expect(project.locator(".lucide-folder-open")).toBeVisible();await expect(page.getByRole("heading",{name:"我们应该在 sample-project 中构建什么？"})).toBeVisible();await project.hover();await expect(page.getByRole("button",{name:/New task in sample-project/i})).toBeVisible();await page.getByRole("button",{name:/New task in sample-project/i}).click();await expect(page.getByRole("heading",{name:"我们应该在 sample-project 中构建什么？"})).toBeVisible();await expect(page.locator(".tag-chip")).toHaveCount(0);await expect(page.getByText(/Export diagnostics/i)).toHaveCount(0)}
  finally{const process=application.process();const exited=new Promise<void>(resolve=>{if(process.exitCode!==null)resolve();else process.once("exit",()=>resolve())});await page.close();await exited;await rm(userData,{recursive:true,force:true})}
});

test("discovers projects and Sessions from Noval Core storage",async()=>{
  test.setTimeout(60_000);
  const userData=await mkdtemp(path.join(tmpdir(),"noval-desktop-core-state-e2e-"));
  const projectPath=path.join(userData,"core-project");await mkdir(projectPath);
  const sessionsRoot=path.join(userData,"sessions"),projectKey=createHash("sha256").update(path.resolve(projectPath)).digest("hex").slice(0,16),projectStore=path.join(sessionsRoot,projectKey);await mkdir(projectStore,{recursive:true});
  const createdAt="2026-07-23T00:00:00.000+08:00",sessionId="stored-session";
  const markdown=`## Rendered Markdown\n\nNoval shows **formatted text**.\n\n${Array.from({length:60},(_,index)=>`- Item ${index+1}`).join("\n")}`;
  const commandCall=(id:string)=>({role:"assistant",blocks:[{type:"tool_call",id,name:"run_bash",arguments:'{"command":"echo test"}'}]});
  const commandResult=(id:string)=>({role:"tool",blocks:[{type:"tool_result",call_id:id,content:"done",is_error:false}]});
  await writeFile(path.join(projectStore,"project.json"),JSON.stringify({real_workdir:path.resolve(projectPath),created_at:createdAt}),"utf8");
  await writeFile(path.join(projectStore,`${sessionId}.jsonl`),`${JSON.stringify({_meta:{schema_version:2,session_id:sessionId,created_at:createdAt,workdir:path.resolve(projectPath),model:"stored-model"}})}\n${JSON.stringify({seq:0,ts:createdAt,message:{role:"user",blocks:[{type:"text",text:"Stored conversation"}]}})}\n${JSON.stringify({seq:1,ts:createdAt,message:{role:"assistant",blocks:[{type:"text",text:markdown}]}})}\n${JSON.stringify({seq:2,ts:createdAt,message:commandCall("call-1")})}\n${JSON.stringify({seq:3,ts:createdAt,message:commandResult("call-1")})}\n${JSON.stringify({seq:4,ts:createdAt,message:commandCall("call-2")})}\n${JSON.stringify({seq:5,ts:createdAt,message:commandResult("call-2")})}\n`,"utf8");
  await writeFile(path.join(projectStore,"legacy.jsonl"),`${JSON.stringify({_meta:{schema_version:1,session_id:"legacy",created_at:createdAt,workdir:path.resolve(projectPath),model:"legacy-model"}})}\n${JSON.stringify({seq:0,ts:createdAt,msg:{role:"user",content:"Legacy conversation"}})}\n`,"utf8");
  const settingsPath=path.join(userData,"noval-settings.json");await writeFile(settingsPath,JSON.stringify({sessions_dir:sessionsRoot,provider:"openai-compatible",model:"core-model",judge_model:"core-judge",base_url:"https://core.example.test"}),"utf8");
  const root=path.resolve(import.meta.dirname,".."),executablePath=path.join(root,"node_modules","electron","dist",process.platform==="win32"?"electron.exe":"electron");
  const application=await electron.launch({executablePath,args:[".",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,DEEPSEEK_API_KEY:"e2e-placeholder",NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py",NOVAL_SETTINGS_PATH:settingsPath}});const page=await application.firstWindow();
  try{await expect(page.getByRole("button",{name:"core-project",exact:true})).toBeVisible();const legacy=page.getByRole("button",{name:"[incompatible v1] legacy"});await expect(legacy).toBeDisabled();await expect(legacy).toHaveAttribute("title","This Session uses schema v1 and cannot be opened by this Noval version.");await legacy.click({force:true});await expect(page.getByRole("alert")).toHaveCount(0);await expect(page.getByRole("button",{name:"Stored conversation"})).toBeVisible();await page.getByRole("button",{name:"Stored conversation"}).click();await expect(page.getByRole("heading",{name:"Rendered Markdown",level:2})).toBeVisible({timeout:30000});await expect(page.locator("strong",{hasText:"formatted text"})).toBeVisible();await expect(page.getByText("Ran 2 commands")).toBeVisible();await expect(page.getByText("Tool completed")).toHaveCount(0);const conversation=page.locator(".conversation");expect(await conversation.evaluate(element=>getComputedStyle(element).scrollbarWidth)).toBe("none");expect(await conversation.evaluate(element=>{element.scrollTop=element.scrollHeight;return element.scrollTop>0})).toBe(true);await page.getByRole("button",{name:"Settings"}).click();await expect(page.getByLabel("Model",{exact:true})).toHaveValue("core-model");await expect(page.getByLabel("Judge model")).toHaveValue("core-judge");await expect(page.getByLabel("Base URL")).toHaveValue("https://core.example.test")}
  finally{const process=application.process();const exited=new Promise<void>(resolve=>{if(process.exitCode!==null)resolve();else process.once("exit",()=>resolve())});await page.close();await exited;await rm(userData,{recursive:true,force:true})}
});
