import {type CSSProperties,FormEvent,Fragment,useEffect,useMemo,useRef,useState} from "react";
import {ArrowUp,Check,ChevronDown,Copy,Ellipsis,Folder,FolderOpen,Hand,KeyRound,MessageSquarePlus,Pencil,Plus,RotateCcw,Settings,Shield,ShieldCheck,Square,Trash2,Undo2,X} from "lucide-react";
import type {AppInfo,AppearancePreferences,CompletionReport,ConnectionUpsert,LanguagePreference,ModelConfigurationInfo,PermissionState,ProjectInfo,ProviderProfileInfo,SessionInfo,SidecarEvent,TranscriptEntry,TurnError,UsageAnalytics} from "../shared/protocol";
import {MarkdownText} from "./MarkdownText";
import {buildTimeline,MessageItem,ToolActivity} from "./ToolActivity";
import {SettingsPage,type SettingsSection} from "./SettingsPage";
import {formatWorkDuration,localeLanguage,translate} from "./i18n";
import "./model-selector.css";

type PendingPermission={request_id:string;tool_name:string;risk:string;arguments:Record<string,unknown>};
type Connection="connected"|"recovering"|"disconnected";
type ComposerMenu="permission"|"model"|null;
type PermissionToast={previousMode:PermissionState["mode"]};
type SessionRenameTarget={projectPath:string;session:SessionInfo};
type RetryProgress={attempt:number;maxRetries:number};
const MIN_SIDEBAR_WIDTH=220,MAX_SIDEBAR_WIDTH=480,DEFAULT_SIDEBAR_WIDTH=278;

export function App(){
 const [projects,setProjects]=useState<ProjectInfo[]>([]),[workspace,setWorkspace]=useState<string|null>(null),[sessions,setSessions]=useState<Record<string,SessionInfo[]>>({}),[expanded,setExpanded]=useState<Set<string>>(new Set()),[visible,setVisible]=useState<Record<string,number>>({});
 const [active,setActive]=useState<SessionInfo|null>(null),[permissions,setPermissions]=useState<PermissionState>({mode:"ask",approved_tools:[]}),[entries,setEntries]=useState<TranscriptEntry[]>([]);
 const [historyCursor,setHistoryCursor]=useState<number|null>(null),[hasOlder,setHasOlder]=useState(false);
 const [draft,setDraft]=useState(""),[stream,setStream]=useState(""),[busy,setBusy]=useState(false),[status,setStatus]=useState("Ready"),[error,setError]=useState<string|null>(null),[pending,setPending]=useState<PendingPermission|null>(null);
 const [turnStartedAt,setTurnStartedAt]=useState<number|null>(null),[workedSeconds,setWorkedSeconds]=useState(0),[completedWorkSeconds,setCompletedWorkSeconds]=useState<number|null>(null);
 const [retryProgress,setRetryProgress]=useState<RetryProgress|null>(null),[turnFailure,setTurnFailure]=useState<TurnError|null>(null);
 const [connection,setConnection]=useState<Connection>("connected"),[completion,setCompletion]=useState<CompletionReport|null>(null);
 const [showPermissions,setShowPermissions]=useState(false),[projectMenu,setProjectMenu]=useState<string|null>(null),[projectToRemove,setProjectToRemove]=useState<string|null>(null),[removingProject,setRemovingProject]=useState(false),[draftProject,setDraftProject]=useState<string|null>(null);
 const [sessionMenu,setSessionMenu]=useState<string|null>(null),[sessionToRename,setSessionToRename]=useState<SessionRenameTarget|null>(null),[sessionTitle,setSessionTitle]=useState(""),[renamingSession,setRenamingSession]=useState(false);
 const previousConnection=useRef(connection),eventSequence=useRef(0),viewport=useRef<HTMLDivElement|null>(null),loadingOlder=useRef(false),lastScrollTop=useRef(0),followOutput=useRef(true),resizeStart=useRef<{x:number;width:number}|null>(null);
 const streamValue=useRef(""),streamRequestStart=useRef(0);
 const [showSettings,setShowSettings]=useState(false),[settingsSection,setSettingsSection]=useState<SettingsSection>("general"),[profiles,setProfiles]=useState<ProviderProfileInfo[]>([]),[modelConfig,setModelConfig]=useState<ModelConfigurationInfo|null>(null),[draftModelId,setDraftModelId]=useState("");
 const [appearance,setAppearance]=useState<AppearancePreferences>({theme:"system",density:"comfortable"}),[language,setLanguage]=useState<LanguagePreference>(()=>localeLanguage(navigator.language)),[sidebarWidth,setSidebarWidth]=useState(DEFAULT_SIDEBAR_WIDTH),[appInfo,setAppInfo]=useState<AppInfo|null>(null);
 const [usageAnalytics,setUsageAnalytics]=useState<UsageAnalytics|null>(null),[usageLoading,setUsageLoading]=useState(false),[usageError,setUsageError]=useState<string|null>(null);
 const [composerMenu,setComposerMenu]=useState<ComposerMenu>(null),[permissionToast,setPermissionToast]=useState<PermissionToast|null>(null);
 const permissionToastTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
 const t=(key:Parameters<typeof translate>[1],values?:Record<string,string|number>)=>translate(language,key,values);
 const title=active?.title||t("untitledTask"),canCompose=Boolean(active||draftProject||workspace);
 const workedFor=(seconds:number)=>t("workedFor",{duration:formatWorkDuration(language,seconds)});

 useEffect(()=>{void Promise.all([refreshProjects(),refreshModels(),window.noval.getPreferences().then(preferences=>{applyAppearance(preferences.appearance);applyLanguage(preferences.language);setSidebarWidth(preferences.sidebarWidth)})]).catch(e=>setError(message(e)))},[]);
 useEffect(()=>window.noval.onEvent(handleEvent),[language]);
 useEffect(()=>{if(turnStartedAt===null)return;const update=()=>setWorkedSeconds(elapsedSeconds(turnStartedAt));update();const timer=setInterval(update,1000);return()=>clearInterval(timer)},[turnStartedAt]);
 useEffect(()=>{if(followOutput.current&&(busy||stream))scrollToBottom()},[busy,stream]);
 useEffect(()=>{const recovered=previousConnection.current!=="connected"&&connection==="connected";previousConnection.current=connection;if(!recovered||!active)return;void restoreActive()},[connection,active?.session_id]);
 useEffect(()=>{if(!projectMenu&&!projectToRemove)return;function keydown(event:KeyboardEvent){if(event.key==="Escape"&&!removingProject){setProjectMenu(null);setProjectToRemove(null)}}function pointerdown(event:PointerEvent){const target=event.target;if(projectMenu&&target instanceof Element&&!target.closest(".project-menu")&&!target.closest(".project-actions-trigger"))setProjectMenu(null)}document.addEventListener("keydown",keydown);document.addEventListener("pointerdown",pointerdown);return()=>{document.removeEventListener("keydown",keydown);document.removeEventListener("pointerdown",pointerdown)}},[projectMenu,projectToRemove,removingProject]);
 useEffect(()=>{if(!sessionMenu&&!sessionToRename)return;function keydown(event:KeyboardEvent){if(event.key==="Escape"&&!renamingSession){setSessionMenu(null);setSessionToRename(null);setSessionTitle("")}}function pointerdown(event:PointerEvent){const target=event.target;if(sessionMenu&&target instanceof Element&&!target.closest(".session-menu")&&!target.closest(".session-actions-trigger"))setSessionMenu(null)}document.addEventListener("keydown",keydown);document.addEventListener("pointerdown",pointerdown);return()=>{document.removeEventListener("keydown",keydown);document.removeEventListener("pointerdown",pointerdown)}},[sessionMenu,sessionToRename,renamingSession]);
 useEffect(()=>{if(!composerMenu&&!showPermissions)return;function keydown(event:KeyboardEvent){if(event.key==="Escape"){setComposerMenu(null);setShowPermissions(false)}}function pointerdown(event:PointerEvent){const target=event.target;if(!(target instanceof Element))return;if(composerMenu&&!target.closest(".composer-menu-anchor"))setComposerMenu(null);if(showPermissions&&!target.closest(".grants-anchor"))setShowPermissions(false)}document.addEventListener("keydown",keydown);document.addEventListener("pointerdown",pointerdown);return()=>{document.removeEventListener("keydown",keydown);document.removeEventListener("pointerdown",pointerdown)}},[composerMenu,showPermissions]);
 useEffect(()=>()=>{if(permissionToastTimer.current)clearTimeout(permissionToastTimer.current)},[]);

 async function refreshProjects(){const list=await window.noval.listProjects();setProjects(list);const current=list.find(item=>item.active)?.path??null;setWorkspace(current);setExpanded(old=>{const next=new Set(old);if(current)next.add(current);return next});const pages=await Promise.all(list.map(async project=>[project.path,await window.noval.projectSessions(project.path)] as const));setSessions(Object.fromEntries(pages))}
 async function restoreActive(){if(!active)return;try{const result=await window.noval.resumeSession(active.session_id);setActive(result.session);setPermissions(result.permissions);const replay=await window.noval.replayEvents(active.session_id,eventSequence.current);eventSequence.current=replay.next_sequence;if(replay.gap_detected)setStatus(t("recoveredTranscript"));await loadLatest(active.session_id);setBusy(false);clearStream();setCompletedWorkSeconds(null)}catch(e){setError(`${t("restoreTaskFailed")}: ${message(e)}`)}}
 function handleEvent(value:SidecarEvent){const e=value.event,envelope=value.payload as Record<string,unknown>,p=envelope.type===e&&isRecord(envelope.payload)?envelope.payload:envelope;if(typeof envelope.sequence==="number")eventSequence.current=Math.max(eventSequence.current,envelope.sequence);if(e==="host.connection"){setConnection(String(p.state) as Connection);if(p.state!=="connected")setPending(null);setStatus(p.state==="connected"?t("ready"):p.state==="recovering"?t("runtimeRecovering"):t("runtimeUnavailable"));return}if(e==="model.started"){setRetryProgress(null);setStatus(t("generating"));streamRequestStart.current=streamValue.current.length;if(streamValue.current&&!streamValue.current.endsWith("\n\n")){streamValue.current+="\n\n";setStream(streamValue.current)}}if(e==="model.retrying"){const attempt=Number(p.attempt),maxRetries=Number(p.max_retries);if(Number.isFinite(attempt)&&Number.isFinite(maxRetries)){setRetryProgress({attempt,maxRetries});setStatus(t("retryingModel",{attempt,maxRetries}));streamRequestStart.current=streamValue.current.length;if(streamValue.current&&!streamValue.current.endsWith("\n\n")){streamValue.current+="\n\n";setStream(streamValue.current)}}}if(e==="model.output.delta"){setRetryProgress(null);streamValue.current+=String(p.text??p.delta??"");setStream(streamValue.current)}if(e==="model.output.aborted"){streamValue.current=streamValue.current.slice(0,streamRequestStart.current);setStream(streamValue.current)}if(e==="tool.started")setStatus(`${t("running")} ${p.tool_name??t("tool")}`);if(e==="validation.started")setStatus(t("validating"));if(e==="permission.request"){setPending(p.request as PendingPermission);setStatus(t("waitingApproval"))}if(e==="turn.completed"||e==="turn.failed"){setPending(null);setRetryProgress(null);if(e==="turn.failed"){const failure=turnError(p.error);if(failure)setTurnFailure(failure)}setStatus(e==="turn.completed"?t("ready"):t("turnFailed"))}}
 async function addProject(){try{const value=await window.noval.chooseWorkspace();if(!value)return;await refreshProjects();setWorkspace(value);setExpanded(old=>new Set(old).add(value));setActive(null);setDraftProject(null);setEntries([]);setCompletedWorkSeconds(null)}catch(e){setError(message(e))}}
 async function activateProject(path:string){await window.noval.activateProject(path);setWorkspace(path);setProjects(items=>items.map(item=>({...item,active:item.path===path})))}
 async function toggleProject(project:ProjectInfo){try{await activateProject(project.path);setExpanded(old=>{const next=new Set(old);next.has(project.path)?next.delete(project.path):next.add(project.path);return next});setActive(null);setDraftProject(null);setEntries([]);setCompletedWorkSeconds(null)}catch(e){setError(message(e))}}
 async function beginSession(path:string){try{await activateProject(path);setExpanded(old=>new Set(old).add(path));setActive(null);setDraftModelId(modelConfig?.default_model_id??"");setDraftProject(path);setPermissions({mode:"ask",approved_tools:[]});setShowPermissions(false);setPending(null);setEntries([]);setCompletion(null);setCompletedWorkSeconds(null);setTurnFailure(null);setRetryProgress(null);setDraft("")}catch(e){setError(message(e))}}
 async function openSession(path:string,item:SessionInfo){try{setError(null);setCompletion(null);setCompletedWorkSeconds(null);setTurnFailure(null);setRetryProgress(null);setPending(null);setShowPermissions(false);await activateProject(path);const result=active?.session_id===item.session_id?{session:active,permissions}:await window.noval.resumeSession(item.session_id),restored={...result.session,title:result.session.title??item.title};setActive(restored);setDraftModelId(restored.selected_model_id);setDraftProject(null);setPermissions(result.permissions);const replay=await window.noval.replayEvents(item.session_id,0);eventSequence.current=replay.next_sequence;await loadLatest(item.session_id)}catch(e){setError(message(e))}}
 async function removeProject(){const path=projectToRemove;if(!path||removingProject)return;setRemovingProject(true);try{const list=await window.noval.removeProject(path);setProjects(list);setSessions(old=>{const next={...old};delete next[path];return next});if(workspace===path){setWorkspace(list.find(item=>item.active)?.path??null);setActive(null);setDraftProject(null);setEntries([])}setProjectToRemove(null)}catch(e){setError(message(e))}finally{setRemovingProject(false)}}
 function openSessionRename(projectPath:string,session:SessionInfo){setSessionMenu(null);setSessionToRename({projectPath,session});setSessionTitle(session.title??"")}
 function closeSessionRename(){if(renamingSession)return;setSessionToRename(null);setSessionTitle("")}
 async function renameSelectedSession(){const target=sessionToRename,title=sessionTitle.trim();if(!target||!title||title===(target.session.title??"").trim()||renamingSession)return;setRenamingSession(true);try{const updated=await window.noval.renameSession(target.session.session_id,title);setSessions(old=>({...old,[target.projectPath]:(old[target.projectPath]??[]).map(item=>item.session_id===updated.session_id?updated:item)}));if(active?.session_id===updated.session_id)setActive(updated);setSessionToRename(null);setSessionTitle("")}catch(e){setError(message(e))}finally{setRenamingSession(false)}}
 async function send(event:FormEvent){event.preventDefault();const projectPath=active?.workdir??draftProject??workspace;if(!projectPath||!draft.trim()||busy||connection!=="connected")return;const text=draft.trim(),startedAt=Date.now(),optimisticSequence=-startedAt;let current=active,turnRequested=false,turnFailed=false;setDraft("");setBusy(true);setCompletion(null);setCompletedWorkSeconds(null);setTurnFailure(null);setRetryProgress(null);clearStream();setWorkedSeconds(0);setTurnStartedAt(startedAt);setStatus(t("thinking"));followOutput.current=true;setEntries(old=>[...old,{sequence:optimisticSequence,role:"user",text,timestamp:null,tool_calls:[],tool_results:[]}]);scrollToBottom();try{if(!current){await activateProject(projectPath);const requestedPermissionMode=permissions.mode;const created=await window.noval.createSession(draftModelId?{selected_model_id:draftModelId}:{});current=created.session;setActive(current);setDraftModelId(current.selected_model_id);setDraftProject(null);const createdPermissions=requestedPermissionMode===created.permissions.mode?created.permissions:await window.noval.setPermissionMode(current.session_id,requestedPermissionMode);setPermissions(createdPermissions)}turnRequested=true;const result=await window.noval.startTurn(current.session_id,text);turnFailed=result.status==="failed";setCompletion(result.completion);setTurnFailure(turnFailed&&result.error?result.error:null);await loadLatest(current.session_id);clearStream();setCompletedWorkSeconds(elapsedSeconds(startedAt))}catch(e){setError(message(e));clearStream();setCompletedWorkSeconds(null);if(current)await loadLatest(current.session_id).catch(()=>setEntries(old=>old.filter(item=>item.sequence!==optimisticSequence)));else setEntries(old=>old.filter(item=>item.sequence!==optimisticSequence));if(!turnRequested)setDraft(text)}finally{setTurnStartedAt(null);setRetryProgress(null);const persisted=await window.noval.projectSessions(projectPath).catch(()=>sessions[projectPath]??[]);setSessions(old=>({...old,[projectPath]:persisted}));if(current){const stored=persisted.find(item=>item.session_id===current!.session_id);if(stored)setActive({...stored,is_open:true});else{setActive(null);setDraftProject(projectPath)}}setBusy(false);setStatus(turnFailed?t("turnFailed"):t("ready"))}}
 function clearStream(){streamValue.current="";streamRequestStart.current=0;setStream("")}
 async function loadLatest(sessionId:string){const page=await window.noval.transcriptHistory(sessionId);setEntries(page.entries);setHistoryCursor(page.previous_sequence);setHasOlder(page.has_more);scrollToBottom()}
 async function loadOlder(){if(!active||!hasOlder||historyCursor===null||loadingOlder.current)return;const element=viewport.current;if(!element)return;loadingOlder.current=true;const oldHeight=element.scrollHeight,oldTop=element.scrollTop;try{const page=await window.noval.transcriptHistory(active.session_id,historyCursor);setEntries(current=>{const known=new Set(current.map(item=>item.sequence));return [...page.entries.filter(item=>!known.has(item.sequence)),...current]});setHistoryCursor(page.previous_sequence);setHasOlder(page.has_more);window.requestAnimationFrame(()=>{if(viewport.current){viewport.current.scrollTop=viewport.current.scrollHeight-oldHeight+oldTop;lastScrollTop.current=viewport.current.scrollTop}loadingOlder.current=false})}catch(e){loadingOlder.current=false;setError(message(e))}}
 function scrollToBottom(){window.requestAnimationFrame(()=>{if(viewport.current){viewport.current.scrollTop=viewport.current.scrollHeight;lastScrollTop.current=viewport.current.scrollTop}})}
 function handleConversationScroll(element:HTMLDivElement){const current=element.scrollTop,movingUp=current<lastScrollTop.current;lastScrollTop.current=current;followOutput.current=element.scrollHeight-element.clientHeight-current<=80;if(movingUp&&current<=32)void loadOlder()}
 function dismissPermissionToast(){if(permissionToastTimer.current)clearTimeout(permissionToastTimer.current);permissionToastTimer.current=null;setPermissionToast(null)}
 function showPermissionToast(previousMode:PermissionState["mode"]){dismissPermissionToast();setPermissionToast({previousMode});permissionToastTimer.current=setTimeout(()=>{setPermissionToast(null);permissionToastTimer.current=null},6000)}
 async function selectPermissionMode(next:PermissionState["mode"],notify=true){setComposerMenu(null);if(next===permissions.mode)return;const previousMode=permissions.mode;try{if(active)setPermissions(await window.noval.setPermissionMode(active.session_id,next));else setPermissions(current=>({...current,mode:next}));if(next==="full_access"&&notify)showPermissionToast(previousMode)}catch(e){setError(message(e))}}
 async function undoPermissionMode(){const previousMode=permissionToast?.previousMode;if(!previousMode)return;dismissPermissionToast();await selectPermissionMode(previousMode,false)}
 async function resolve(decision:"allow_once"|"allow_session"|"deny"){if(!pending)return;try{await window.noval.resolvePermission(pending.request_id,decision);setPending(null);setStatus(t("running"))}catch(e){setError(message(e))}}
 async function revokeTool(tool:string){if(active)try{setPermissions(await window.noval.revokeTool(active.session_id,tool))}catch(e){setError(message(e))}}
 async function resetPermissions(){if(!active||!confirm(t("resetPermissionsConfirm")))return;try{setPermissions(await window.noval.resetPermissions(active.session_id))}catch(e){setError(message(e))}}
 async function refreshModels(){const [profileList,configuration]=await Promise.all([window.noval.listProviderProfiles(),window.noval.getModelConfiguration()]);setProfiles(profileList);setModelConfig(configuration);setDraftModelId(current=>current||configuration.default_model_id)}
 async function loadUsageAnalytics(){setUsageLoading(true);setUsageError(null);try{setUsageAnalytics(await window.noval.getUsageAnalytics())}catch(e){setUsageError(message(e))}finally{setUsageLoading(false)}}
 async function openSettings(section:SettingsSection="general"){try{const [,info,preferences]=await Promise.all([refreshModels(),window.noval.appInfo(),window.noval.getPreferences()]);setAppInfo(info);applyAppearance(preferences.appearance);applyLanguage(preferences.language);setSettingsSection(section);setShowSettings(true);void loadUsageAnalytics()}catch(e){setError(message(e))}}
 async function updateConnection(value:ConnectionUpsert){try{setModelConfig(await window.noval.upsertConnection(value))}catch(err){setError(message(err));throw err}}
 async function selectModel(id:string){setComposerMenu(null);if(!active){setDraftModelId(id);return}try{const updated=await window.noval.selectSessionModel(active.session_id,id);setActive(updated);setDraftModelId(updated.selected_model_id);setSessions(old=>Object.fromEntries(Object.entries(old).map(([path,items])=>[path,items.map(item=>item.session_id===updated.session_id?updated:item)])))}catch(err){setError(message(err))}}
 function toggleComposerMenu(next:Exclude<ComposerMenu,null>){const opening=composerMenu!==next;if(opening&&next==="permission")dismissPermissionToast();setComposerMenu(opening?next:null)}
 function navigateComposerMenu(event:React.KeyboardEvent<HTMLDivElement>){if(!["ArrowDown","ArrowUp","Home","End"].includes(event.key))return;const items=Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]'));if(!items.length)return;event.preventDefault();const current=items.indexOf(document.activeElement as HTMLButtonElement);const next=event.key==="Home"?0:event.key==="End"?items.length-1:event.key==="ArrowDown"?(current+1+items.length)%items.length:(current-1+items.length)%items.length;items[next]?.focus()}
 async function saveAppearance(value:AppearancePreferences){try{const saved=await window.noval.saveAppearance(value);applyAppearance(saved)}catch(err){setError(message(err))}}
 async function saveLanguage(value:LanguagePreference){try{const saved=await window.noval.saveLanguage(value);applyLanguage(saved.language)}catch(err){setError(message(err))}}
 async function persistSidebarWidth(value:number){try{const saved=await window.noval.saveSidebarWidth(value);setSidebarWidth(saved.sidebarWidth)}catch(err){setError(message(err))}}
 function applyAppearance(value:AppearancePreferences){setAppearance(value);document.documentElement.dataset.theme=value.theme;document.documentElement.dataset.density=value.density}
 function applyLanguage(value:LanguagePreference){setLanguage(value);document.documentElement.lang=value}
 function resizeSidebar(value:number){const next=Math.min(MAX_SIDEBAR_WIDTH,Math.max(MIN_SIDEBAR_WIDTH,Math.round(value)));setSidebarWidth(next);return next}
 function startSidebarResize(event:React.PointerEvent<HTMLDivElement>){if(event.button!==0)return;resizeStart.current={x:event.clientX,width:sidebarWidth};event.currentTarget.setPointerCapture?.(event.pointerId)}
 function moveSidebarResize(event:React.PointerEvent<HTMLDivElement>){if(!resizeStart.current)return;resizeSidebar(resizeStart.current.width+event.clientX-resizeStart.current.x)}
 function finishSidebarResize(event:React.PointerEvent<HTMLDivElement>){const start=resizeStart.current;if(!start)return;resizeStart.current=null;event.currentTarget.releasePointerCapture?.(event.pointerId);void persistSidebarWidth(resizeSidebar(start.width+event.clientX-start.x))}
 function cancelSidebarResize(event:React.PointerEvent<HTMLDivElement>){if(!resizeStart.current)return;resizeStart.current=null;event.currentTarget.releasePointerCapture?.(event.pointerId);void persistSidebarWidth(sidebarWidth)}
 function resizeSidebarByKeyboard(event:React.KeyboardEvent<HTMLDivElement>){let next:number|undefined;if(event.key==="ArrowLeft")next=sidebarWidth-16;if(event.key==="ArrowRight")next=sidebarWidth+16;if(event.key==="Home")next=MIN_SIDEBAR_WIDTH;if(event.key==="End")next=MAX_SIDEBAR_WIDTH;if(next===undefined)return;event.preventDefault();void persistSidebarWidth(resizeSidebar(next))}

 const grouped=useMemo(()=>entries.filter(x=>x.text||x.tool_calls.length||x.tool_results.length),[entries]);
 const timeline=useMemo(()=>buildTimeline(grouped),[grouped]);
 const completedReplyKey=useMemo(()=>[...timeline].reverse().find(item=>item.type==="message"&&item.role==="assistant"&&item.showMeta)?.key??null,[timeline]);
 const selectedModelId=active?.selected_model_id||draftModelId||modelConfig?.default_model_id||"";
 const selectedModel=modelConfig?.configured.find(item=>item.id===selectedModelId);
 if(showSettings)return <SettingsPage
   profiles={profiles}
   models={modelConfig}
   upsertConnection={updateConnection}
   appearance={appearance}
   saveAppearance={saveAppearance}
   language={language}
   saveLanguage={saveLanguage}
   appInfo={appInfo}
   workspace={workspace}
   projectCount={projects.length}
   sessionCount={Object.values(sessions).reduce((total,items)=>total+items.length,0)}
   close={()=>setShowSettings(false)}
   error={error}
   dismissError={()=>setError(null)}
   usage={usageAnalytics}
   usageLoading={usageLoading}
   usageError={usageError}
   reloadUsage={()=>void loadUsageAnalytics()}
   initialSection={settingsSection}
 />;
 return <div className="shell" style={{"--sidebar-width":`${sidebarWidth}px`} as CSSProperties}>
   <aside className="sidebar project-sidebar">
     <header className="brand"><strong>Noval</strong></header>
     <div className="project-heading"><span>{t("projects")}</span><div><button aria-label={t("addProject")} onClick={addProject}><Plus size={17}/></button></div></div>
     <nav className="project-tree" aria-label={t("projects")}>
       {projects.map(project=>{
         const items=sessions[project.path]??[],limit=visible[project.path]??5,open=expanded.has(project.path),menuOpen=projectMenu===project.path;
         return <section className="project-node" key={project.path}>
           <div className={`project-row ${project.active?"current":""}`}>
             <button className="project-main" onClick={()=>toggleProject(project)} title={project.path}>{open?<FolderOpen size={17}/>:<Folder size={17}/>}<span>{project.name}</span></button>
             <div className="hover-actions">
               <button className="project-actions-trigger" aria-label={t("projectActions",{name:project.name})} aria-haspopup="menu" aria-expanded={menuOpen} onClick={()=>{setSessionMenu(null);setProjectMenu(menuOpen?null:project.path)}}><Ellipsis size={15}/></button>
               <button aria-label={t("newTaskIn",{name:project.name})} onClick={()=>beginSession(project.path)}><MessageSquarePlus size={15}/></button>
             </div>
             {menuOpen&&<div className="menu project-menu" role="menu" aria-label={t("actionsFor",{name:project.name})}>
               <button role="menuitem" onClick={()=>{void window.noval.revealProject(project.path);setProjectMenu(null)}}><FolderOpen size={16}/>{t("openExplorer")}</button>
               <button role="menuitem" className="danger" onClick={()=>{setProjectMenu(null);setProjectToRemove(project.path)}}><Trash2 size={16}/>{t("removeProject")}</button>
             </div>}
           </div>
           {open&&<div className="session-list">
             {items.slice(0,limit).map(item=>{const selected=active?.session_id===item.session_id,name=item.title||t("untitledTask"),menuOpen=sessionMenu===item.session_id;return <div key={item.session_id} className={`session-item ${selected?"active":""}`}>
               <button className="session-row" aria-current={selected?"page":undefined} onClick={()=>openSession(project.path,item)}><span>{name}</span></button>
               <button type="button" className="session-actions-trigger" aria-label={t("sessionActions",{name})} aria-haspopup="menu" aria-expanded={menuOpen} onClick={()=>{setProjectMenu(null);setSessionMenu(menuOpen?null:item.session_id)}}><Ellipsis size={14}/></button>
               {menuOpen&&<div className="menu project-menu session-menu" role="menu" aria-label={t("actionsFor",{name})}>
                 <button role="menuitem" onClick={()=>openSessionRename(project.path,item)}><Pencil size={15}/>{t("renameTask")}</button>
               </div>}
             </div>})}
             {items.length>limit&&<button className="show-more" onClick={()=>setVisible(old=>({...old,[project.path]:limit+5}))}>{t("showMore",{count:Math.min(5,items.length-limit)})}</button>}
             {items.length===0&&<p className="no-sessions">{t("noTasks")}</p>}
           </div>}
         </section>;
       })}
       {projects.length===0&&<p className="no-projects">{t("addProjectHint")}</p>}
     </nav>
     <footer>
       <button className="settings-link" onClick={()=>void openSettings()}><Settings size={16}/>{t("settings")}</button>
       <span className="connection"><span className={`status-dot ${connection}`}/>{connection==="connected"?t("runtimeConnected"):connection==="recovering"?t("runtimeRecovering"):t("runtimeUnavailable")}</span>
     </footer>
   </aside>
   <div
     className="sidebar-resizer"
     role="separator"
     aria-label={t("resizeSidebar")}
     aria-orientation="vertical"
     aria-valuemin={MIN_SIDEBAR_WIDTH}
     aria-valuemax={MAX_SIDEBAR_WIDTH}
     aria-valuenow={sidebarWidth}
     tabIndex={0}
     onPointerDown={startSidebarResize}
     onPointerMove={moveSidebarResize}
     onPointerUp={finishSidebarResize}
     onPointerCancel={cancelSidebarResize}
     onKeyDown={resizeSidebarByKeyboard}
   />
   <main className="workspace-pane">
     {active&&<header className="topbar"><h1>{title}</h1></header>}
      {!canCompose?<EmptyState projectName={null} language={language}/>:<>
       <div className="conversation-viewport" ref={viewport} onScroll={event=>handleConversationScroll(event.currentTarget)} onWheel={event=>{if(event.deltaY<0&&event.currentTarget.scrollTop<=32)void loadOlder()}}>
         {timeline.length===0&&!stream&&!completion&&!turnFailure?<EmptyState projectName={leaf(active?.workdir??draftProject??workspace!)} language={language}/>:<div className="conversation">
           {hasOlder&&<div className="history-loader" aria-label={t("olderMessages")}>{t("scrollOlder")}</div>}
            {timeline.map(item=><Fragment key={item.key}>{!busy&&completedWorkSeconds!==null&&item.key===completedReplyKey&&<div className="turn-elapsed">{workedFor(completedWorkSeconds)}</div>}{item.type==="message"?<ConversationMessage item={item} language={language}/>:<ToolActivity activity={item} language={language}/>}</Fragment>)}
            {busy&&<div className="turn-progress" role="status" aria-live="polite"><span className="thinking-dot"/><strong>{retryProgress?t("retryingModel",{attempt:retryProgress.attempt,maxRetries:retryProgress.maxRetries}):stream?t("responding"):t("thinking")}</strong><small>{workedFor(workedSeconds)}</small></div>}
            {stream&&<article className="message message-assistant"><div className="message-content"><MarkdownText text={stream} streaming/></div></article>}
            {!busy&&completedWorkSeconds!==null&&!completedReplyKey&&<div className="turn-elapsed">{workedFor(completedWorkSeconds)}</div>}
           {turnFailure&&<TurnFailureCard error={turnFailure} language={language} openModelSettings={()=>void openSettings("models")}/>}
           {completion&&<CompletionCard report={completion} language={language}/>}
         </div>}
       </div>
       <form className="composer" onSubmit={send}>
         <textarea aria-label={t("messageNoval")} value={draft} onChange={event=>setDraft(event.target.value)} placeholder={t("composerPlaceholder")} disabled={connection!=="connected"} onKeyDown={event=>{if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();event.currentTarget.form?.requestSubmit()}}}/>
         <div className="composer-row">
           <div className="composer-controls">
             <div className="composer-menu-anchor">
               <button type="button" className={`composer-menu-trigger permission-selector ${permissions.mode}`} aria-label={t("sessionAccess")} aria-haspopup="menu" aria-expanded={composerMenu==="permission"} onClick={()=>{setShowPermissions(false);toggleComposerMenu("permission")}}>
                 <Shield size={15}/><span>{permissions.mode==="full_access"?t("fullAccess"):t("askPermission")}</span>
               </button>
               {composerMenu==="permission"&&<div className="composer-menu permission-menu" role="menu" aria-label={t("sessionAccess")} onKeyDown={navigateComposerMenu}>
                 <div className="composer-menu-header">{t("accessMenuTitle")}</div>
                 <button type="button" role="menuitemradio" aria-checked={permissions.mode==="ask"} autoFocus={permissions.mode==="ask"} onClick={()=>void selectPermissionMode("ask")}>
                    <Hand size={18}/><span className="permission-choice-copy"><strong>{t("askPermission")}</strong><small>{t("askPermissionDescription")}</small></span>{permissions.mode==="ask"&&<Check size={17}/>}
                  </button>
                  <button type="button" className="full-access-option" role="menuitemradio" aria-checked={permissions.mode==="full_access"} autoFocus={permissions.mode==="full_access"} onClick={()=>void selectPermissionMode("full_access")}>
                    <ShieldCheck size={18}/><span className="permission-choice-copy"><strong>{t("fullAccess")}</strong><small>{t("fullAccessDescription")}</small></span>{permissions.mode==="full_access"&&<Check size={17}/>}
                  </button>
                </div>}
              </div>
              {active&&permissions.approved_tools.length>0&&<div className="grants-anchor">
                <button type="button" className="grant-button" aria-haspopup="dialog" aria-expanded={showPermissions} onClick={()=>setShowPermissions(value=>!value)}><KeyRound size={14}/><span>{t("grants",{count:permissions.approved_tools.length})}</span></button>
                {showPermissions&&<section className="grants-panel" role="dialog" aria-label={t("sessionPermissions")}>
                  <header><div><strong>{t("sessionPermissions")}</strong><small>{t("runtimePermissions")}</small></div><div className="grants-panel-actions"><button onClick={resetPermissions}><RotateCcw size={13}/>{t("resetAll")}</button><button className="grants-close" aria-label={t("hidePermissions")} onClick={()=>setShowPermissions(false)}><X size={14}/></button></div></header>
                  {permissions.approved_tools.length?<ul>{permissions.approved_tools.map(tool=><li key={tool}><code>{tool}</code><button onClick={()=>revokeTool(tool)}>{t("revoke")}</button></li>)}</ul>:<p>{t("noApprovedTools")}</p>}
                </section>}
              </div>}
             {(busy||connection!=="connected")&&<span className="composer-status"><span className={`status-dot ${connection}`}/>{status}</span>}
           </div>
           <div className="composer-actions">
             <div className="composer-menu-anchor model-menu-anchor">
               <button type="button" className="composer-menu-trigger model-selector" aria-label={t("sessionModel")} aria-haspopup="menu" aria-expanded={composerMenu==="model"} disabled={!modelConfig?.configured.length} onClick={()=>toggleComposerMenu("model")}>
                 <span>{selectedModel?.label??t("notConfigured")}</span>{busy&&<small>{t("nextTurn")}</small>}<ChevronDown size={13}/>
               </button>
               {composerMenu==="model"&&<div className="composer-menu model-menu" role="menu" aria-label={t("sessionModel")} onKeyDown={navigateComposerMenu}>
                 {modelConfig?.configured.map(item=><button type="button" key={item.id} role="menuitemradio" aria-checked={item.id===selectedModelId} autoFocus={item.id===selectedModelId} onClick={()=>void selectModel(item.id)}>
                   <span><strong>{item.label}</strong></span>{item.id===selectedModelId&&<Check size={17}/>}
                 </button>)}
               </div>}
             </div>
             {busy?<button type="button" className="send stop" aria-label={t("stop")} onClick={()=>active&&window.noval.cancelTurn(active.session_id)}><Square size={13}/></button>:<button className="send" disabled={!draft.trim()||connection!=="connected"} aria-label={t("send")}><ArrowUp size={17}/></button>}
           </div>
         </div>
       </form>
     </>}
     {permissionToast&&<div className="permission-toast" role="status" aria-live="polite">
       <ShieldCheck size={18}/><div><strong>{t("fullAccessEnabled")}</strong><span>{t("fullAccessToastDescription")}</span></div>
       <button type="button" className="toast-undo" onClick={()=>void undoPermissionMode()}><Undo2 size={14}/>{t("undo")}</button>
       <button type="button" className="toast-dismiss" aria-label={t("dismissToast")} onClick={dismissPermissionToast}><X size={15}/></button>
     </div>}
     {error&&<div className="error" role="alert"><strong>{t("attention")}</strong><span>{error}</span><button onClick={()=>setError(null)}>{t("dismiss")}</button></div>}
     {pending&&<div className="scrim"><section className="permission">
       <div className="permission-icon"><KeyRound size={20}/></div><p className="eyebrow">{t("permissionRequired")}</p><h2>{t("allowTool",{tool:pending.tool_name})}</h2><p>{t("actionApproval",{risk:pending.risk})}</p><pre>{safePreview(pending.arguments)}</pre>
       <div className="actions"><button onClick={()=>resolve("deny")}>{t("deny")}</button><button onClick={()=>resolve("allow_session")}>{t("allowSession")}</button><button className="primary" onClick={()=>resolve("allow_once")}>{t("allowOnce")}</button></div>
     </section></div>}
     {projectToRemove&&<div className="scrim project-remove-scrim" onPointerDown={event=>{if(event.target===event.currentTarget&&!removingProject)setProjectToRemove(null)}}>
       <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="remove-project-title">
         <button className="dialog-close" aria-label={t("closeRemoveDialog")} disabled={removingProject} onClick={()=>setProjectToRemove(null)}><X size={18}/></button>
         <h2 id="remove-project-title">{t("removeProjectQuestion",{name:leaf(projectToRemove)})}</h2><p>{t("removeProjectDetail")}</p>
         <div className="dialog-actions"><button autoFocus disabled={removingProject} onClick={()=>setProjectToRemove(null)}>{t("cancel")}</button><button className="danger-action" disabled={removingProject} onClick={removeProject}>{removingProject?t("removing"):t("remove")}</button></div>
       </section>
     </div>}
     {sessionToRename&&<div className="scrim project-remove-scrim" onPointerDown={event=>{if(event.target===event.currentTarget)closeSessionRename()}}>
       <section className="confirm-dialog session-rename-dialog" role="dialog" aria-modal="true" aria-labelledby="rename-session-title">
         <button className="dialog-close" aria-label={t("cancelRename")} disabled={renamingSession} onClick={closeSessionRename}><X size={18}/></button>
         <h2 id="rename-session-title">{t("renameTask")}</h2>
         <p>{t("renameTaskDescription")}</p>
         <form onSubmit={event=>{event.preventDefault();void renameSelectedSession()}}>
           <label htmlFor="session-title-input">{t("taskTitle")}</label>
           <input id="session-title-input" autoFocus value={sessionTitle} onChange={event=>setSessionTitle(event.target.value)} disabled={renamingSession}/>
           <div className="dialog-actions"><button type="button" disabled={renamingSession} onClick={closeSessionRename}>{t("cancel")}</button><button className="primary-action" disabled={!sessionTitle.trim()||sessionTitle.trim()===(sessionToRename.session.title??"").trim()||renamingSession}>{t("saveTitle")}</button></div>
         </form>
       </section>
     </div>}
   </main>
 </div>;
}

function EmptyState({projectName,language}:{projectName:string|null;language:LanguagePreference}){
 const [before,after]=translate(language,"emptyProject",{name:"__PROJECT__"}).split("__PROJECT__");
 return <section className="empty-state"><h2 aria-label={projectName?translate(language,"emptyProject",{name:projectName}):undefined}>{projectName
   ?<>{before}<span>{projectName}</span>{after}</>
   :translate(language,"emptyNoProject")}</h2></section>;
}
function ConversationMessage({item,language}:{item:MessageItem;language:LanguagePreference}){
 const [copied,setCopied]=useState(false),displayTime=formatTimestamp(item.timestamp,language);
 async function copy(){await window.noval.copyText(item.text);setCopied(true);window.setTimeout(()=>setCopied(false),1400)}
 return <article className={`message message-${item.role}`}><div className="message-content"><MarkdownText text={item.text}/></div>{item.showMeta&&<footer className="message-meta">{displayTime&&<time dateTime={item.timestamp!} title={formatFullTimestamp(item.timestamp!,language)}>{displayTime}</time>}<button type="button" aria-label={translate(language,copied?"copied":"copyMessage")} onClick={copy}>{copied?<Check size={14}/>:<Copy size={14}/>}</button></footer>}</article>;
}
function TurnFailureCard({error,language,openModelSettings}:{error:TurnError;language:LanguagePreference;openModelSettings:()=>void}){
 const t=(key:Parameters<typeof translate>[1])=>translate(language,key);
 const status=Number(error.details.status_code),code=`${error.code} ${error.details.provider_code??""} ${error.safe_message}`.toLowerCase();
 const billing=status===402||/\b(balance|billing|credit|funds?|insufficient|payment|quota)\b/.test(code);
 const authentication=error.code==="provider_authentication"||status===401||status===403;
 const title=authentication?t("modelAuthenticationFailed"):billing?t("modelBillingFailed"):t("modelRequestFailed");
 const help=authentication?t("modelAuthenticationHelp"):billing?t("modelBillingHelp"):error.retryable?t("modelRetriesExhausted"):t("modelRequestHelp");
 return <section className="turn-failure" role="alert" aria-label={title}>
   <strong>{title}</strong><p>{help}</p><small>{error.safe_message}</small>
   {authentication&&<button type="button" onClick={openModelSettings}>{t("openModelSettings")}</button>}
 </section>;
}
function CompletionCard({report,language}:{report:CompletionReport;language:LanguagePreference}){
 return <section className={`completion ${report.status}`} aria-label={translate(language,"completionEvidence")}><div><strong>{translate(language,report.status==="completed"?"verifiedComplete":report.status==="incomplete"?"incomplete":"completionUncertain")}</strong><small>{translate(language,"evidenceEvaluated")}</small></div>{report.criteria.length>0&&<ul>{report.criteria.map(item=><li key={item.criterion_id}><span>{item.criterion_id}</span><strong>{item.status}</strong><small>{item.source||translate(language,"noVerificationSource")}</small></li>)}</ul>}</section>;
}
function leaf(value:string){return value.split(/[\\/]/).filter(Boolean).at(-1)||value}function message(e:unknown){return e instanceof Error?e.message:"An unexpected error occurred."}function safePreview(v:unknown){try{return JSON.stringify(v,null,2).slice(0,1600)}catch{return "Details unavailable"}}function isRecord(value:unknown):value is Record<string,unknown>{return Boolean(value)&&typeof value==="object"&&!Array.isArray(value)}function turnError(value:unknown):TurnError|null{if(!isRecord(value)||typeof value.code!=="string"||typeof value.safe_message!=="string")return null;return{code:value.code,safe_message:value.safe_message,retryable:value.retryable===true,details:isRecord(value.details)?value.details:{}}}
function elapsedSeconds(startedAt:number){return Math.max(0,Math.floor((Date.now()-startedAt)/1000))}
function formatTimestamp(value:string|null,language:LanguagePreference){if(!value)return "";const date=new Date(value);return Number.isNaN(date.valueOf())?"":new Intl.DateTimeFormat(language,{hour:"2-digit",minute:"2-digit"}).format(date)}
function formatFullTimestamp(value:string,language:LanguagePreference){const date=new Date(value);return Number.isNaN(date.valueOf())?value:new Intl.DateTimeFormat(language,{dateStyle:"medium",timeStyle:"short"}).format(date)}
