import {type CSSProperties,FormEvent,useEffect,useMemo,useRef,useState} from "react";
import {ArrowUp,Check,CheckCircle2,ChevronDown,Copy,Ellipsis,Folder,FolderOpen,Hand,KeyRound,MessageSquarePlus,Pencil,Plus,RotateCcw,Settings,Shield,ShieldCheck,Square,Trash2,Undo2,X,XCircle} from "lucide-react";
import type {AppInfo,AppearancePreferences,CompletionReport,ConnectionUpsert,LanguagePreference,ModelConfigurationInfo,PermissionState,ProjectInfo,ProviderProfileInfo,SessionInfo,SidecarEvent,TranscriptEntry} from "../shared/protocol";
import {MarkdownText} from "./MarkdownText";
import {buildTimeline,MessageItem,ToolActivity} from "./ToolActivity";
import {SettingsPage} from "./SettingsPage";
import {localeLanguage,translate} from "./i18n";
import "./model-selector.css";

type PendingPermission={request_id:string;tool_name:string;risk:string;arguments:Record<string,unknown>};
type Connection="connected"|"recovering"|"disconnected";
type ComposerMenu="permission"|"model"|null;
type PermissionToast={previousMode:PermissionState["mode"]};
const MIN_SIDEBAR_WIDTH=220,MAX_SIDEBAR_WIDTH=480,DEFAULT_SIDEBAR_WIDTH=278;

export function App(){
 const [projects,setProjects]=useState<ProjectInfo[]>([]),[workspace,setWorkspace]=useState<string|null>(null),[sessions,setSessions]=useState<Record<string,SessionInfo[]>>({}),[expanded,setExpanded]=useState<Set<string>>(new Set()),[visible,setVisible]=useState<Record<string,number>>({});
 const [active,setActive]=useState<SessionInfo|null>(null),[permissions,setPermissions]=useState<PermissionState>({mode:"ask",approved_tools:[]}),[entries,setEntries]=useState<TranscriptEntry[]>([]);
 const [historyCursor,setHistoryCursor]=useState<number|null>(null),[hasOlder,setHasOlder]=useState(false);
 const [draft,setDraft]=useState(""),[stream,setStream]=useState(""),[busy,setBusy]=useState(false),[status,setStatus]=useState("Ready"),[error,setError]=useState<string|null>(null),[pending,setPending]=useState<PendingPermission|null>(null);
 const [connection,setConnection]=useState<Connection>("connected"),[completion,setCompletion]=useState<CompletionReport|null>(null),[renaming,setRenaming]=useState(false),[renameDraft,setRenameDraft]=useState("");
 const [showPermissions,setShowPermissions]=useState(false),[projectMenu,setProjectMenu]=useState<string|null>(null),[projectToRemove,setProjectToRemove]=useState<string|null>(null),[removingProject,setRemovingProject]=useState(false),[draftProject,setDraftProject]=useState<string|null>(null);
 const previousConnection=useRef(connection),eventSequence=useRef(0),viewport=useRef<HTMLDivElement|null>(null),loadingOlder=useRef(false),lastScrollTop=useRef(0),resizeStart=useRef<{x:number;width:number}|null>(null);
 const [showSettings,setShowSettings]=useState(false),[profiles,setProfiles]=useState<ProviderProfileInfo[]>([]),[modelConfig,setModelConfig]=useState<ModelConfigurationInfo|null>(null),[draftModelId,setDraftModelId]=useState("");
 const [appearance,setAppearance]=useState<AppearancePreferences>({theme:"system",density:"comfortable"}),[language,setLanguage]=useState<LanguagePreference>(()=>localeLanguage(navigator.language)),[sidebarWidth,setSidebarWidth]=useState(DEFAULT_SIDEBAR_WIDTH),[appInfo,setAppInfo]=useState<AppInfo|null>(null);
 const [composerMenu,setComposerMenu]=useState<ComposerMenu>(null),[permissionToast,setPermissionToast]=useState<PermissionToast|null>(null);
 const permissionToastTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
 const t=(key:Parameters<typeof translate>[1],values?:Record<string,string|number>)=>translate(language,key,values);
 const title=active?.title||t("newTask"),hasTask=Boolean(active||draftProject);

 useEffect(()=>{void Promise.all([refreshProjects(),refreshModels(),window.noval.getPreferences().then(preferences=>{applyAppearance(preferences.appearance);applyLanguage(preferences.language);setSidebarWidth(preferences.sidebarWidth)})]).catch(e=>setError(message(e)))},[]);
 useEffect(()=>window.noval.onEvent(handleEvent),[language]);
 useEffect(()=>{const recovered=previousConnection.current!=="connected"&&connection==="connected";previousConnection.current=connection;if(!recovered||!active)return;void restoreActive()},[connection,active?.session_id]);
 useEffect(()=>{if(!projectMenu&&!projectToRemove)return;function keydown(event:KeyboardEvent){if(event.key==="Escape"&&!removingProject){setProjectMenu(null);setProjectToRemove(null)}}function pointerdown(event:PointerEvent){const target=event.target;if(projectMenu&&target instanceof Element&&!target.closest(".project-menu")&&!target.closest(".project-actions-trigger"))setProjectMenu(null)}document.addEventListener("keydown",keydown);document.addEventListener("pointerdown",pointerdown);return()=>{document.removeEventListener("keydown",keydown);document.removeEventListener("pointerdown",pointerdown)}},[projectMenu,projectToRemove,removingProject]);
 useEffect(()=>{if(!composerMenu)return;function keydown(event:KeyboardEvent){if(event.key==="Escape")setComposerMenu(null)}function pointerdown(event:PointerEvent){const target=event.target;if(target instanceof Element&&!target.closest(".composer-menu-anchor"))setComposerMenu(null)}document.addEventListener("keydown",keydown);document.addEventListener("pointerdown",pointerdown);return()=>{document.removeEventListener("keydown",keydown);document.removeEventListener("pointerdown",pointerdown)}},[composerMenu]);
 useEffect(()=>()=>{if(permissionToastTimer.current)clearTimeout(permissionToastTimer.current)},[]);

 async function refreshProjects(){const list=await window.noval.listProjects();setProjects(list);const current=list.find(item=>item.active)?.path??null;setWorkspace(current);setExpanded(old=>{const next=new Set(old);if(current)next.add(current);return next});const pages=await Promise.all(list.map(async project=>[project.path,await window.noval.projectSessions(project.path)] as const));setSessions(Object.fromEntries(pages))}
 async function restoreActive(){if(!active)return;try{const result=await window.noval.resumeSession(active.session_id);setActive(result.session);setPermissions(result.permissions);const replay=await window.noval.replayEvents(active.session_id,eventSequence.current);eventSequence.current=replay.next_sequence;if(replay.gap_detected)setStatus(t("recoveredTranscript"));await loadLatest(active.session_id);setBusy(false);setStream("")}catch(e){setError(`${t("restoreTaskFailed")}: ${message(e)}`)}}
 function handleEvent(value:SidecarEvent){const e=value.event,p=value.payload as any;if(typeof p.sequence==="number")eventSequence.current=Math.max(eventSequence.current,p.sequence);if(e==="host.connection"){setConnection(p.state);if(p.state!=="connected")setPending(null);setStatus(p.state==="connected"?t("ready"):p.state==="recovering"?t("runtimeRecovering"):t("runtimeUnavailable"));return}if(e==="model.started"){setStatus(t("generating"));setStream("")}if(e==="model.output.delta")setStream(v=>v+String(p.text??p.delta??""));if(e==="tool.started")setStatus(`${t("running")} ${p.tool_name??t("tool")}`);if(e==="validation.started")setStatus(t("validating"));if(e==="permission.request"){setPending(p.request as PendingPermission);setStatus(t("waitingApproval"))}if(e==="turn.completed"||e==="turn.failed"){setPending(null);setStatus(e==="turn.completed"?t("ready"):t("turnFailed"))}}
 async function addProject(){try{const value=await window.noval.chooseWorkspace();if(!value)return;await refreshProjects();setWorkspace(value);setExpanded(old=>new Set(old).add(value));setActive(null);setDraftProject(null);setEntries([])}catch(e){setError(message(e))}}
 async function activateProject(path:string){await window.noval.activateProject(path);setWorkspace(path);setProjects(items=>items.map(item=>({...item,active:item.path===path})))}
 async function toggleProject(project:ProjectInfo){try{await activateProject(project.path);setExpanded(old=>{const next=new Set(old);next.has(project.path)?next.delete(project.path):next.add(project.path);return next});setActive(null);setDraftProject(null);setEntries([])}catch(e){setError(message(e))}}
 async function beginSession(path:string){try{await activateProject(path);setExpanded(old=>new Set(old).add(path));setActive(null);setDraftModelId(modelConfig?.default_model_id??"");setDraftProject(path);setPermissions({mode:"ask",approved_tools:[]});setPending(null);setEntries([]);setCompletion(null);setDraft("")}catch(e){setError(message(e))}}
 async function openSession(path:string,item:SessionInfo){try{setError(null);setCompletion(null);setPending(null);await activateProject(path);const result=active?.session_id===item.session_id?{session:active,permissions}:await window.noval.resumeSession(item.session_id);setActive(result.session);setDraftModelId(result.session.selected_model_id);setDraftProject(null);setPermissions(result.permissions);const replay=await window.noval.replayEvents(item.session_id,0);eventSequence.current=replay.next_sequence;await loadLatest(item.session_id)}catch(e){setError(message(e))}}
 async function removeProject(){const path=projectToRemove;if(!path||removingProject)return;setRemovingProject(true);try{const list=await window.noval.removeProject(path);setProjects(list);setSessions(old=>{const next={...old};delete next[path];return next});if(workspace===path){setWorkspace(list.find(item=>item.active)?.path??null);setActive(null);setDraftProject(null);setEntries([])}setProjectToRemove(null)}catch(e){setError(message(e))}finally{setRemovingProject(false)}}
 async function renameSession(){if(!active||!renameDraft.trim())return;try{const updated=await window.noval.renameSession(active.session_id,renameDraft.trim());setActive(updated);setSessions(old=>Object.fromEntries(Object.entries(old).map(([path,items])=>[path,items.map(item=>item.session_id===updated.session_id?updated:item)])));setRenaming(false)}catch(e){setError(message(e))}}
 async function send(event:FormEvent){event.preventDefault();const projectPath=active?.workdir??draftProject;if(!projectPath||!draft.trim()||busy||connection!=="connected")return;const text=draft.trim();let current=active;setDraft("");setBusy(true);setCompletion(null);try{if(!current){await activateProject(projectPath);const requestedPermissionMode=permissions.mode;const created=await window.noval.createSession(draftModelId?{selected_model_id:draftModelId}:{});current=created.session;setActive(current);setDraftModelId(current.selected_model_id);setDraftProject(null);const createdPermissions=requestedPermissionMode===created.permissions.mode?created.permissions:await window.noval.setPermissionMode(current.session_id,requestedPermissionMode);setPermissions(createdPermissions)}setEntries(old=>[...old,{sequence:Date.now(),role:"user",text,timestamp:null,tool_calls:[],tool_results:[]}]);scrollToBottom();const result=await window.noval.startTurn(current.session_id,text);setCompletion(result.completion);await loadLatest(current.session_id);setStream("")}catch(e){setError(message(e))}finally{const persisted=await window.noval.projectSessions(projectPath).catch(()=>sessions[projectPath]??[]);setSessions(old=>({...old,[projectPath]:persisted}));if(current){const stored=persisted.find(item=>item.session_id===current!.session_id);if(stored)setActive({...stored,is_open:true});else{setActive(null);setDraftProject(projectPath)}}setBusy(false);setStatus(t("ready"))}}
 async function loadLatest(sessionId:string){const page=await window.noval.transcriptHistory(sessionId);setEntries(page.entries);setHistoryCursor(page.previous_sequence);setHasOlder(page.has_more);scrollToBottom()}
 async function loadOlder(){if(!active||!hasOlder||historyCursor===null||loadingOlder.current)return;const element=viewport.current;if(!element)return;loadingOlder.current=true;const oldHeight=element.scrollHeight,oldTop=element.scrollTop;try{const page=await window.noval.transcriptHistory(active.session_id,historyCursor);setEntries(current=>{const known=new Set(current.map(item=>item.sequence));return [...page.entries.filter(item=>!known.has(item.sequence)),...current]});setHistoryCursor(page.previous_sequence);setHasOlder(page.has_more);window.requestAnimationFrame(()=>{if(viewport.current){viewport.current.scrollTop=viewport.current.scrollHeight-oldHeight+oldTop;lastScrollTop.current=viewport.current.scrollTop}loadingOlder.current=false})}catch(e){loadingOlder.current=false;setError(message(e))}}
 function scrollToBottom(){window.requestAnimationFrame(()=>{if(viewport.current){viewport.current.scrollTop=viewport.current.scrollHeight;lastScrollTop.current=viewport.current.scrollTop}})}
 function handleConversationScroll(element:HTMLDivElement){const current=element.scrollTop,movingUp=current<lastScrollTop.current;lastScrollTop.current=current;if(movingUp&&current<=32)void loadOlder()}
 function dismissPermissionToast(){if(permissionToastTimer.current)clearTimeout(permissionToastTimer.current);permissionToastTimer.current=null;setPermissionToast(null)}
 function showPermissionToast(previousMode:PermissionState["mode"]){dismissPermissionToast();setPermissionToast({previousMode});permissionToastTimer.current=setTimeout(()=>{setPermissionToast(null);permissionToastTimer.current=null},6000)}
 async function selectPermissionMode(next:PermissionState["mode"],notify=true){setComposerMenu(null);if(next===permissions.mode)return;const previousMode=permissions.mode;try{if(active)setPermissions(await window.noval.setPermissionMode(active.session_id,next));else setPermissions(current=>({...current,mode:next}));if(next==="full_access"&&notify)showPermissionToast(previousMode)}catch(e){setError(message(e))}}
 async function undoPermissionMode(){const previousMode=permissionToast?.previousMode;if(!previousMode)return;dismissPermissionToast();await selectPermissionMode(previousMode,false)}
 async function resolve(decision:"allow_once"|"allow_session"|"deny"){if(!pending)return;try{await window.noval.resolvePermission(pending.request_id,decision);setPending(null);setStatus(t("running"))}catch(e){setError(message(e))}}
 async function revokeTool(tool:string){if(active)try{setPermissions(await window.noval.revokeTool(active.session_id,tool))}catch(e){setError(message(e))}}
 async function resetPermissions(){if(!active||!confirm(t("resetPermissionsConfirm")))return;try{setPermissions(await window.noval.resetPermissions(active.session_id))}catch(e){setError(message(e))}}
 async function refreshModels(){const [profileList,configuration]=await Promise.all([window.noval.listProviderProfiles(),window.noval.getModelConfiguration()]);setProfiles(profileList);setModelConfig(configuration);setDraftModelId(current=>current||configuration.default_model_id)}
 async function openSettings(){try{const [,info,preferences]=await Promise.all([refreshModels(),window.noval.appInfo(),window.noval.getPreferences()]);setAppInfo(info);applyAppearance(preferences.appearance);applyLanguage(preferences.language);setShowSettings(true)}catch(e){setError(message(e))}}
 async function updateConnection(value:ConnectionUpsert){try{setModelConfig(await window.noval.upsertConnection(value))}catch(err){setError(message(err));throw err}}
 async function selectModel(id:string){setComposerMenu(null);if(!active){setDraftModelId(id);return}try{const updated=await window.noval.selectSessionModel(active.session_id,id);setActive(updated);setDraftModelId(updated.selected_model_id);setSessions(old=>Object.fromEntries(Object.entries(old).map(([path,items])=>[path,items.map(item=>item.session_id===updated.session_id?updated:item)])))}catch(err){setError(message(err))}}
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
               <button className="project-actions-trigger" aria-label={t("projectActions",{name:project.name})} aria-haspopup="menu" aria-expanded={menuOpen} onClick={()=>setProjectMenu(menuOpen?null:project.path)}><Ellipsis size={15}/></button>
               <button aria-label={t("newTaskIn",{name:project.name})} onClick={()=>beginSession(project.path)}><MessageSquarePlus size={15}/></button>
             </div>
             {menuOpen&&<div className="menu project-menu" role="menu" aria-label={t("actionsFor",{name:project.name})}>
               <button role="menuitem" onClick={()=>{void window.noval.revealProject(project.path);setProjectMenu(null)}}><FolderOpen size={16}/>{t("openExplorer")}</button>
               <button role="menuitem" className="danger" onClick={()=>{setProjectMenu(null);setProjectToRemove(project.path)}}><Trash2 size={16}/>{t("removeProject")}</button>
             </div>}
           </div>
           {open&&<div className="session-list">
             {items.slice(0,limit).map(item=><button key={item.session_id} className={`session-row ${active?.session_id===item.session_id?"active":""}`} onClick={()=>openSession(project.path,item)}><span>{item.title||t("untitledTask")}</span></button>)}
             {items.length>limit&&<button className="show-more" onClick={()=>setVisible(old=>({...old,[project.path]:limit+5}))}>{t("showMore",{count:Math.min(5,items.length-limit)})}</button>}
             {items.length===0&&<p className="no-sessions">{t("noTasks")}</p>}
           </div>}
         </section>;
       })}
       {projects.length===0&&<p className="no-projects">{t("addProjectHint")}</p>}
     </nav>
     <footer>
       <button className="settings-link" onClick={openSettings}><Settings size={16}/>{t("settings")}</button>
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
     <header className="topbar">
       {active?<div>{renaming?<form className="rename" onSubmit={event=>{event.preventDefault();void renameSession()}}>
         <input aria-label={t("taskTitle")} autoFocus value={renameDraft} onChange={event=>setRenameDraft(event.target.value)}/>
         <button aria-label={t("saveTitle")}><CheckCircle2 size={15}/></button>
         <button type="button" aria-label={t("cancelRename")} onClick={()=>setRenaming(false)}><XCircle size={15}/></button>
       </form>:<h1>{title}<button className="rename-trigger" aria-label={t("renameTask")} onClick={()=>{setRenameDraft(title);setRenaming(true)}}><Pencil size={12}/></button></h1>}<p>{workspace}</p></div>
       :draftProject?<div><h1>{t("newTask")}</h1><p>{draftProject}</p></div>
       :<div><h1>{workspace?leaf(workspace):"Noval"}</h1><p>{workspace||t("addProjectSidebar")}</p></div>}
     </header>
     {showPermissions&&active&&<section className="grants-panel">
       <header><div><strong>{t("sessionPermissions")}</strong><small>{t("runtimePermissions")}</small></div><button onClick={resetPermissions}><RotateCcw size={13}/>{t("resetAll")}</button></header>
       {permissions.approved_tools.length?<ul>{permissions.approved_tools.map(tool=><li key={tool}><code>{tool}</code><button onClick={()=>revokeTool(tool)}>{t("revoke")}</button></li>)}</ul>:<p>{t("noApprovedTools")}</p>}
     </section>}
     {!hasTask?<EmptyState projectName={workspace?leaf(workspace):null} language={language}/>:<>
       <div className="conversation-viewport" ref={viewport} onScroll={event=>handleConversationScroll(event.currentTarget)} onWheel={event=>{if(event.deltaY<0&&event.currentTarget.scrollTop<=32)void loadOlder()}}>
         {timeline.length===0&&!stream&&!completion?<EmptyState projectName={leaf(active?.workdir??draftProject!)} language={language}/>:<div className="conversation">
           {hasOlder&&<div className="history-loader" aria-label={t("olderMessages")}>{t("scrollOlder")}</div>}
           {timeline.map(item=>item.type==="message"?<ConversationMessage key={item.key} item={item} language={language}/>:<ToolActivity key={item.key} activity={item} language={language}/>)}
           {stream&&<article className="message message-assistant"><MarkdownText text={stream} streaming/></article>}
           {completion&&<CompletionCard report={completion} language={language}/>}
         </div>}
       </div>
       <form className="composer" onSubmit={send}>
         <textarea aria-label={t("messageNoval")} value={draft} onChange={event=>setDraft(event.target.value)} placeholder={t("composerPlaceholder")} disabled={connection!=="connected"} onKeyDown={event=>{if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();event.currentTarget.form?.requestSubmit()}}}/>
         <div className="composer-row">
           <div className="composer-controls">
             <div className="composer-menu-anchor">
               <button type="button" className={`composer-menu-trigger permission-selector ${permissions.mode}`} aria-label={t("sessionAccess")} aria-haspopup="menu" aria-expanded={composerMenu==="permission"} onClick={()=>{setShowPermissions(false);setComposerMenu(value=>value==="permission"?null:"permission")}}>
                 <Shield size={15}/><span>{permissions.mode==="full_access"?t("fullAccess"):t("askPermission")}</span><ChevronDown size={13}/>
               </button>
               {composerMenu==="permission"&&<div className="composer-menu permission-menu" role="menu" aria-label={t("sessionAccess")} onKeyDown={navigateComposerMenu}>
                 <button type="button" role="menuitemradio" aria-checked={permissions.mode==="ask"} onClick={()=>void selectPermissionMode("ask")}>
                   <Hand size={18}/><span><strong>{t("askPermission")}</strong><small>{t("askPermissionDescription")}</small></span>{permissions.mode==="ask"&&<Check size={17}/>}
                 </button>
                 <button type="button" className="full-access-option" role="menuitemradio" aria-checked={permissions.mode==="full_access"} onClick={()=>void selectPermissionMode("full_access")}>
                   <ShieldCheck size={18}/><span><strong>{t("fullAccess")}</strong><small>{t("fullAccessDescription")}</small></span>{permissions.mode==="full_access"&&<Check size={17}/>}
                 </button>
               </div>}
             </div>
             {active&&permissions.approved_tools.length>0&&<button type="button" className="grant-button" onClick={()=>setShowPermissions(value=>!value)}><KeyRound size={14}/>{t("grants",{count:permissions.approved_tools.length})}</button>}
             <span className="composer-status"><span className={`status-dot ${connection}`}/>{status}</span>
           </div>
           <div className="composer-actions">
             <div className="composer-menu-anchor model-menu-anchor">
               <button type="button" className="composer-menu-trigger model-selector" aria-label={t("sessionModel")} aria-haspopup="menu" aria-expanded={composerMenu==="model"} disabled={!modelConfig?.configured.length} onClick={()=>setComposerMenu(value=>value==="model"?null:"model")}>
                 <span>{selectedModel?.label??t("notConfigured")}</span>{busy&&<small>{t("nextTurn")}</small>}<ChevronDown size={13}/>
               </button>
               {composerMenu==="model"&&<div className="composer-menu model-menu" role="menu" aria-label={t("sessionModel")} onKeyDown={navigateComposerMenu}>
                 {modelConfig?.configured.map(item=><button type="button" key={item.id} role="menuitemradio" aria-checked={item.id===selectedModelId} onClick={()=>void selectModel(item.id)}>
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
 return <article className={`message message-${item.role}`}><MarkdownText text={item.text}/>{item.showMeta&&<footer className="message-meta">{displayTime&&<time dateTime={item.timestamp!} title={formatFullTimestamp(item.timestamp!,language)}>{displayTime}</time>}<button type="button" aria-label={translate(language,copied?"copied":"copyMessage")} onClick={copy}>{copied?<Check size={14}/>:<Copy size={14}/>}</button></footer>}</article>;
}
function CompletionCard({report,language}:{report:CompletionReport;language:LanguagePreference}){
 return <section className={`completion ${report.status}`} aria-label={translate(language,"completionEvidence")}><div><strong>{translate(language,report.status==="completed"?"verifiedComplete":report.status==="incomplete"?"incomplete":"completionUncertain")}</strong><small>{translate(language,"evidenceEvaluated")}</small></div>{report.criteria.length>0&&<ul>{report.criteria.map(item=><li key={item.criterion_id}><span>{item.criterion_id}</span><strong>{item.status}</strong><small>{item.source||translate(language,"noVerificationSource")}</small></li>)}</ul>}</section>;
}
function leaf(value:string){return value.split(/[\\/]/).filter(Boolean).at(-1)||value}function message(e:unknown){return e instanceof Error?e.message:"An unexpected error occurred."}function safePreview(v:unknown){try{return JSON.stringify(v,null,2).slice(0,1600)}catch{return "Details unavailable"}}
function formatTimestamp(value:string|null,language:LanguagePreference){if(!value)return "";const date=new Date(value);return Number.isNaN(date.valueOf())?"":new Intl.DateTimeFormat(language,{hour:"2-digit",minute:"2-digit"}).format(date)}
function formatFullTimestamp(value:string,language:LanguagePreference){const date=new Date(value);return Number.isNaN(date.valueOf())?value:new Intl.DateTimeFormat(language,{dateStyle:"medium",timeStyle:"short"}).format(date)}
