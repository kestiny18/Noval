import {useMemo,useState} from "react";
import {BarChart3,RotateCcw} from "lucide-react";
import type {LanguagePreference,UsageAnalytics,UsageDailyPoint,UsageModelSummary} from "../shared/protocol";
import {translate} from "./i18n";

type Props={analytics:UsageAnalytics|null;loading:boolean;error:string|null;language:LanguagePreference;retry:()=>void};
const ALL_MODELS="all";

export function UsageAnalyticsPanel({analytics,loading,error,language,retry}:Props){
  const [selectedModel,setSelectedModel]=useState(ALL_MODELS);
  const t=(key:Parameters<typeof translate>[1])=>translate(language,key);
  const modelSummary=selectedModel===ALL_MODELS?null:analytics?.models.find(item=>item.model===selectedModel)??null;
  const summary=summaryFor(analytics,modelSummary);
  const values=useMemo(()=>analytics?.days.map(day=>tokensFor(day,selectedModel))??[],[analytics,selectedModel]);
  const max=Math.max(0,...values);

  return <section className="usage-section" aria-labelledby="usage-title">
    <div className="usage-heading">
      <div><span className="usage-icon"><BarChart3 size={15}/></span><div><h2 id="usage-title">{t("tokenActivity")}</h2><p>{t("tokenActivityDescription")}</p></div></div>
      <select aria-label={t("usageModel")} value={selectedModel} onChange={event=>setSelectedModel(event.target.value)}>
        <option value={ALL_MODELS}>{t("allModels")}</option>
        {analytics?.models.map(item=><option value={item.model} key={item.model}>{item.model}</option>)}
      </select>
    </div>
    {loading&&!analytics?<div className="usage-state" role="status">{t("usageLoading")}</div>:
      error&&!analytics?<div className="usage-state usage-state-error" role="alert"><span>{t("usageUnavailable")}</span><button onClick={retry}><RotateCcw size={13}/>{t("retry")}</button></div>:
      <div className="usage-card">
        <div className="usage-metrics">
          <Metric value={formatTokens(summary.total,language)} label={t("cumulativeTokens")}/>
          <Metric value={formatTokens(summary.peak,language)} label={t("peakDailyTokens")}/>
          <Metric value={formatDuration(summary.duration,language)} label={t("longestTaskDuration")}/>
        </div>
        <div className="usage-calendar-scroll">
          <div className="usage-grid" role="grid" aria-label={t("tokenActivity")}>
            {analytics?.days.map((day,index)=>{
              const value=values[index]??0,column=Math.floor(index/7),row=index%7,level=intensity(value,max);
              const label=dayLabel(day.day,value,selectedModel,language,t("allModels"));
              return <button type="button" role="gridcell" className={`usage-cell usage-level-${level} ${row<3?"tooltip-below":""} ${column<4?"tooltip-left":column>47?"tooltip-right":""}`} aria-label={label} key={day.day}>
                <span className="usage-tooltip" aria-hidden="true">{label}</span>
              </button>;
            })}
          </div>
        </div>
      </div>}
  </section>;
}

function Metric({value,label}:{value:string;label:string}){return <div className="usage-metric"><strong>{value}</strong><span>{label}</span></div>}
function summaryFor(analytics:UsageAnalytics|null,model:UsageModelSummary|null){
  if(model)return {total:model.total_tokens,peak:model.peak_daily_tokens,duration:model.longest_turn_duration_ms};
  return {total:analytics?.total_tokens??0,peak:analytics?.peak_daily_tokens??0,duration:analytics?.longest_turn_duration_ms??0};
}
function tokensFor(day:UsageDailyPoint,model:string){return model===ALL_MODELS?day.total_tokens:day.by_model.find(item=>item.model===model)?.total_tokens??0}
function intensity(value:number,max:number){
  if(value<=0||max<=0)return 0;
  return Math.max(1,Math.min(4,Math.ceil(Math.log1p(value)/Math.log1p(max)*4)));
}
function formatTokens(value:number,language:LanguagePreference){return new Intl.NumberFormat(language,{notation:"compact",maximumFractionDigits:1}).format(value)}
function formatDuration(milliseconds:number,language:LanguagePreference){
  if(milliseconds<=0)return "—";
  const seconds=Math.max(1,Math.round(milliseconds/1000)),hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60),remainder=seconds%60;
  if(language==="zh-CN"){if(hours)return `${hours} 小时 ${minutes} 分`;if(minutes)return `${minutes} 分 ${remainder} 秒`;return `${remainder} 秒`}
  if(hours)return `${hours}h ${minutes}m`;if(minutes)return `${minutes}m ${remainder}s`;return `${remainder}s`;
}
function dayLabel(day:string,tokens:number,model:string,language:LanguagePreference,allModels:string){
  const date=new Intl.DateTimeFormat(language,{year:"numeric",month:"short",day:"numeric",timeZone:"UTC"}).format(new Date(`${day}T00:00:00Z`));
  return `${date}: ${new Intl.NumberFormat(language).format(tokens)} Tokens · ${model===ALL_MODELS?allModels:model}`;
}
