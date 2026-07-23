import {FormEvent,useState} from "react";
import {ArrowLeft,Check,ChevronRight,CircleUserRound,Cpu,FolderKanban,KeyRound,MonitorCog,Moon,Palette,Settings2,ShieldCheck,Sun,SunMoon} from "lucide-react";
import type {AppInfo,AppearancePreferences,ProviderProfile} from "../shared/protocol";

type Section="general"|"profile"|"appearance";
type Props={
 profile:ProviderProfile;setProfile:(value:ProviderProfile)=>void;apiKey:string;setApiKey:(value:string)=>void;
 appearance:AppearancePreferences;saveAppearance:(value:AppearancePreferences)=>Promise<void>;
 appInfo:AppInfo|null;workspace:string|null;projectCount:number;sessionCount:number;
 saveProvider:(event:FormEvent)=>Promise<boolean>;close:()=>void;error:string|null;dismissError:()=>void;
};

export function SettingsPage(props:Props){
 const [section,setSection]=useState<Section>("general");
 return <div className="settings-shell" data-testid="settings-shell">
  <aside className="settings-sidebar">
   <button className="settings-back" onClick={props.close}><ArrowLeft size={14}/>Back to Noval</button>
   <div className="settings-brand"><span>Noval</span><strong>Settings</strong></div>
   <nav aria-label="Settings sections">
    <p>Desktop</p>
    <NavButton active={section==="general"} icon={<Settings2 size={15}/>} onClick={()=>setSection("general")}>General</NavButton>
    <NavButton active={section==="profile"} icon={<CircleUserRound size={15}/>} onClick={()=>setSection("profile")}>Profile</NavButton>
    <NavButton active={section==="appearance"} icon={<Palette size={15}/>} onClick={()=>setSection("appearance")}>Appearance</NavButton>
   </nav>
   <footer><ShieldCheck size={14}/><span>Preferences stay on this device.</span></footer>
  </aside>
  <main className="settings-content">
   {props.error&&<div className="settings-error" role="alert"><span>{props.error}</span><button onClick={props.dismissError}>Dismiss</button></div>}
   {section==="general"&&<GeneralSettings {...props}/>}
   {section==="profile"&&<ProfileSettings {...props}/>}
   {section==="appearance"&&<AppearanceSettings appearance={props.appearance} saveAppearance={props.saveAppearance}/>}
  </main>
 </div>
}

function NavButton({active,icon,onClick,children}:{active:boolean;icon:React.ReactNode;onClick:()=>void;children:React.ReactNode}){
 return <button className={active?"active":""} aria-current={active?"page":undefined} onClick={onClick}><span>{icon}{children}</span><ChevronRight size={13}/></button>
}

function GeneralSettings({profile,setProfile,apiKey,setApiKey,saveProvider,appInfo}:Props){
 const [saving,setSaving]=useState(false),[saved,setSaved]=useState(false);
 async function submit(event:FormEvent){setSaving(true);setSaved(false);try{if(await saveProvider(event))setSaved(true)}finally{setSaving(false)}}
 return <form className="settings-page" onSubmit={submit}>
  <PageHeader eyebrow="DESKTOP" title="General" description="Configure the model connection used by Noval Runtime."/>
  <SettingsGroup title="Model connection">
   <SettingRow title="Provider" description="The adapter used for new and resumed Sessions.">
    <select aria-label="Provider" value={profile.provider} onChange={event=>setProfile({...profile,provider:event.target.value as ProviderProfile["provider"]})}><option value="openai-compatible">OpenAI-compatible</option><option value="anthropic">Anthropic</option></select>
   </SettingRow>
   <SettingRow title="Model" description="Primary model for conversation and tool use."><input aria-label="Model" value={profile.model} onChange={event=>setProfile({...profile,model:event.target.value})}/></SettingRow>
   <SettingRow title="Judge model" description="Lightweight model used for semantic completion assessment."><input aria-label="Judge model" value={profile.judgeModel} onChange={event=>setProfile({...profile,judgeModel:event.target.value})}/></SettingRow>
   <SettingRow title="Base URL" description="Provider-compatible API endpoint."><input aria-label="Base URL" value={profile.baseUrl} onChange={event=>setProfile({...profile,baseUrl:event.target.value})}/></SettingRow>
   <SettingRow title="API key" description="Encrypted with operating-system credential protection.">
    <div className="credential-field"><KeyRound size={14}/><input aria-label="API key" type="password" autoComplete="off" value={apiKey} placeholder={profile.hasApiKey?"Saved securely — enter a new key to replace":"Enter API key"} onChange={event=>setApiKey(event.target.value)}/></div>
   </SettingRow>
  </SettingsGroup>
  <SettingsGroup title="Application">
   <SettingRow title="Desktop version" description="Current preview build."><code>{appInfo?.desktopVersion??"—"}</code></SettingRow>
   <SettingRow title="Noval Core" description="Embedded Python Runtime version."><code>{appInfo?.coreVersion??"—"}</code></SettingRow>
   <SettingRow title="Sidecar protocol" description="Typed Electron ↔ Python transport contract."><code>v{appInfo?.protocolVersion??"—"}</code></SettingRow>
  </SettingsGroup>
  <div className="settings-save"><span>{saved&&<><Check size={14}/>Saved. Runtime restarted with this connection.</>}</span><button className="settings-primary" disabled={saving}>{saving?"Saving…":"Save connection"}</button></div>
 </form>
}

function ProfileSettings({profile,workspace,projectCount,sessionCount,appInfo}:Props){
 return <section className="settings-page">
  <PageHeader eyebrow="LOCAL PROFILE" title="Private by design" description="A truthful view of the Noval state available on this device. No account or cloud profile is required."/>
  <div className="profile-hero">
   <div className="profile-mark">N</div><div><span className="profile-status"><i/>Local Runtime connected</span><h2>Noval Desktop</h2><p>Your projects, Sessions, permissions, and provider configuration remain under local Runtime ownership.</p></div>
  </div>
  <div className="profile-stats">
   <Stat icon={<FolderKanban size={17}/>} value={String(projectCount)} label="Projects"/>
   <Stat icon={<CircleUserRound size={17}/>} value={String(sessionCount)} label="Stored Sessions"/>
   <Stat icon={<Cpu size={17}/>} value={profile.model} label="Active model"/>
  </div>
  <SettingsGroup title="Current environment">
   <SettingRow title="Active workspace" description="The project Noval will use for the next new task."><span className="setting-value truncate" title={workspace??undefined}>{workspace??"No project selected"}</span></SettingRow>
   <SettingRow title="Provider" description="Effective Runtime adapter."><span className="setting-value">{profile.provider}</span></SettingRow>
   <SettingRow title="Runtime boundary" description="Electron is the product shell; Python remains the only execution kernel."><span className="privacy-badge"><ShieldCheck size={13}/>Local</span></SettingRow>
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
