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
      connections:[
        {id:"connection-deepseek",revision:1,label:"DeepSeek",profile_id:"deepseek",adapter:"openai-compatible",base_url:"https://api.deepseek.com",api_key:"",api_key_env:"DEEPSEEK_API_KEY"},
        {id:"connection-e2e",revision:1,label:"E2E Connection",profile_id:"custom",adapter:"openai-compatible",base_url:"https://api.example.test/v1",api_key:"",api_key_env:"NOVAL_E2E_API_KEY"},
      ],
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

async function seedUsage(root:string){
  const localDay=(date:Date)=>[date.getFullYear(),String(date.getMonth()+1).padStart(2,"0"),String(date.getDate()).padStart(2,"0")].join("-");
  const now=new Date(),today=localDay(now),yesterdayDate=new Date(now);yesterdayDate.setDate(now.getDate()-1);
  const yesterday=localDay(yesterdayDate),daily=[
    [yesterday,[
      {schema_version:2,event_type:"model_usage",timestamp:`${yesterday}T09:00:00+08:00`,model:"deepseek-v4-pro",purpose:"agent",prompt_tokens:210000,completion_tokens:30000,total_tokens:240000},
      {schema_version:2,event_type:"turn",timestamp:`${yesterday}T10:05:00+08:00`,model:"deepseek-v4-pro",duration_ms:3_720_000},
    ]],
    [today,[{schema_version:2,event_type:"model_usage",timestamp:`${today}T10:00:00+08:00`,model:"deepseek-v4-flash",purpose:"judge",prompt_tokens:90000,completion_tokens:30000,total_tokens:120000}]],
  ] as const;
  for(const [day,events] of daily){
    const directory=path.join(root,"usage",day);await mkdir(directory,{recursive:true});
    await writeFile(path.join(directory,"noval-e2e.jsonl"),`${events.map(event=>JSON.stringify(event)).join("\n")}\n`,"utf8");
  }
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
          setTimeout(()=>{
            response.write(`data: ${JSON.stringify({id:"chatcmpl-e2e",object:"chat.completion.chunk",created:1,model,choices:[{index:0,delta:{},finish_reason:"stop"}]})}\n\n`);
            response.end("data: [DONE]\n\n");
          },250);
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
  const application=await electron.launch({executablePath,args:[".","--lang=en-US",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py",NOVAL_SETTINGS_PATH:settingsPath}});
  const page=await application.firstWindow();
  try{
    await expect(page.getByRole("button",{name:/add project/i})).toBeVisible();
    await expect(page.getByText(/Add a project to start using Noval/i)).toBeVisible();
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
  await writeFile(path.join(userData,"desktop-settings.json"),JSON.stringify({workspace:projectPath,workspaces:[projectPath],language:"en"}),"utf8");
  const root=path.resolve(import.meta.dirname,".."),executablePath=path.join(root,"node_modules","electron","dist",process.platform==="win32"?"electron.exe":"electron");
  const application=await electron.launch({executablePath,args:[".","--lang=en-US",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py",NOVAL_SETTINGS_PATH:settingsPath}});const page=await application.firstWindow();
  try{
    const project=page.getByRole("button",{name:"sample-project",exact:true}),screenshotDir=process.env.NOVAL_OVERLAY_SCREENSHOT_DIR;
    await expect(project).toBeVisible();
    await expect(project.locator(".lucide-folder-open")).toBeVisible();
    await expect(page.getByRole("heading",{name:"What should we build in sample-project?"})).toBeVisible();
    await expect(page.getByLabel("Message Noval")).toBeVisible();
    await expect(page.locator(".topbar")).toHaveCount(0);
    if(screenshotDir){await mkdir(screenshotDir,{recursive:true});await page.screenshot({path:path.join(screenshotDir,"project-composer-without-session.png")})}
    await project.hover();
    await expect(page.getByRole("button",{name:/New task in sample-project/i})).toBeVisible();
    await page.getByRole("button",{name:/Project actions for sample-project/i}).click();
    await expect(page.getByRole("menu",{name:/Actions for sample-project/i})).toBeVisible();
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"project-menu.png")});
    await page.getByRole("menuitem",{name:/Remove project/i}).click();
    const dialog=page.getByRole("dialog",{name:/Remove sample-project/i});
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("Files and Sessions on disk will not be deleted");
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"remove-project-dialog.png")});
    await dialog.getByRole("button",{name:"Cancel"}).click();
    await expect(dialog).toBeHidden();
    await expect(project).toBeVisible();
    await expect(page.locator(".tag-chip")).toHaveCount(0);
    await expect(page.getByText(/Export diagnostics/i)).toHaveCount(0);
  }
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
  await writeFile(path.join(projectStore,`${sessionId}.meta.json`),JSON.stringify({application:{schema_version:2,selected_model_id:"model-primary",selected_judge_model_id:"model-judge",configuration_revision:1},permissions:{mode:"ask",approved_tools:["call_mcp_tool","run_bash"]}}),"utf8");
  await writeFile(path.join(projectStore,"legacy.jsonl"),`${JSON.stringify({_meta:{schema_version:2,session_id:"legacy",created_at:createdAt,workdir:path.resolve(projectPath),model:"legacy-model"}})}\n${JSON.stringify({seq:0,ts:createdAt,message:{role:"user",blocks:[{type:"text",text:"Legacy conversation"}]}})}\n`,"utf8");
  const settingsPath=path.join(userData,"noval-settings.json");await writeFile(settingsPath,JSON.stringify(runtimeSettings(sessionsRoot,"core-model","core-judge")),"utf8");
  const root=path.resolve(import.meta.dirname,".."),executablePath=path.join(root,"node_modules","electron","dist",process.platform==="win32"?"electron.exe":"electron");
  const application=await electron.launch({executablePath,args:[".","--lang=en-US",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,DEEPSEEK_API_KEY:"e2e-placeholder",NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py",NOVAL_SETTINGS_PATH:settingsPath}});const page=await application.firstWindow();
  try{
    await expect(page.getByRole("button",{name:"core-project",exact:true})).toBeVisible();
    await expect(page.getByRole("button",{name:/incompatible v2/i})).toHaveCount(0);
    const sessionButton=page.getByRole("button",{name:"Stored conversation",exact:true});
    await expect(sessionButton).toBeVisible();
    await sessionButton.click();
    await expect(page.locator(".topbar")).toHaveText("Stored conversation",{timeout:30000});
    await expect(sessionButton).toHaveAttribute("aria-current","page");
    await expect(page.locator(".topbar")).not.toContainText(projectPath);
    await expect(page.getByRole("button",{name:/Rename task/i})).toHaveCount(0);
    await expect(page.getByRole("heading",{name:"Rendered Markdown",level:2})).toBeVisible({timeout:30000});
    const grants=page.getByRole("button",{name:"2 grants"});await expect(grants).toBeVisible();
    const grantGeometry=await grants.evaluate(element=>{const icon=element.querySelector("svg")!.getBoundingClientRect(),label=element.querySelector("span")!.getBoundingClientRect();return {iconX:icon.x,labelX:label.x,verticalDistance:Math.abs(icon.y+icon.height/2-(label.y+label.height/2))}});
    expect(grantGeometry.labelX).toBeGreaterThan(grantGeometry.iconX);expect(grantGeometry.verticalDistance).toBeLessThan(2);
    await grants.click();const grantsDialog=page.getByRole("dialog",{name:"Session permissions"});await expect(grantsDialog).toBeVisible();expect((await grantsDialog.boundingBox())!.width).toBeLessThanOrEqual(360);
    if(process.env.NOVAL_OVERLAY_SCREENSHOT_DIR){await mkdir(process.env.NOVAL_OVERLAY_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.NOVAL_OVERLAY_SCREENSHOT_DIR,"session-grants.png")})}
    await grants.click();await expect(grantsDialog).toBeHidden();
    await page.getByRole("button",{name:"Session access"}).click();
    const accessMenu=page.getByRole("menu",{name:"Session access"}),choices=accessMenu.locator(".permission-choice-copy");
    await expect(accessMenu).toBeVisible();expect((await accessMenu.boundingBox())!.width).toBeLessThanOrEqual(360);
    const choiceX=await choices.evaluateAll(elements=>elements.map(element=>element.getBoundingClientRect().x));expect(choiceX[0]).toBeCloseTo(choiceX[1],0);
    await page.keyboard.press("Escape");await expect(accessMenu).toBeHidden();
    await page.getByRole("button",{name:"Session model"}).click();
    await expect(page.getByRole("menu",{name:"Session model"}).locator("strong").first()).toHaveCSS("text-align","left");
    await page.keyboard.press("Escape");
    await expect(page.locator("strong",{hasText:"formatted text"})).toBeVisible();
    const userMessage=page.locator(".message-user").filter({hasText:"Stored conversation"});
    await expect(userMessage.locator(":scope > .message-content")).toHaveCount(1);
    await expect(userMessage.locator(":scope > .message-meta")).toHaveCount(1);
    const messageGeometry=await page.evaluate(()=>{const conversation=document.querySelector(".conversation")!.getBoundingClientRect(),message=document.querySelector(".message-user")!.getBoundingClientRect();return {conversationWidth:conversation.width,messageWidth:message.width,rightGap:Math.abs(conversation.right-message.right)}});
    expect(messageGeometry.messageWidth).toBeLessThan(messageGeometry.conversationWidth*.6);
    expect(messageGeometry.rightGap).toBeLessThanOrEqual(1);
    const screenshotDir=process.env.NOVAL_OVERLAY_SCREENSHOT_DIR;
    if(screenshotDir){await mkdir(screenshotDir,{recursive:true});await userMessage.hover();await page.screenshot({path:path.join(screenshotDir,"restored-session.png")})}
    const activity=page.getByText("Ran 2 commands");
    await expect(activity).toBeVisible();
    await expect(page.getByText("Tool completed")).toHaveCount(0);
    await activity.click();
    await expect(page.locator(".activity-details pre").first()).toHaveText("done");
    const viewport=page.locator(".conversation-viewport");
    expect(await viewport.evaluate(element=>getComputedStyle(element).scrollbarWidth)).toBe("thin");
    expect(await viewport.evaluate(element=>{element.scrollTop=element.scrollHeight;return element.scrollTop>0})).toBe(true);
    const geometry=await page.evaluate(()=>{const last=document.querySelector(".activity-row")?.getBoundingClientRect(),viewport=document.querySelector(".conversation-viewport")?.getBoundingClientRect(),composer=document.querySelector(".composer")?.getBoundingClientRect();return {lastBottom:last?.bottom??0,viewportBottom:viewport?.bottom??0,composerTop:composer?.top??0}});
    expect(geometry.lastBottom).toBeLessThan(geometry.composerTop);
    expect(geometry.viewportBottom).toBeLessThanOrEqual(geometry.composerTop);
    await sessionButton.hover();
    await page.getByRole("button",{name:"Task actions for Stored conversation"}).click();
    await expect(page.getByRole("menu",{name:"Actions for Stored conversation"})).toBeVisible();
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"session-menu.png")});
    await page.getByRole("menuitem",{name:"Rename task"}).click();
    const renameDialog=page.getByRole("dialog",{name:"Rename task"}),titleInput=page.getByLabel("Task title");
    await expect(renameDialog).toBeVisible();
    await expect(titleInput).toHaveValue("Stored conversation");
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"session-rename-dialog.png")});
    await titleInput.fill("Renamed conversation");
    await renameDialog.getByRole("button",{name:"Save title"}).click();
    await expect(renameDialog).toBeHidden();
    await expect(page.getByRole("button",{name:"Renamed conversation",exact:true})).toHaveAttribute("aria-current","page");
    await expect(page.locator(".topbar")).toHaveText("Renamed conversation");
    await page.getByRole("button",{name:"Settings"}).click();
    await expect(page.getByRole("heading",{name:"General"})).toBeVisible();
    await page.getByRole("button",{name:"Models"}).click();
    await expect(page.getByRole("heading",{name:"Models",exact:true})).toBeVisible();
    await expect(page.getByText("DeepSeek",{exact:true})).toBeVisible();
    await expect(page.getByText(/provider|vendor|runtime|electron|python|sidecar/i)).toHaveCount(0);
    await expect(page.getByLabel("Base URL")).toHaveCount(0);
  }
  finally{const process=application.process();const exited=new Promise<void>(resolve=>{if(process.exitCode!==null)resolve();else process.once("exit",()=>resolve())});await page.close();await exited;await rm(userData,{recursive:true,force:true})}
});

test("persists appearance, language, and a resized project sidebar",async()=>{
  const userData=await mkdtemp(path.join(tmpdir(),"noval-desktop-settings-e2e-"));
  await seedUsage(userData);
  const settingsPath=path.join(userData,"noval-settings.json");await writeFile(settingsPath,JSON.stringify(runtimeSettings(path.join(userData,"sessions"),"settings-model","settings-judge")),"utf8");
  const root=path.resolve(import.meta.dirname,".."),executablePath=path.join(root,"node_modules","electron","dist",process.platform==="win32"?"electron.exe":"electron");
  const application=await electron.launch({executablePath,args:[".","--lang=en-US",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,DEEPSEEK_API_KEY:"e2e-placeholder",NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py",NOVAL_SETTINGS_PATH:settingsPath}});const page=await application.firstWindow();
  const screenshotDir=process.env.NOVAL_SETTINGS_SCREENSHOT_DIR;if(screenshotDir)await mkdir(screenshotDir,{recursive:true});
  try{
    const separator=page.getByRole("separator",{name:"Resize project sidebar"});
    const box=await separator.boundingBox();if(!box)throw new Error("Sidebar separator has no bounding box.");
    await page.mouse.move(box.x+box.width/2,box.y+40);
    await page.mouse.down();
    await page.mouse.move(box.x+box.width/2+(350-278),box.y+40,{steps:4});
    await page.mouse.up();
    await expect(separator).toHaveAttribute("aria-valuenow","350");
    await page.getByRole("button",{name:"Settings"}).click();
    await expect(page.getByRole("heading",{name:"General"})).toBeVisible();
    const navigation=page.getByRole("navigation",{name:"Settings sections"});
    await expect(navigation.getByRole("button")).toHaveText(["General","Appearance","Models"]);
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"settings-general.png")});
    await page.getByRole("button",{name:"Models"}).click();
    await expect(page.getByRole("heading",{name:"Models",exact:true})).toBeVisible();
    await expect(page.getByText("DeepSeek",{exact:true})).toBeVisible();
    await expect(page.getByRole("gridcell")).toHaveCount(364);
    await expect(page.getByText("Recent 52 weeks",{exact:true})).toHaveCount(0);
    await expect(page.locator(".usage-legend")).toHaveCount(0);
    await expect(page.locator(".usage-metric",{hasText:"Cumulative Tokens"})).toContainText("360K");
    await expect(page.locator(".usage-metric",{hasText:"Peak daily Tokens"})).toContainText("240K");
    await expect(page.locator(".usage-metric",{hasText:"Longest task duration"})).toContainText("1h 2m");
    const usageGeometry=await page.evaluate(()=>{const scrollElement=document.querySelector(".usage-calendar-scroll")! as HTMLElement,scroll=scrollElement.getBoundingClientRect(),style=getComputedStyle(scrollElement),grid=document.querySelector(".usage-grid")!.getBoundingClientRect(),last=document.querySelectorAll(".usage-cell")[363]!.getBoundingClientRect();return {available:scroll.width-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight),gridWidth:grid.width,lastGap:Math.abs(grid.right-last.right),horizontalOverflow:scrollElement.scrollWidth-scrollElement.clientWidth}});
    expect(usageGeometry.gridWidth).toBeGreaterThanOrEqual(usageGeometry.available-1);
    expect(usageGeometry.lastGap).toBeLessThanOrEqual(1);
    expect(usageGeometry.horizontalOverflow).toBeLessThanOrEqual(1);
    const firstUsageCell=page.getByRole("gridcell").first();await firstUsageCell.hover();
    const tooltipGeometry=await firstUsageCell.evaluate(element=>{const cell=element.getBoundingClientRect(),tooltip=element.querySelector(".usage-tooltip")!.getBoundingClientRect();return {cellBottom:cell.bottom,tooltipTop:tooltip.top}});
    expect(tooltipGeometry.tooltipTop).toBeGreaterThan(tooltipGeometry.cellBottom);
    await page.getByLabel("Usage model").selectOption("deepseek-v4-flash");
    await expect(page.locator(".usage-metric",{hasText:"Cumulative Tokens"})).toContainText("120K");
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"settings-token-activity.png")});
    await expect(page.getByLabel("Base URL")).toHaveCount(0);
    await page.getByRole("button",{name:"Appearance"}).click();
    await expect(page.getByRole("heading",{name:"Appearance"})).toBeVisible();
    await page.getByRole("button",{name:"Dark"}).click();
    await page.getByRole("button",{name:"Compact"}).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme","dark");
    await expect(page.locator("html")).toHaveAttribute("data-density","compact");
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"settings-appearance-dark.png")});
    await page.getByRole("button",{name:"General"}).click();
    await page.getByLabel("Display language").selectOption("zh-CN");
    await expect(page.locator("html")).toHaveAttribute("lang","zh-CN");
    await expect(page.getByRole("heading",{name:"常规",exact:true})).toBeVisible();
    await page.getByRole("button",{name:"模型"}).click();
    await expect(page.getByLabel("API key")).toBeVisible();
    await expect(page.getByText(/API 密钥/)).toHaveCount(0);
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"settings-models-zh.png")});
    await page.getByRole("button",{name:/返回 Noval/i}).click();
    await expect(page.getByRole("button",{name:"设置"})).toBeVisible();
  }finally{
    const process=application.process();const exited=new Promise<void>(resolve=>{if(process.exitCode!==null)resolve();else process.once("exit",()=>resolve())});await page.close();await exited;
  }
  const stored=JSON.parse(await readFile(path.join(userData,"desktop-settings.json"),"utf8"));
  expect(stored.appearance).toEqual({theme:"dark",density:"compact"});
  expect(stored.language).toBe("zh-CN");
  expect(stored.sidebarWidth).toBe(350);
  const relaunched=await electron.launch({executablePath,args:[".","--lang=en-US",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,DEEPSEEK_API_KEY:"e2e-placeholder",NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py",NOVAL_SETTINGS_PATH:settingsPath}});const restoredPage=await relaunched.firstWindow();
  await expect(restoredPage.locator("html")).toHaveAttribute("data-theme","dark");
  await expect(restoredPage.locator("html")).toHaveAttribute("data-density","compact");
  await expect(restoredPage.locator("html")).toHaveAttribute("lang","zh-CN");
  await expect(restoredPage.getByRole("separator",{name:"调整项目侧栏宽度"})).toHaveAttribute("aria-valuenow","350");
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
  settings.models.connections[1].base_url=provider.baseUrl;
  settings.request_timeout_seconds=5;
  settings.request_max_retries=0;
  await writeFile(settingsPath,JSON.stringify(settings),"utf8");
  await writeFile(path.join(userData,"desktop-settings.json"),JSON.stringify({workspace:projectPath,workspaces:[projectPath],language:"en"}),"utf8");
  const root=path.resolve(import.meta.dirname,"..");
  const executablePath=path.join(root,"node_modules","electron","dist",process.platform==="win32"?"electron.exe":"electron");
  const launch=()=>electron.launch({executablePath,args:[".","--lang=en-US",`--user-data-dir=${userData}`],cwd:root,env:{...process.env,NOVAL_E2E_API_KEY:"e2e-provider-key",NOVAL_PYTHON:process.env.NOVAL_PYTHON??"py",NOVAL_SETTINGS_PATH:settingsPath}});
  const screenshotDir=process.env.NOVAL_OVERLAY_SCREENSHOT_DIR;
  if(screenshotDir)await mkdir(screenshotDir,{recursive:true});
  let application=await launch();
  try{
    let page=await application.firstWindow();
    await page.getByRole("button",{name:"Settings"}).click();
    await expect(page.getByRole("heading",{name:"General"})).toBeVisible();
    await page.getByRole("button",{name:"Models"}).click();
    await expect(page.getByRole("heading",{name:"Models",exact:true})).toBeVisible();
    await page.getByLabel("API key",{exact:true}).fill(secret);
    await page.getByRole("button",{name:"Save API key"}).click();
    await expect(page.getByText("API key saved. No restart needed.")).toBeVisible();
    await expect(page.getByLabel("API key",{exact:true})).toHaveValue("");
    const persistedSettings=JSON.parse(await readFile(settingsPath,"utf8"));
    expect(persistedSettings.models.connections[0].api_key).toBe(secret);
    expect(persistedSettings.models.connections[1].base_url).toBe(provider.baseUrl);
    await page.getByRole("button",{name:/Back to Noval/i}).click();

    await expect(page.getByRole("button",{name:"flow-project",exact:true})).toBeVisible();
    const selector=page.getByRole("button",{name:"Session model"});
    await expect(selector).toContainText("primary-model");
    const access=page.getByRole("button",{name:"Session access"});
    await expect(access).toContainText("Request approval");
    await access.click();
    const permissionMenu=page.getByRole("menu",{name:"Session access"}),askOption=page.getByRole("menuitemradio",{name:/Request approval/i});
    await expect(permissionMenu).toBeVisible();
    await expect(permissionMenu).toContainText("How should Noval approve actions?");
    await expect(askOption).toBeFocused();
    const permissionMenuWidth=await permissionMenu.evaluate(element=>Math.round(element.getBoundingClientRect().width));
    expect(permissionMenuWidth).toBeGreaterThanOrEqual(336);
    expect(permissionMenuWidth).toBeLessThanOrEqual(342);
    if(screenshotDir){
      await page.screenshot({path:path.join(screenshotDir,"permission-menu.png")});
      await page.evaluate(()=>document.documentElement.dataset.theme="dark");
      await page.screenshot({path:path.join(screenshotDir,"permission-menu-dark.png")});
      await page.evaluate(()=>document.documentElement.dataset.theme="light");
    }
    await page.getByRole("menuitemradio",{name:/Full access/i}).click();
    await expect(page.getByRole("status")).toContainText("Full access enabled");
    await expect(access).toHaveClass(/full_access/);
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"full-access-toast.png")});
    await access.click();
    await expect(page.getByRole("status")).toHaveCount(0);
    await expect(page.getByRole("menuitemradio",{name:/Full access/i})).toBeFocused();
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"permission-menu-full-access.png")});
    await page.getByRole("menuitemradio",{name:/Request approval/i}).click();
    await expect(access).toContainText("Request approval");
    await page.getByLabel("Message Noval").fill("First model request");
    await page.getByRole("button",{name:"Send"}).click();
    await expect(page.locator(".message-user").filter({hasText:"First model request"})).toBeVisible();
    await expect(page.locator(".turn-progress")).toContainText("Thinking");
    await expect(page.locator(".turn-progress")).toContainText("Worked for");
    await expect(page.getByText("Next turn",{exact:true})).toBeVisible();
    await expect.poll(()=>provider.models.length,{timeout:15_000}).toBe(1);
    await selector.click();
    const modelMenu=page.getByRole("menu",{name:"Session model"}),selectedModel=page.getByRole("menuitemradio",{name:"primary-model"});
    await expect(modelMenu).toBeVisible();
    await expect(selectedModel).toBeFocused();
    const modelMenuWidth=await modelMenu.evaluate(element=>Math.round(element.getBoundingClientRect().width));
    expect(modelMenuWidth).toBeGreaterThanOrEqual(216);
    expect(modelMenuWidth).toBeLessThanOrEqual(222);
    if(screenshotDir){
      await page.screenshot({path:path.join(screenshotDir,"model-menu.png")});
      await page.evaluate(()=>document.documentElement.dataset.theme="dark");
      await page.screenshot({path:path.join(screenshotDir,"model-menu-dark.png")});
      await page.evaluate(()=>document.documentElement.dataset.theme="light");
    }
    await page.getByRole("menuitemradio",{name:"alternate-model"}).click();
    await expect(selector).toContainText("alternate-model");
    provider.releaseFirst();
    await expect(page.locator(".turn-progress")).toContainText("Responding");
    await expect(page.locator(".message-assistant")).toContainText("Reply from primary-model");
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"streaming-turn.png")});
    await expect(page.getByText("Reply from primary-model")).toBeVisible();
    await expect(page.getByRole("button",{name:"Send"})).toBeVisible();
    await expect(page.locator(".turn-progress")).toHaveCount(0);
    await expect(page.locator(".turn-elapsed")).toContainText("Worked for");
    if(screenshotDir)await page.screenshot({path:path.join(screenshotDir,"completed-turn.png")});

    await page.getByLabel("Message Noval").fill("Second model request");
    await page.getByRole("button",{name:"Send"}).click();
    await expect(page.getByText("Reply from alternate-model")).toBeVisible();
    await expect(page.getByRole("button",{name:"Send"})).toBeVisible();
    expect(provider.models).toEqual(["primary-model","alternate-model"]);

    const firstProcess=application.process();
    const firstExit=new Promise<void>(resolve=>{if(firstProcess.exitCode!==null)resolve();else firstProcess.once("exit",()=>resolve())});
    await page.close();
    await firstExit;
    expect(await filesContaining(userData,secret,settingsPath)).toEqual([]);

    application=await launch();
    page=await application.firstWindow();
    await expect(page.getByRole("button",{name:"First model request",exact:true})).toBeVisible();
    await page.getByRole("button",{name:"First model request",exact:true}).click();
    await expect(page.getByRole("button",{name:"Session model"})).toContainText("alternate-model");
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
