import {FormEvent,useState} from "react";
import {
  ArrowLeft,Check,ChevronRight,CircleUserRound,Cpu,FolderKanban,Languages,
  KeyRound,MonitorCog,Moon,Palette,ServerCog,Settings2,ShieldCheck,Sun,SunMoon,
} from "lucide-react";
import type {
  AppInfo,AppearancePreferences,ConnectionUpsert,LanguagePreference,
  ModelConfigurationInfo,ProviderProfileInfo,
} from "../shared/protocol";
import {API_SCHEMA_VERSION} from "../shared/protocol";
import {translate} from "./i18n";

type Section="profile"|"appearance"|"models"|"language";
type Props={
  profiles:ProviderProfileInfo[];
  models:ModelConfigurationInfo|null;
  upsertConnection:(value:ConnectionUpsert)=>Promise<void>;
  appearance:AppearancePreferences;
  saveAppearance:(value:AppearancePreferences)=>Promise<void>;
  language:LanguagePreference;
  saveLanguage:(value:LanguagePreference)=>Promise<void>;
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
  const t=(key:Parameters<typeof translate>[1],values?:Record<string,string|number>)=>translate(props.language,key,values);
  return <div className="settings-shell" data-testid="settings-shell">
    <aside className="settings-sidebar">
      <button className="settings-back" onClick={props.close}><ArrowLeft size={14}/>{t("backToNoval")}</button>
      <div className="settings-brand"><span>Noval</span><strong>{t("settings")}</strong></div>
      <nav aria-label={t("settingsSections")}>
        <p>{t("desktop")}</p>
        <NavButton active={section==="profile"} icon={<CircleUserRound size={15}/>} onClick={()=>setSection("profile")}>{t("profile")}</NavButton>
        <NavButton active={section==="appearance"} icon={<Palette size={15}/>} onClick={()=>setSection("appearance")}>{t("appearance")}</NavButton>
        <NavButton active={section==="models"} icon={<Settings2 size={15}/>} onClick={()=>setSection("models")}>{t("models")}</NavButton>
        <NavButton active={section==="language"} icon={<Languages size={15}/>} onClick={()=>setSection("language")}>{t("language")}</NavButton>
      </nav>
      <footer><ShieldCheck size={14}/><span>{t("settingsBoundary")}</span></footer>
    </aside>
    <main className="settings-content">
      {props.error&&<div className="settings-error" role="alert"><span>{props.error}</span><button onClick={props.dismissError}>{t("dismiss")}</button></div>}
      {section==="profile"&&<ProfileSettings {...props}/>}
      {section==="appearance"&&<AppearanceSettings {...props}/>}
      {section==="models"&&<ModelSettings {...props}/>}
      {section==="language"&&<LanguageSettings {...props}/>}
    </main>
  </div>;
}

function NavButton({active,icon,onClick,children}:{active:boolean;icon:React.ReactNode;onClick:()=>void;children:React.ReactNode}){
  return <button className={active?"active":""} aria-current={active?"page":undefined} onClick={onClick}>
    <span>{icon}{children}</span><ChevronRight size={13}/>
  </button>;
}

function ModelSettings(props:Props){
  const t=(key:Parameters<typeof translate>[1])=>translate(props.language,key);
  const profile=props.profiles.find(item=>item.id==="deepseek"&&item.kind==="builtin");
  const connection=props.models?.connections.find(item=>item.profile_id==="deepseek");
  const [apiKey,setApiKey]=useState("");
  const [clearKey,setClearKey]=useState(false);
  const [saving,setSaving]=useState(false);
  const [saved,setSaved]=useState(false);

  async function save(event:FormEvent){
    event.preventDefault();
    if(!profile||!connection||!props.models)return;
    setSaving(true);
    setSaved(false);
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
      setSaved(true);
    }catch{}finally{setSaving(false)}
  }

  return <section className="settings-page">
    <PageHeader eyebrow={t("runtime")} title={t("models")} description={t("modelsDescription")}/>
    <SettingsGroup title={t("provider")}>
      <SettingRow title={t("modelProvider")} description={t("providerDescription")}>
        <select aria-label={t("modelProvider")} value="deepseek" disabled>
          <option value="deepseek">{profile?.label??"DeepSeek"}</option>
        </select>
      </SettingRow>
      <SettingRow title={t("credentialStatus")} description={t("credentialStatusDescription")}>
        <span className="privacy-badge"><ShieldCheck size={13}/>{connection?.credential_available?t("available"):t("notConfigured")}</span>
      </SettingRow>
    </SettingsGroup>
    <SettingsGroup title={t("apiKeyGroup")}>
      <form className="connection-form" onSubmit={save}>
        <SettingRow title={t("deepseekApiKey")} description={t("apiKeyDescription")}>
          <div className="credential-stack">
            <div className="credential-field">
              <KeyRound size={14}/>
              <input
                aria-label={t("apiKey")}
                type="password"
                autoComplete="off"
                value={apiKey}
                placeholder={connection?.api_key_configured?t("credentialConfigured"):t("enterCredential")}
                onChange={event=>{setApiKey(event.target.value);setClearKey(false)}}
              />
            </div>
            {connection?.api_key_configured&&<label>
              <input
                type="checkbox"
                checked={clearKey}
                onChange={event=>{setClearKey(event.target.checked);if(event.target.checked)setApiKey("")}}
              />
              {t("clearCredential")}
            </label>}
          </div>
        </SettingRow>
        <div className="settings-save">
          <span>{saved&&<><Check size={14}/>{t("apiKeySaved")}</>}</span>
          <button className="settings-primary" disabled={saving||!connection}>{saving?t("saving"):t("saveApiKey")}</button>
        </div>
      </form>
    </SettingsGroup>
  </section>;
}

function ProfileSettings(props:Props){
  const t=(key:Parameters<typeof translate>[1])=>translate(props.language,key);
  return <section className="settings-page">
    <PageHeader eyebrow={t("localProfile")} title={t("privateByDesign")} description={t("profileDescription")}/>
    <div className="profile-hero">
      <div className="profile-mark">N</div>
      <div>
        <span className="profile-status"><i/>{t("localRuntimeConnected")}</span>
        <h2>{t("novalDesktop")}</h2>
        <p>{t("profileOwnership")}</p>
      </div>
    </div>
    <div className="profile-stats">
      <Stat icon={<FolderKanban size={17}/>} value={String(props.projectCount)} label={t("projectsCount")}/>
      <Stat icon={<CircleUserRound size={17}/>} value={String(props.sessionCount)} label={t("storedSessions")}/>
      <Stat icon={<Cpu size={17}/>} value={props.appInfo?.coreVersion??"—"} label={t("coreVersion")}/>
    </div>
    <SettingsGroup title={t("currentEnvironment")}>
      <SettingRow title={t("activeWorkspace")} description={t("activeWorkspaceDescription")}>
        <span className="setting-value truncate" title={props.workspace??undefined}>{props.workspace??t("noProjectSelected")}</span>
      </SettingRow>
      <SettingRow title={t("runtimeBoundary")} description={t("runtimeBoundaryDescription")}>
        <span className="privacy-badge"><ServerCog size={13}/>{t("local")}</span>
      </SettingRow>
      <SettingRow title={t("desktopVersion")} description={t("previewBuild")}><code>{props.appInfo?.desktopVersion??"—"}</code></SettingRow>
      <SettingRow title={t("sidecarProtocol")} description={t("sidecarDescription")}><code>v{props.appInfo?.protocolVersion??"—"}</code></SettingRow>
    </SettingsGroup>
  </section>;
}

function AppearanceSettings(props:Props){
  const t=(key:Parameters<typeof translate>[1])=>translate(props.language,key);
  return <section className="settings-page">
    <PageHeader eyebrow={t("interface")} title={t("appearance")} description={t("appearanceDescription")}/>
    <SettingsGroup title={t("theme")}>
      <div className="theme-grid">
        <ThemeChoice label={t("system")} icon={<SunMoon size={16}/>} value="system" current={props.appearance.theme} onChoose={theme=>props.saveAppearance({...props.appearance,theme})}/>
        <ThemeChoice label={t("light")} icon={<Sun size={16}/>} value="light" current={props.appearance.theme} onChoose={theme=>props.saveAppearance({...props.appearance,theme})}/>
        <ThemeChoice label={t("dark")} icon={<Moon size={16}/>} value="dark" current={props.appearance.theme} onChoose={theme=>props.saveAppearance({...props.appearance,theme})}/>
      </div>
    </SettingsGroup>
    <SettingsGroup title={t("layout")}>
      <SettingRow title={t("interfaceDensity")} description={t("densityDescription")}>
        <div className="density-control" role="group" aria-label={t("interfaceDensity")}>
          {(["comfortable","compact"] as const).map(value=><button
            type="button"
            className={props.appearance.density===value?"active":""}
            aria-pressed={props.appearance.density===value}
            key={value}
            onClick={()=>props.saveAppearance({...props.appearance,density:value})}
          >{t(value)}</button>)}
        </div>
      </SettingRow>
    </SettingsGroup>
    <div className="appearance-note">
      <MonitorCog size={17}/>
      <div><strong>{t("desktopPreference")}</strong><p>{t("appearanceBoundary")}</p></div>
    </div>
  </section>;
}

function LanguageSettings(props:Props){
  const t=(key:Parameters<typeof translate>[1])=>translate(props.language,key);
  return <section className="settings-page">
    <PageHeader eyebrow={t("languageEyebrow")} title={t("languageTitle")} description={t("languageDescription")}/>
    <SettingsGroup title={t("displayLanguage")}>
      <SettingRow title={t("displayLanguage")} description={t("displayLanguageDescription")}>
        <select
          aria-label={t("displayLanguage")}
          value={props.language}
          onChange={event=>void props.saveLanguage(event.target.value as LanguagePreference)}
        >
          <option value="zh-CN">{t("chinese")}</option>
          <option value="en">{t("english")}</option>
        </select>
      </SettingRow>
    </SettingsGroup>
    <div className="appearance-note">
      <Languages size={17}/>
      <div><strong>{t("desktopPreference")}</strong><p>{t("languageNote")}</p></div>
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
