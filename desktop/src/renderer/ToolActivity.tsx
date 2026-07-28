import {FilePenLine,Search,TerminalSquare,Wrench} from "lucide-react";
import type {LanguagePreference,TranscriptEntry} from "../shared/protocol";
import {translate} from "./i18n";

type ActivityKind="command"|"inspect"|"edit"|"other";
export type MessageItem={type:"message";key:string;role:TranscriptEntry["role"];text:string;timestamp:string|null;showMeta:boolean};
type ActivityDetail={key:string;toolName:string;content:string|null;failed:boolean;pending:boolean};
export type ActivityItem={type:"activity";key:string;kind:ActivityKind;toolNames:string[];count:number;failed:boolean;pending:boolean;details:ActivityDetail[]};
export type TimelineItem=MessageItem|ActivityItem;

export function buildTimeline(entries:TranscriptEntry[]):TimelineItem[]{
 const results=new Map(entries.flatMap(entry=>entry.tool_results.map(result=>[result.call_id,result] as const)));
 const callIds=new Set(entries.flatMap(entry=>entry.tool_calls.map(call=>call.call_id)));
 const timeline:TimelineItem[]=[];
 for(const entry of entries){
  if(entry.text)timeline.push({type:"message",key:`message-${entry.sequence}`,role:entry.role,text:entry.text,timestamp:entry.timestamp,showMeta:entry.role==="user"||(entry.role==="assistant"&&entry.tool_calls.length===0)});
  for(const call of entry.tool_calls){
   const result=results.get(call.call_id),activity:ActivityItem={type:"activity",key:`activity-${call.call_id}`,kind:activityKind(call.name),toolNames:[call.name],count:1,failed:Boolean(result?.is_error),pending:!result,details:[{key:call.call_id,toolName:call.name,content:result?.content??null,failed:Boolean(result?.is_error),pending:!result}]};
   appendActivity(timeline,activity);
  }
  for(const result of entry.tool_results){
   if(callIds.has(result.call_id))continue;
   appendActivity(timeline,{type:"activity",key:`result-${result.call_id}`,kind:"other",toolNames:[],count:1,failed:result.is_error,pending:false,details:[{key:result.call_id,toolName:"Tool",content:result.content,failed:result.is_error,pending:false}]});
  }
 }
 return timeline;
}

export function ToolActivity({activity,language}:{activity:ActivityItem;language:LanguagePreference}){
 const Icon=activity.kind==="command"?TerminalSquare:activity.kind==="inspect"?Search:activity.kind==="edit"?FilePenLine:Wrench;
 const label=activityLabel(activity,language);
 return <details className={`activity-row ${activity.failed?"failed":activity.pending?"pending":""}`}>
  <summary aria-label={`${label}${activity.toolNames.length?`: ${activity.toolNames.join(", ")}`:""}`} title={activity.toolNames.join(", ")}><Icon size={15}/><span>{label}</span></summary>
  <div className="activity-details">{activity.details.map(detail=><section key={detail.key}><strong>{detail.toolName}</strong><small>{translate(language,detail.pending?"running":detail.failed?"failed":"completed")}</small>{detail.content&&<pre>{detail.content}</pre>}</section>)}</div>
 </details>
}

function appendActivity(timeline:TimelineItem[],activity:ActivityItem){
 const previous=timeline.at(-1);
 if(previous?.type==="activity"&&previous.kind===activity.kind&&previous.failed===activity.failed&&previous.pending===activity.pending){
  previous.count+=activity.count;previous.toolNames.push(...activity.toolNames);previous.details.push(...activity.details);return;
 }
 timeline.push(activity);
}

function activityKind(name:string):ActivityKind{
 if(/bash|shell|command|process/i.test(name))return "command";
 if(/read|list|glob|grep|search|inspect/i.test(name))return "inspect";
 if(/write|edit|patch|delete|move/i.test(name))return "edit";
 return "other";
}

function activityLabel(activity:ActivityItem,language:LanguagePreference){
 if(activity.failed)return translate(language,activity.kind==="command"?"commandFailed":activity.kind==="edit"?"fileChangeFailed":"toolFailed");
 if(activity.pending)return translate(language,activity.kind==="command"?"runningCommand":activity.kind==="inspect"?"inspectingFiles":activity.kind==="edit"?"editingFiles":"usingTool");
 if(activity.kind==="command")return translate(language,activity.count>1?"ranCommands":"ranCommand",{count:activity.count});
 if(activity.kind==="inspect")return translate(language,activity.count>1?"inspectedFiles":"inspectedFile");
 if(activity.kind==="edit")return translate(language,activity.count>1?"editedFiles":"editedFile");
 return translate(language,activity.count>1?"usedTools":"usedTool",{count:activity.count});
}
