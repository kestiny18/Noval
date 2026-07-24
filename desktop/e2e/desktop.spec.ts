import {expect,test,_electron as electron} from "@playwright/test";
import {mkdir,mkdtemp,readFile,rm,writeFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import {createServer} from "node:http";
import {tmpdir} from "node:os";
import path from "node:path";

function runtimeSettings(sessionsDir:string,model="deepseek-v4-pro",judgeModel="deepseek-v4-flash"){
  const root=path.dirname(sessionsDir);
  return {
    schema_version:2,
    models:{
      revision:1,
      connections:[{id:"connection-e2e",revision:1,label:"E2E Connection",profile_id:"custom",adapter:"openai-compatible",base_url:"https://api.example.test/v1",api_key:"",api_key_env:"NOVAL_E2E_API_KEY"}],
      configured:[
        {id:"model-primary",label:model,connection_id:"connection-e2e",model},
        {id:"model-judge",label:judgeModel,connection_id:"connection-e2e",model:judgeModel},
      ],
      default_model_id:"model-primary",
    },
    max_steps:40,max_tool_output_chars:8000,persist_sessions:true,sessions_dir:sessionsDir,
    persist_logs:true,logs_dir:path.join(root,"logs"),log_retention_days:14,
    persist_usage:true,usage_dir:path.join(root,"usage"),context_budget_tokens:256000,
    request_timeout_seconds:120,request_max_retries:2,anthropic_max_tokens:8192,
  };
}

async function startMockOpenAIProvider(){
  const models:string[]=[];
  let releaseFirst!:()=>void;
  const firstResponse=new Promise<void>(resolve=>{releaseFirst=resolve});
  const server=createServer((request,response)=>{
    const chunks:Buffer[]=[];
    request.on("data",chunk=>chunks.push(Buffer.from(chunk)));
    request.on("end",()=>{
      let payload:{model?:string;stream?:boolean}={};
      try{payload=JSON.parse(Buffer.concat(chunks).toString("utf8"))}catch{}
      const model=payload.model??"unknown-model";
      models.push(model);
      const send=()=>{
        if(payload.stream){
          response.writeHead(200,{"content-type":"text/event-stream","cache-control":"no-cache"});
          response.write(`data: ${JSON.stringify({id:"chatcmpl-e2e",object:"chat.completion.chunk",created:1,model,choices:[{index:0,delta:{role:"assistant",content:`Reply from ${model}`},finish_reason:null}]})}\n\n`);
          response.write(`data: ${JSON.stringify({id:"chatcmpl-e2e",object:"chat.completion.chunk",created:1,model,choices:[{index:0,delta:{},finish_reason:"stop"}]})}\n\n`);
          response.end("data: [DONE]\n\n");
          return;
        }
        response.writeHead(200,{"content-type":"application/json"});
        response.end(JSON.stringify({id:"chatcmpl-e2e",object:"chat.completion",created:1,model,choices:[{index:0,message:{role:"assistant",content:`Reply from ${model}`},finish_reason:"stop"}],usage:{prompt_tokens:1,completion_tokens:1,total_tokens:2}}));
      };
      if(models.length===1)void firstResponse.then(send);else send();
    });
  });
  await new Promise<void>((resolve,reject)=>{
    server.once("error",reject);
    server.listen(0,"127.0.0.1",()=>resolve());
  });
  const address=server.address();
  if(!address||typeof address==="string")throw new Error("Mock Provider did not bind a TCP port.");
  return {
    baseUrl:`http://127.0.0.1:${address.port}/v1`,
    models,
    releaseFirst,
    close:()=>new Promise<void>((resolve,reject)=>server.close(error=>error?reject(error):resolve())),
  };
}

async function filesContaining(root:string,needle:string,excluded:string){
  const matches:string[]=[];
  async function walk(directory:string){
    const entries=await import("node:fs/promises").then(fs=>fs.readdir(directory,{withFileTypes:true}));
    for(const entry of entries){
      const target=path.join(directory,entry.name);
      if(entry.isDirectory()){await walk(target);continue}
      if(path.resolve(target)===path.resolve(excluded))continue;
      try{if((await readFile(target,"utf8")).includes(needle))matches.push(target)}catch{}
    }
  }
  await walk(root);
  return matches;
}

test("launches the real Electron host with a persistent single-page project shell",async()=>{
  const userData=await mkdtemp(path.join(tmpdir(),"noval-desktop-e2e-"));
  const settingsPath=path.join(userData,"noval-settings.json");await writeFile(settingsPath,JSON.stringify(runtimeSettings(path.join(userData,"sessions"))),"utf8");
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
  const settingsPath=path.join(userData,"noval-settings.json");await writeFile(settingsPath,JSON.stringify(runtimeSettings(path.join(userData,"sessions"))),"utf8");
  const projectPath=path.join(userData,"sample-project");await mkdir(projectPath);
  await writeFile(path.join(userData,"desktop-settings.json"),JSON.stringify({workspace:projectPath,workspaces:[projectPath]}),"utf8");
  const root=path.resolve(import.meta.dirname,".."),executablePath=path.join(root,"node_modules","electron","dist",process.platform==="win32"?"electron.exe":"electron");
  const application=await electron.launch({executablePath,args:[".",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py",NOVAL_SETTINGS_PATH:settingsPath}});const page=await application.firstWindow();
  try{const project=page.getByRole("button",{name:"sample-project",exact:true}),screenshotDir=process.env.NOVAL_OVERLAY_SCREENSHOT_DIR;await expect(project).toBeVisible();await expect(project.locator(".lucide-folder-open")).toBeVisible();await expect(page.getByRole("heading",{name:"我们应该在 sample-project 中构建什么？"})).toBeVisible();await project.hover();await expect(page.getByRole("button",{name:/New task in sample-project/i})).toBeVisible();await page.getByRole("button",{name:/Project actions for sample-project/i}).click();await expect(page.getByRole("menu",{name:/Actions for sample-project/i})).toBeVisible();if(screenshotDir){await mkdir(screenshotDir,{recursive:true});await page.screenshot({path:path.join(screenshotDir,"project-menu.png")})}await page.getByRole("menuitem",{name:/Remove project/i}).click();const dialog=page.getByRole("dialog",{name:/Remove sample-project/i});await expect(dialog).toBeVisible();await expect(dialog).toContainText("Files and Sessions on disk will not be deleted");if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"remove-project-dialog.png")});await dialog.getByRole("button",{name:"Cancel"}).click();await expect(dialog).toBeHidden();await expect(project).toBeVisible();await project.hover();await page.getByRole("button",{name:/New task in sample-project/i}).click();await expect(page.getByRole("heading",{name:"我们应该在 sample-project 中构建什么？"})).toBeVisible();await expect(page.locator(".tag-chip")).toHaveCount(0);await expect(page.getByText(/Export diagnostics/i)).toHaveCount(0)}
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
  await writeFile(path.join(projectStore,`${sessionId}.jsonl`),`${JSON.stringify({_meta:{schema_version:3,session_id:sessionId,created_at:createdAt,workdir:path.resolve(projectPath)}})}\n${JSON.stringify({seq:0,ts:createdAt,message:{role:"user",blocks:[{type:"text",text:"Stored conversation"}]}})}\n${JSON.stringify({seq:1,ts:createdAt,message:{role:"assistant",blocks:[{type:"text",text:markdown}]}})}\n${JSON.stringify({seq:2,ts:createdAt,message:commandCall("call-1")})}\n${JSON.stringify({seq:3,ts:createdAt,message:commandResult("call-1")})}\n${JSON.stringify({seq:4,ts:createdAt,message:commandCall("call-2")})}\n${JSON.stringify({seq:5,ts:createdAt,message:commandResult("call-2")})}\n`,"utf8");
  await writeFile(path.join(projectStore,`${sessionId}.meta.json`),JSON.stringify({application:{schema_version:2,selected_model_id:"model-primary",selected_judge_model_id:"model-judge",configuration_revision:1}}),"utf8");
  await writeFile(path.join(projectStore,"legacy.jsonl"),`${JSON.stringify({_meta:{schema_version:2,session_id:"legacy",created_at:createdAt,workdir:path.resolve(projectPath),model:"legacy-model"}})}\n${JSON.stringify({seq:0,ts:createdAt,message:{role:"user",blocks:[{type:"text",text:"Legacy conversation"}]}})}\n`,"utf8");
  const settingsPath=path.join(userData,"noval-settings.json");await writeFile(settingsPath,JSON.stringify(runtimeSettings(sessionsRoot,"core-model","core-judge")),"utf8");
  const root=path.resolve(import.meta.dirname,".."),executablePath=path.join(root,"node_modules","electron","dist",process.platform==="win32"?"electron.exe":"electron");
  const application=await electron.launch({executablePath,args:[".",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,DEEPSEEK_API_KEY:"e2e-placeholder",NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py",NOVAL_SETTINGS_PATH:settingsPath}});const page=await application.firstWindow();
  try{await expect(page.getByRole("button",{name:"core-project",exact:true})).toBeVisible();await expect(page.getByRole("button",{name:/incompatible v2/i})).toHaveCount(0);await expect(page.getByRole("button",{name:"Stored conversation"})).toBeVisible();await page.getByRole("button",{name:"Stored conversation"}).click();await expect(page.getByRole("heading",{name:"Rendered Markdown",level:2})).toBeVisible({timeout:30000});await expect(page.locator("strong",{hasText:"formatted text"})).toBeVisible();const activity=page.getByText("Ran 2 commands");await expect(activity).toBeVisible();await expect(page.getByText("Tool completed")).toHaveCount(0);await activity.click();await expect(page.locator(".activity-details pre").first()).toHaveText("done");const viewport=page.locator(".conversation-viewport");expect(await viewport.evaluate(element=>getComputedStyle(element).scrollbarWidth)).toBe("thin");expect(await viewport.evaluate(element=>{element.scrollTop=element.scrollHeight;return element.scrollTop>0})).toBe(true);const geometry=await page.evaluate(()=>{const last=document.querySelector(".activity-row")?.getBoundingClientRect(),composer=document.querySelector(".composer")?.getBoundingClientRect();return {lastBottom:last?.bottom??0,composerTop:composer?.top??0}});expect(geometry.lastBottom).toBeLessThan(geometry.composerTop);await page.getByRole("button",{name:"Settings"}).click();await expect(page.getByRole("heading",{name:"Models",exact:true})).toBeVisible();await expect(page.getByText(/core-model via E2E Connection/).first()).toBeVisible();await expect(page.getByText(/core-judge via E2E Connection/).first()).toBeVisible();await expect(page.getByLabel("Base URL")).toHaveValue("https://api.example.test/v1")}
  finally{const process=application.process();const exited=new Promise<void>(resolve=>{if(process.exitCode!==null)resolve();else process.once("exit",()=>resolve())});await page.close();await exited;await rm(userData,{recursive:true,force:true})}
});

test("renders the focused Settings pages and persists appearance locally",async()=>{
  const userData=await mkdtemp(path.join(tmpdir(),"noval-desktop-settings-e2e-"));
  const settingsPath=path.join(userData,"noval-settings.json");await writeFile(settingsPath,JSON.stringify(runtimeSettings(path.join(userData,"sessions"),"settings-model","settings-judge")),"utf8");
  const root=path.resolve(import.meta.dirname,".."),executablePath=path.join(root,"node_modules","electron","dist",process.platform==="win32"?"electron.exe":"electron");
  const application=await electron.launch({executablePath,args:[".",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,DEEPSEEK_API_KEY:"e2e-placeholder",NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py",NOVAL_SETTINGS_PATH:settingsPath}});const page=await application.firstWindow();
  const screenshotDir=process.env.NOVAL_SETTINGS_SCREENSHOT_DIR;if(screenshotDir)await mkdir(screenshotDir,{recursive:true});
  try{
    await page.getByRole("button",{name:"Settings"}).click();
    await expect(page.getByRole("heading",{name:"Models",exact:true})).toBeVisible();
    await expect(page.getByText(/settings-model via E2E Connection/).first()).toBeVisible();
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"settings-general.png")});
    await page.getByRole("button",{name:"Profile"}).click();
    await expect(page.getByRole("heading",{name:"Private by design"})).toBeVisible();
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"settings-profile.png")});
    await page.getByRole("button",{name:"Appearance"}).click();
    await expect(page.getByRole("heading",{name:"Appearance"})).toBeVisible();
    await page.getByRole("button",{name:"Dark"}).click();
    await page.getByRole("button",{name:"Compact"}).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme","dark");
    await expect(page.locator("html")).toHaveAttribute("data-density","compact");
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"settings-appearance-dark.png")});
    await page.getByRole("button",{name:/Back to Noval/i}).click();
    await expect(page.getByRole("button",{name:"Settings"})).toBeVisible();
  }finally{
    const process=application.process();const exited=new Promise<void>(resolve=>{if(process.exitCode!==null)resolve();else process.once("exit",()=>resolve())});await page.close();await exited;
  }
  const stored=JSON.parse(await readFile(path.join(userData,"desktop-settings.json"),"utf8"));
  expect(stored.appearance).toEqual({theme:"dark",density:"compact"});
  const relaunched=await electron.launch({executablePath,args:[".",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,DEEPSEEK_API_KEY:"e2e-placeholder",NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py",NOVAL_SETTINGS_PATH:settingsPath}});const restoredPage=await relaunched.firstWindow();
  await expect(restoredPage.locator("html")).toHaveAttribute("data-theme","dark");
  await expect(restoredPage.locator("html")).toHaveAttribute("data-density","compact");
  const relaunchedProcess=relaunched.process(),relaunchExited=new Promise<void>(resolve=>{if(relaunchedProcess.exitCode!==null)resolve();else relaunchedProcess.once("exit",()=>resolve())});await restoredPage.close();await relaunchExited;
  await rm(userData,{recursive:true,force:true});
});

test("configures, switches during a Turn, and restores one durable model selection",async()=>{
  test.setTimeout(90_000);
  const provider=await startMockOpenAIProvider();
  const userData=await mkdtemp(path.join(tmpdir(),"noval-desktop-model-flow-e2e-"));
  const projectPath=path.join(userData,"flow-project");
  const sessionsRoot=path.join(userData,"sessions");
  const settingsPath=path.join(userData,"noval-settings.json");
  const secret="NOVAL_E2E_WRITE_ONLY_SECRET";
  await mkdir(projectPath);
  const settings=runtimeSettings(sessionsRoot,"primary-model","alternate-model");
  settings.models.connections[0].base_url=provider.baseUrl;
  settings.request_timeout_seconds=5;
  settings.request_max_retries=0;
  await writeFile(settingsPath,JSON.stringify(settings),"utf8");
  await writeFile(path.join(userData,"desktop-settings.json"),JSON.stringify({workspace:projectPath,workspaces:[projectPath]}),"utf8");
  const root=path.resolve(import.meta.dirname,"..");
  const executablePath=path.join(root,"node_modules","electron","dist",process.platform==="win32"?"electron.exe":"electron");
  const launch=()=>electron.launch({executablePath,args:[".",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py",NOVAL_SETTINGS_PATH:settingsPath}});
  let application=await launch();
  try{
    let page=await application.firstWindow();
    await page.getByRole("button",{name:"Settings"}).click();
    await expect(page.getByRole("heading",{name:"Models",exact:true})).toBeVisible();
    await page.getByLabel("Connection label").fill("E2E Updated Connection");
    await page.getByLabel("API key",{exact:true}).fill(secret);
    await page.getByRole("button",{name:"Save Connection"}).click();
    await expect(page.getByText("Connection saved without restarting the Runtime.")).toBeVisible();
    await expect(page.getByLabel("API key",{exact:true})).toHaveValue("");
    const persistedSettings=JSON.parse(await readFile(settingsPath,"utf8"));
    expect(persistedSettings.models.connections[0].api_key).toBe(secret);
    expect(persistedSettings.models.connections[0].base_url).toBe(provider.baseUrl);
    await page.getByRole("button",{name:/Back to Noval/i}).click();

    const project=page.getByRole("button",{name:"flow-project",exact:true});
    await project.hover();
    await page.getByRole("button",{name:/New task in flow-project/i}).click();
    const selector=page.getByLabel("Session model");
    await expect(selector).toHaveValue("model-primary");
    await page.getByLabel("Message Noval").fill("First model request");
    await page.getByRole("button",{name:"Send"}).click();
    await expect(page.getByText("Next Turn",{exact:true})).toBeVisible();
    await expect.poll(()=>provider.models.length,{timeout:15_000}).toBe(1);
    await selector.selectOption("model-judge");
    await expect(selector).toHaveValue("model-judge");
    provider.releaseFirst();
    await expect(page.getByText("Reply from primary-model")).toBeVisible();

    await page.getByLabel("Message Noval").fill("Second model request");
    await page.getByRole("button",{name:"Send"}).click();
    await expect(page.getByText("Reply from alternate-model")).toBeVisible();
    expect(provider.models).toEqual(["primary-model","alternate-model"]);

    const firstProcess=application.process();
    const firstExit=new Promise<void>(resolve=>{if(firstProcess.exitCode!==null)resolve();else firstProcess.once("exit",()=>resolve())});
    await page.close();
    await firstExit;
    expect(await filesContaining(userData,secret,settingsPath)).toEqual([]);

    application=await launch();
    page=await application.firstWindow();
    await expect(page.getByRole("button",{name:"First model request"})).toBeVisible();
    await page.getByRole("button",{name:"First model request"}).click();
    await expect(page.getByLabel("Session model")).toHaveValue("model-judge");
    await expect(page.getByText("Reply from primary-model")).toBeVisible();
    await expect(page.getByText("Reply from alternate-model")).toBeVisible();
  }finally{
    provider.releaseFirst();
    const process=application.process();
    const exited=new Promise<void>(resolve=>{if(process.exitCode!==null)resolve();else process.once("exit",()=>resolve())});
    for(const page of application.windows())await page.close().catch(()=>{});
    await exited;
    await provider.close();
    await rm(userData,{recursive:true,force:true});
  }
});
