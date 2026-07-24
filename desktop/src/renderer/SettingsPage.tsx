import {FormEvent,useEffect,useMemo,useState} from "react";
import {ArrowLeft,Check,ChevronRight,CircleUserRound,Cpu,FolderKanban,KeyRound,MonitorCog,Moon,Palette,Plus,ServerCog,Settings2,ShieldCheck,Sun,SunMoon,Trash2} from "lucide-react";
import type {AppInfo,AppearancePreferences,ConfiguredModelUpsert,ConnectionUpsert,ModelConfigurationInfo,ProviderProfileInfo} from "../shared/protocol";
import {API_SCHEMA_VERSION} from "../shared/protocol";

type Section="models"|"profile"|"appearance";
type Props={
 profiles:ProviderProfileInfo[];models:ModelConfigurationInfo|null;
 upsertConnection:(value:ConnectionUpsert)=>Promise<void>;deleteConnection:(id:string)=>Promise<void>;
 upsertConfiguredModel:(value:ConfiguredModelUpsert)=>Promise<void>;deleteConfiguredModel:(id:string)=>Promise<void>;
 setDefaultModel:(id:string)=>Promise<void>;
 appearance:AppearancePreferences;saveAppearance:(value:AppearancePreferences)=>Promise<void>;
 appInfo:AppInfo|null;workspace:string|null;projectCount:number;sessionCount:number;
 close:()=>void;error:string|null;dismissError:()=>void;
};

export function SettingsPage(props:Props){
 const [section,setSection]=useState<Section>("models");
 return <div className="settings-shell" data-testid="settings-shell">
  <aside className="settings-sidebar">
   <button className="settings-back" onClick={props.close}><ArrowLeft size={14}/>Back to Noval</button>
   <div className="settings-brand"><span>Noval</span><strong>Settings</strong></div>
   <nav aria-label="Settings sections">
    <p>Desktop</p>
    <NavButton active={section==="models"} icon={<Settings2 size={15}/>} onClick={()=>setSection("models")}>Models</NavButton>
    <NavButton active={section==="profile"} icon={<CircleUserRound size={15}/>} onClick={()=>setSection("profile")}>Profile</NavButton>
    <NavButton active={section==="appearance"} icon={<Palette size={15}/>} onClick={()=>setSection("appearance")}>Appearance</NavButton>
   </nav>
   <footer><ShieldCheck size={14}/><span>Runtime owns model configuration.</span></footer>
  </aside>
  <main className="settings-content">
   {props.error&&<div className="settings-error" role="alert"><span>{props.error}</span><button onClick={props.dismissError}>Dismiss</button></div>}
   {section==="models"&&<ModelSettings {...props}/>}
   {section==="profile"&&<ProfileSettings {...props}/>}
   {section==="appearance"&&<AppearanceSettings appearance={props.appearance} saveAppearance={props.saveAppearance}/>}
  </main>
 </div>
}

function NavButton({active,icon,onClick,children}:{active:boolean;icon:React.ReactNode;onClick:()=>void;children:React.ReactNode}){
 return <button className={active?"active":""} aria-current={active?"page":undefined} onClick={onClick}><span>{icon}{children}</span><ChevronRight size={13}/></button>
}

function ModelSettings(props:Props){
 const configuration=props.models;
 const [connectionId,setConnectionId]=useState(configuration?.connections[0]?.id??"new"),[profileId,setProfileId]=useState("deepseek"),[connectionLabel,setConnectionLabel]=useState(""),[baseUrl,setBaseUrl]=useState(""),[apiKeyEnv,setApiKeyEnv]=useState(""),[apiKey,setApiKey]=useState(""),[clearKey,setClearKey]=useState(false);
 const [modelConnectionId,setModelConnectionId]=useState(configuration?.connections[0]?.id??""),[modelLabel,setModelLabel]=useState(""),[providerModel,setProviderModel]=useState(""),[saving,setSaving]=useState(false),[saved,setSaved]=useState("");
 const connection=configuration?.connections.find(item=>item.id===connectionId);
 const profile=props.profiles.find(item=>item.id===profileId);
 const modelConnection=configuration?.connections.find(item=>item.id===modelConnectionId);
 const modelProfile=props.profiles.find(item=>item.id===modelConnection?.profile_id);
 useEffect(()=>{if(!configuration)return;if(!connectionId||(connectionId!=="new"&&!configuration.connections.some(item=>item.id===connectionId)))setConnectionId(configuration.connections[0]?.id??"new");if(!modelConnectionId)setModelConnectionId(configuration.connections[0]?.id??"")},[configuration?.revision]);
 useEffect(()=>{if(connection){setProfileId(connection.profile_id);setConnectionLabel(connection.label);setBaseUrl(connection.base_url);setApiKeyEnv(connection.api_key_env)}else{setConnectionLabel("");const initial=props.profiles.find(item=>item.id===profileId);setBaseUrl("");setApiKeyEnv("");if(initial?.kind==="builtin")setConnectionLabel(initial.label)}setApiKey("");setClearKey(false)},[connectionId]);
 useEffect(()=>{if(!modelProfile)return;const first=modelProfile.default_model??modelProfile.models[0]?.id??"";setProviderModel(first);setModelLabel(modelProfile.models.find(item=>item.id===first)?.label??first)},[modelConnectionId,modelProfile?.id]);
 function chooseProfile(value:string){setProfileId(value);const next=props.profiles.find(item=>item.id===value);if(connectionId==="new"){setConnectionLabel(next?.label??"");setBaseUrl("");setApiKeyEnv("")}}
 async function saveConnection(event:FormEvent){event.preventDefault();if(!configuration)return;setSaving(true);setSaved("");try{await props.upsertConnection({
   schema_version:API_SCHEMA_VERSION,expected_configuration_revision:configuration.revision,
   connection_id:connection?.id,expected_connection_revision:connection?.revision,
   label:connectionLabel.trim(),profile_id:profileId,
   base_url:profile?.kind==="custom"?baseUrl.trim():undefined,
   api_key_env:profile?.kind==="custom"?apiKeyEnv.trim():undefined,
   api_key:apiKey.trim()||undefined,clear_api_key:clearKey,
  });setApiKey("");setClearKey(false);setSaved("Connection saved without restarting the Runtime.")}catch{}finally{setSaving(false)}}
 async function addModel(event:FormEvent){event.preventDefault();if(!configuration)return;setSaving(true);setSaved("");try{await props.upsertConfiguredModel({schema_version:API_SCHEMA_VERSION,expected_configuration_revision:configuration.revision,label:modelLabel.trim(),connection_id:modelConnectionId,model:providerModel.trim()});setSaved("Configured Model added.")}catch{}finally{setSaving(false)}}
 return <section className="settings-page">
  <PageHeader eyebrow="RUNTIME" title="Models" description="Manage OpenAI-compatible Connections and reusable model selections. Changes apply to the next Turn without restarting the Runtime."/>
  <SettingsGroup title="Configured Models">
   <div className="model-list">{configuration?.configured.map(model=>{const itemConnection=configuration.connections.find(item=>item.id===model.connection_id);return <div className="model-row" key={model.id}>
    <button type="button" className="model-default" aria-pressed={model.id===configuration.default_model_id} onClick={()=>void props.setDefaultModel(model.id).catch(()=>{})}><span>{model.label}<small>{model.model} via {itemConnection?.label??"Unknown Connection"}</small></span>{model.id===configuration.default_model_id?<strong><Check size={13}/>Default</strong>:<em>Set default</em>}</button>
    <button type="button" aria-label={`Delete ${model.label}`} disabled={configuration.configured.length===1} onClick={()=>void props.deleteConfiguredModel(model.id).catch(()=>{})}><Trash2 size={14}/></button>
   </div>})}</div>
   <form className="model-add" onSubmit={addModel}>
    <select aria-label="Model Connection" value={modelConnectionId} onChange={event=>setModelConnectionId(event.target.value)}>{configuration?.connections.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select>
    <input aria-label="Configured Model label" required value={modelLabel} onChange={event=>setModelLabel(event.target.value)} placeholder="Display label"/>
    {modelProfile?.kind==="builtin"?<select aria-label="Provider model" value={providerModel} onChange={event=>{setProviderModel(event.target.value);const item=modelProfile.models.find(model=>model.id===event.target.value);if(item)setModelLabel(item.label)}}>{modelProfile.models.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select>:<input aria-label="Provider model" required value={providerModel} onChange={event=>setProviderModel(event.target.value)} placeholder="Model identifier"/>}
    <button className="settings-primary" disabled={saving||!modelConnectionId}><Plus size={13}/>Add model</button>
   </form>
  </SettingsGroup>
  <SettingsGroup title="Connections">
   <SettingRow title="Connection" description="Choose an existing Connection or add another endpoint.">
    <div className="connection-picker"><select aria-label="Connection" value={connectionId} onChange={event=>setConnectionId(event.target.value)}><option value="new">New Connection…</option>{configuration?.connections.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select>{connection&&<button type="button" aria-label={`Delete Connection ${connection.label}`} onClick={()=>void props.deleteConnection(connection.id).catch(()=>{})}><Trash2 size={14}/></button>}</div>
   </SettingRow>
   <form className="connection-form" onSubmit={saveConnection}>
    <SettingRow title="Provider Profile" description="Built-in Profiles lock trusted endpoint metadata."><select aria-label="Provider Profile" disabled={Boolean(connection)} value={profileId} onChange={event=>chooseProfile(event.target.value)}>{props.profiles.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select></SettingRow>
    <SettingRow title="Label" description="A local name used by Configured Models."><input aria-label="Connection label" required value={connectionLabel} onChange={event=>setConnectionLabel(event.target.value)}/></SettingRow>
    {profile?.kind==="custom"&&<><SettingRow title="Base URL" description="Custom endpoints must use HTTPS, except loopback development servers."><input aria-label="Base URL" required value={baseUrl} onChange={event=>setBaseUrl(event.target.value)}/></SettingRow><SettingRow title="API key environment" description="Optional environment-variable fallback."><input aria-label="API key environment" value={apiKeyEnv} onChange={event=>setApiKeyEnv(event.target.value)}/></SettingRow></>}
    <SettingRow title="API key" description="Write-only. If entered, it is stored as plaintext in your user-local settings.json. Prefer an environment variable when possible.">
     <div className="credential-stack"><div className="credential-field"><KeyRound size={14}/><input aria-label="API key" type="password" autoComplete="off" value={apiKey} placeholder={connection?.api_key_configured?"Credential configured — enter to replace":"Enter credential"} onChange={event=>{setApiKey(event.target.value);setClearKey(false)}}/></div>{connection?.api_key_configured&&<label><input type="checkbox" checked={clearKey} onChange={event=>{setClearKey(event.target.checked);if(event.target.checked)setApiKey("")}}/>Clear stored credential</label>}</div>
    </SettingRow>
    <div className="settings-save"><span>{saved&&<><Check size={14}/>{saved}</>}</span><button className="settings-primary" disabled={saving}>{saving?"Saving…":"Save Connection"}</button></div>
   </form>
  </SettingsGroup>
  <SettingsGroup title="Application">
   <SettingRow title="Desktop version" description="Current preview build."><code>{props.appInfo?.desktopVersion??"—"}</code></SettingRow>
   <SettingRow title="Noval Core" description="Configuration and credential owner."><code>{props.appInfo?.coreVersion??"—"}</code></SettingRow>
   <SettingRow title="Sidecar protocol" description="Typed Electron ↔ Python transport contract."><code>v{props.appInfo?.protocolVersion??"—"}</code></SettingRow>
  </SettingsGroup>
 </section>
}

function ProfileSettings({models,workspace,projectCount,sessionCount,appInfo}:Props){
 const active=useMemo(()=>models?.configured.find(item=>item.id===models.default_model_id),[models]);
 const connection=models?.connections.find(item=>item.id===active?.connection_id);
 return <section className="settings-page">
  <PageHeader eyebrow="LOCAL PROFILE" title="Private by design" description="A truthful view of the Noval state available on this device. No account or cloud profile is required."/>
  <div className="profile-hero">
   <div className="profile-mark">N</div><div><span className="profile-status"><i/>Local Runtime connected</span><h2>Noval Desktop</h2><p>Your projects, Sessions, permissions, and model configuration remain under local Runtime ownership.</p></div>
  </div>
  <div className="profile-stats">
   <Stat icon={<FolderKanban size={17}/>} value={String(projectCount)} label="Projects"/>
   <Stat icon={<CircleUserRound size={17}/>} value={String(sessionCount)} label="Stored Sessions"/>
   <Stat icon={<Cpu size={17}/>} value={active?.label??"—"} label="Default model"/>
  </div>
  <SettingsGroup title="Current environment">
   <SettingRow title="Active workspace" description="The project Noval will use for the next new task."><span className="setting-value truncate" title={workspace??undefined}>{workspace??"No project selected"}</span></SettingRow>
   <SettingRow title="Provider adapter" description="Phase 1 uses the OpenAI-compatible Adapter."><span className="setting-value">{connection?.adapter??"—"}</span></SettingRow>
   <SettingRow title="Credential status" description="Only availability is exposed to Desktop."><span className="privacy-badge"><ShieldCheck size={13}/>{connection?.credential_available?"Available":"Not configured"}</span></SettingRow>
   <SettingRow title="Runtime boundary" description="Electron is the product shell; Python remains the only execution kernel."><span className="privacy-badge"><ServerCog size={13}/>Local</span></SettingRow>
   <SettingRow title="Core version" description="Canonical Session and permission owner."><code>{appInfo?.coreVersion??"—"}</code></SettingRow>
  </SettingsGroup>
 </section>
}

function AppearanceSettings({appearance,saveAppearance}:{appearance:AppearancePreferences;saveAppearance:(value:AppearancePreferences)=>Promise<void>}){
 return <section className="settings-page">
  <PageHeader eyebrow="INTERFACE" title="Appearance" description="Choose how Noval looks on this device. Changes apply immediately and persist across restarts."/>
  <SettingsGroup title="Theme">
   <div className="theme-grid">
    <ThemeChoice label="System" icon={<SunMoon size={16}/>} value="system" current={appearance.theme} onChoose={theme=>saveAppearance({...appearance,theme})}/>
    <ThemeChoice label="Light" icon={<Sun size={16}/>} value="light" current={appearance.theme} onChoose={theme=>saveAppearance({...appearance,theme})}/>
    <ThemeChoice label="Dark" icon={<Moon size={16}/>} value="dark" current={appearance.theme} onChoose={theme=>saveAppearance({...appearance,theme})}/>
   </div>
  </SettingsGroup>
  <SettingsGroup title="Layout">
   <SettingRow title="Interface density" description="Adjust project rows and surrounding application chrome.">
    <div className="density-control" role="group" aria-label="Interface density">
     {(["comfortable","compact"] as const).map(value=><button type="button" className={appearance.density===value?"active":""} aria-pressed={appearance.density===value} key={value} onClick={()=>saveAppearance({...appearance,density:value})}>{value[0].toUpperCase()+value.slice(1)}</button>)}
    </div>
   </SettingRow>
  </SettingsGroup>
  <div className="appearance-note"><MonitorCog size={17}/><div><strong>Desktop preference only</strong><p>Theme and density are stored by Electron. They do not change Noval Core settings or Session behavior.</p></div></div>
 </section>
}

function PageHeader({eyebrow,title,description}:{eyebrow:string;title:string;description:string}){return <header className="settings-page-header"><span>{eyebrow}</span><h1>{title}</h1><p>{description}</p></header>}
function SettingsGroup({title,children}:{title:string;children:React.ReactNode}){return <section className="settings-group"><h2>{title}</h2><div className="settings-card">{children}</div></section>}
function SettingRow({title,description,children}:{title:string;description:string;children:React.ReactNode}){return <div className="setting-row"><div className="setting-copy"><strong>{title}</strong><span>{description}</span></div><div className="setting-control">{children}</div></div>}
function Stat({icon,value,label}:{icon:React.ReactNode;value:string;label:string}){return <div className="profile-stat">{icon}<strong title={value}>{value}</strong><span>{label}</span></div>}
function ThemeChoice({label,icon,value,current,onChoose}:{label:string;icon:React.ReactNode;value:AppearancePreferences["theme"];current:AppearancePreferences["theme"];onChoose:(value:AppearancePreferences["theme"])=>void}){
 return <button className={`theme-choice ${current===value?"active":""}`} aria-pressed={current===value} onClick={()=>onChoose(value)}>
  <div className={`theme-preview preview-${value}`}><span/><main><i/><i/><i/></main></div><span>{icon}{label}</span>{current===value&&<Check className="theme-check" size={14}/>}
 </button>
}
