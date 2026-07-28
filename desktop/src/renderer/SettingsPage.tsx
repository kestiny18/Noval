import {FormEvent,useMemo,useState} from "react";
import {
  ArrowLeft,Check,ChevronRight,CircleUserRound,Cpu,FolderKanban,
  KeyRound,MonitorCog,Moon,Palette,ServerCog,Settings2,ShieldCheck,
  Sun,SunMoon,
} from "lucide-react";
import type {
  AppInfo,AppearancePreferences,ConnectionUpsert,ModelConfigurationInfo,
  ProviderProfileInfo,
} from "../shared/protocol";
import {API_SCHEMA_VERSION} from "../shared/protocol";

type Section="profile"|"appearance"|"models";
type Props={
  profiles:ProviderProfileInfo[];
  models:ModelConfigurationInfo|null;
  upsertConnection:(value:ConnectionUpsert)=>Promise<void>;
  appearance:AppearancePreferences;
  saveAppearance:(value:AppearancePreferences)=>Promise<void>;
  appInfo:AppInfo|null;
  workspace:string|null;
  projectCount:number;
  sessionCount:number;
  close:()=>void;
  error:string|null;
  dismissError:()=>void;
};

export function SettingsPage(props:Props){
  const [section,setSection]=useState<Section>("profile");
  return <div className="settings-shell" data-testid="settings-shell">
    <aside className="settings-sidebar">
      <button className="settings-back" onClick={props.close}>
        <ArrowLeft size={14}/>Back to Noval
      </button>
      <div className="settings-brand"><span>Noval</span><strong>Settings</strong></div>
      <nav aria-label="Settings sections">
        <p>Desktop</p>
        <NavButton active={section==="profile"} icon={<CircleUserRound size={15}/>} onClick={()=>setSection("profile")}>Profile</NavButton>
        <NavButton active={section==="appearance"} icon={<Palette size={15}/>} onClick={()=>setSection("appearance")}>Appearance</NavButton>
        <NavButton active={section==="models"} icon={<Settings2 size={15}/>} onClick={()=>setSection("models")}>Models</NavButton>
      </nav>
      <footer><ShieldCheck size={14}/><span>Runtime owns model configuration.</span></footer>
    </aside>
    <main className="settings-content">
      {props.error&&<div className="settings-error" role="alert">
        <span>{props.error}</span><button onClick={props.dismissError}>Dismiss</button>
      </div>}
      {section==="profile"&&<ProfileSettings {...props}/>}
      {section==="appearance"&&<AppearanceSettings appearance={props.appearance} saveAppearance={props.saveAppearance}/>}
      {section==="models"&&<ModelSettings {...props}/>}
    </main>
  </div>;
}

function NavButton({active,icon,onClick,children}:{active:boolean;icon:React.ReactNode;onClick:()=>void;children:React.ReactNode}){
  return <button className={active?"active":""} aria-current={active?"page":undefined} onClick={onClick}>
    <span>{icon}{children}</span><ChevronRight size={13}/>
  </button>;
}

function ModelSettings(props:Props){
  const profile=props.profiles.find(item=>item.id==="deepseek"&&item.kind==="builtin");
  const connection=props.models?.connections.find(item=>item.profile_id==="deepseek");
  const [apiKey,setApiKey]=useState("");
  const [clearKey,setClearKey]=useState(false);
  const [saving,setSaving]=useState(false);
  const [saved,setSaved]=useState("");

  async function save(event:FormEvent){
    event.preventDefault();
    if(!profile||!connection||!props.models)return;
    setSaving(true);
    setSaved("");
    try{
      await props.upsertConnection({
        schema_version:API_SCHEMA_VERSION,
        expected_configuration_revision:props.models.revision,
        connection_id:connection.id,
        expected_connection_revision:connection.revision,
        label:profile.label,
        profile_id:profile.id,
        api_key:apiKey.trim()||undefined,
        clear_api_key:clearKey,
      });
      setApiKey("");
      setClearKey(false);
      setSaved("API key saved without restarting the Runtime.");
    }catch{}finally{setSaving(false)}
  }

  return <section className="settings-page">
    <PageHeader
      eyebrow="RUNTIME"
      title="Models"
      description="Configure the credential for the verified Phase 1 Provider. Choose the model for each Session from the conversation input."
    />
    <SettingsGroup title="Provider">
      <SettingRow title="Model provider" description="Phase 1 currently ships only the real-contract-verified DeepSeek Profile.">
        <select aria-label="Model provider" value="deepseek" disabled>
          <option value="deepseek">{profile?.label??"DeepSeek"}</option>
        </select>
      </SettingRow>
      <SettingRow title="Credential status" description="Only availability is exposed to Desktop; the existing value is never returned.">
        <span className="privacy-badge">
          <ShieldCheck size={13}/>{connection?.credential_available?"Available":"Not configured"}
        </span>
      </SettingRow>
    </SettingsGroup>
    <SettingsGroup title="API key">
      <form className="connection-form" onSubmit={save}>
        <SettingRow
          title="DeepSeek API key"
          description="Write-only. A saved value is plaintext in your user-local settings.json and never rehydrated into Desktop."
        >
          <div className="credential-stack">
            <div className="credential-field">
              <KeyRound size={14}/>
              <input
                aria-label="API key"
                type="password"
                autoComplete="off"
                value={apiKey}
                placeholder={connection?.api_key_configured?"Credential configured — enter to replace":"Enter credential"}
                onChange={event=>{setApiKey(event.target.value);setClearKey(false)}}
              />
            </div>
            {connection?.api_key_configured&&<label>
              <input
                type="checkbox"
                checked={clearKey}
                onChange={event=>{setClearKey(event.target.checked);if(event.target.checked)setApiKey("")}}
              />
              Clear stored credential
            </label>}
          </div>
        </SettingRow>
        <div className="settings-save">
          <span>{saved&&<><Check size={14}/>{saved}</>}</span>
          <button className="settings-primary" disabled={saving||!connection}>
            {saving?"Saving…":"Save API key"}
          </button>
        </div>
      </form>
    </SettingsGroup>
  </section>;
}

function ProfileSettings({workspace,projectCount,sessionCount,appInfo}:Props){
  return <section className="settings-page">
    <PageHeader
      eyebrow="LOCAL PROFILE"
      title="Private by design"
      description="A truthful view of the Noval state available on this device. No account or cloud profile is required."
    />
    <div className="profile-hero">
      <div className="profile-mark">N</div>
      <div>
        <span className="profile-status"><i/>Local Runtime connected</span>
        <h2>Noval Desktop</h2>
        <p>Your projects, Sessions, permissions, and model configuration remain under local Runtime ownership.</p>
      </div>
    </div>
    <div className="profile-stats">
      <Stat icon={<FolderKanban size={17}/>} value={String(projectCount)} label="Projects"/>
      <Stat icon={<CircleUserRound size={17}/>} value={String(sessionCount)} label="Stored Sessions"/>
      <Stat icon={<Cpu size={17}/>} value={appInfo?.coreVersion??"—"} label="Core version"/>
    </div>
    <SettingsGroup title="Current environment">
      <SettingRow title="Active workspace" description="The project Noval will use for the next new task.">
        <span className="setting-value truncate" title={workspace??undefined}>{workspace??"No project selected"}</span>
      </SettingRow>
      <SettingRow title="Runtime boundary" description="Electron is the product shell; Python remains the only execution kernel.">
        <span className="privacy-badge"><ServerCog size={13}/>Local</span>
      </SettingRow>
      <SettingRow title="Desktop version" description="Current preview build.">
        <code>{appInfo?.desktopVersion??"—"}</code>
      </SettingRow>
      <SettingRow title="Sidecar protocol" description="Typed Electron ↔ Python transport contract.">
        <code>v{appInfo?.protocolVersion??"—"}</code>
      </SettingRow>
    </SettingsGroup>
  </section>;
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
          {(["comfortable","compact"] as const).map(value=><button
            type="button"
            className={appearance.density===value?"active":""}
            aria-pressed={appearance.density===value}
            key={value}
            onClick={()=>saveAppearance({...appearance,density:value})}
          >{value[0].toUpperCase()+value.slice(1)}</button>)}
        </div>
      </SettingRow>
    </SettingsGroup>
    <div className="appearance-note">
      <MonitorCog size={17}/>
      <div><strong>Desktop preference only</strong><p>Theme and density are stored by Electron. They do not change Noval Core settings or Session behavior.</p></div>
    </div>
  </section>;
}

function PageHeader({eyebrow,title,description}:{eyebrow:string;title:string;description:string}){
  return <header className="settings-page-header"><span>{eyebrow}</span><h1>{title}</h1><p>{description}</p></header>;
}
function SettingsGroup({title,children}:{title:string;children:React.ReactNode}){
  return <section className="settings-group"><h2>{title}</h2><div className="settings-card">{children}</div></section>;
}
function SettingRow({title,description,children}:{title:string;description:string;children:React.ReactNode}){
  return <div className="setting-row"><div className="setting-copy"><strong>{title}</strong><span>{description}</span></div><div className="setting-control">{children}</div></div>;
}
function Stat({icon,value,label}:{icon:React.ReactNode;value:string;label:string}){
  return <div className="profile-stat">{icon}<strong title={value}>{value}</strong><span>{label}</span></div>;
}
function ThemeChoice({label,icon,value,current,onChoose}:{label:string;icon:React.ReactNode;value:AppearancePreferences["theme"];current:AppearancePreferences["theme"];onChoose:(value:AppearancePreferences["theme"])=>void}){
  return <button className={`theme-choice ${current===value?"active":""}`} aria-pressed={current===value} onClick={()=>onChoose(value)}>
    <div className={`theme-preview preview-${value}`}><span/><main><i/><i/><i/></main></div>
    <span>{icon}{label}</span>{current===value&&<Check className="theme-check" size={14}/>}
  </button>;
}
