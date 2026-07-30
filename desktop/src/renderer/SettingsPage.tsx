import {FormEvent,useState} from "react";
import {
  ArrowLeft,Check,ChevronRight,Languages,KeyRound,MonitorCog,Moon,Palette,
  Settings2,ShieldCheck,SlidersHorizontal,Sun,SunMoon,
} from "lucide-react";
import type {
  AppInfo,AppearancePreferences,ConnectionUpsert,LanguagePreference,
  ModelConfigurationInfo,ProviderProfileInfo,UsageAnalytics,
} from "../shared/protocol";
import {API_SCHEMA_VERSION} from "../shared/protocol";
import {translate} from "./i18n";
import {UsageAnalyticsPanel} from "./UsageAnalyticsPanel";

export type SettingsSection="general"|"appearance"|"models";
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
  usage:UsageAnalytics|null;
  usageLoading:boolean;
  usageError:string|null;
  reloadUsage:()=>void;
  initialSection:SettingsSection;
};

export function SettingsPage(props:Props){
  const [section,setSection]=useState<SettingsSection>(props.initialSection);
  const t=(key:Parameters<typeof translate>[1],values?:Record<string,string|number>)=>translate(props.language,key,values);
  return <div className="settings-shell" data-testid="settings-shell">
    <aside className="settings-sidebar">
      <button className="settings-back" onClick={props.close}><ArrowLeft size={14}/>{t("backToNoval")}</button>
      <div className="settings-brand"><span>Noval</span><strong>{t("settings")}</strong></div>
      <nav aria-label={t("settingsSections")}>
        <p>{t("desktop")}</p>
        <NavButton active={section==="general"} icon={<SlidersHorizontal size={15}/>} onClick={()=>setSection("general")}>{t("general")}</NavButton>
        <NavButton active={section==="appearance"} icon={<Palette size={15}/>} onClick={()=>setSection("appearance")}>{t("appearance")}</NavButton>
        <NavButton active={section==="models"} icon={<Settings2 size={15}/>} onClick={()=>setSection("models")}>{t("models")}</NavButton>
      </nav>
    </aside>
    <main className="settings-content">
      {props.error&&<div className="settings-error" role="alert"><span>{props.error}</span><button onClick={props.dismissError}>{t("dismiss")}</button></div>}
      {section==="general"&&<GeneralSettings {...props}/>}
      {section==="appearance"&&<AppearanceSettings {...props}/>}
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
  const t=(key:Parameters<typeof translate>[1])=>translate(props.language,key);
  const connection=props.models?.connections.find(item=>item.profile_id==="deepseek");
  const [apiKey,setApiKey]=useState("");
  const [clearKey,setClearKey]=useState(false);
  const [saving,setSaving]=useState(false);
  const [saved,setSaved]=useState(false);

  async function save(event:FormEvent){
    event.preventDefault();
    if(!connection||!props.models)return;
    setSaving(true);
    setSaved(false);
    try{
      await props.upsertConnection({
        schema_version:API_SCHEMA_VERSION,
        expected_configuration_revision:props.models.revision,
        connection_id:connection.id,
        expected_connection_revision:connection.revision,
        label:"DeepSeek",
        profile_id:"deepseek",
        api_key:apiKey.trim()||undefined,
        clear_api_key:clearKey,
      });
      setApiKey("");
      setClearKey(false);
      setSaved(true);
    }catch{}finally{setSaving(false)}
  }

  return <section className="settings-page">
    <PageHeader eyebrow={t("modelSettingsEyebrow")} title={t("models")} description={t("modelsDescription")}/>
    <UsageAnalyticsPanel analytics={props.usage} loading={props.usageLoading} error={props.usageError} language={props.language} retry={props.reloadUsage}/>
    <SettingsGroup title={t("modelConfiguration")}>
      <form className="connection-form" onSubmit={save}>
        <div className="model-summary">
          <div className="model-mark">D</div>
          <div><strong>DeepSeek</strong><span>{t("deepseekDescription")}</span></div>
          <span className="privacy-badge"><ShieldCheck size={13}/>{connection?.credential_available?t("available"):t("notConfigured")}</span>
        </div>
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

function GeneralSettings(props:Props){
  const t=(key:Parameters<typeof translate>[1])=>translate(props.language,key);
  return <section className="settings-page">
    <PageHeader eyebrow={t("preferences")} title={t("general")} description={t("generalDescription")}/>
    <SettingsGroup title={t("application")}>
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
      <SettingRow title={t("appVersion")} description={t("appVersionDescription")}>
        <code>{props.appInfo?.desktopVersion??"—"}</code>
      </SettingRow>
    </SettingsGroup>
    <div className="appearance-note">
      <Languages size={17}/>
      <div><strong>{t("languagePreference")}</strong><p>{t("languageNote")}</p></div>
    </div>
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
      <div><strong>{t("appearancePreference")}</strong><p>{t("appearanceNote")}</p></div>
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
function ThemeChoice({label,icon,value,current,onChoose}:{label:string;icon:React.ReactNode;value:AppearancePreferences["theme"];current:AppearancePreferences["theme"];onChoose:(value:AppearancePreferences["theme"])=>void}){
  return <button className={`theme-choice ${current===value?"active":""}`} aria-pressed={current===value} onClick={()=>onChoose(value)}>
    <div className={`theme-preview preview-${value}`}><span/><main><i/><i/><i/></main></div>
    <span>{icon}{label}</span>{current===value&&<Check className="theme-check" size={14}/>}
  </button>;
}
