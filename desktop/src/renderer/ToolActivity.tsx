import {FilePenLine,Search,TerminalSquare,Wrench} from "lucide-react";
import type {TranscriptEntry} from "../shared/protocol";

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

export function ToolActivity({activity}:{activity:ActivityItem}){
 const Icon=activity.kind==="command"?TerminalSquare:activity.kind==="inspect"?Search:activity.kind==="edit"?FilePenLine:Wrench;
 const label=activityLabel(activity);
 return <details className={`activity-row ${activity.failed?"failed":activity.pending?"pending":""}`}>
  <summary aria-label={`${label}${activity.toolNames.length?`: ${activity.toolNames.join(", ")}`:""}`} title={activity.toolNames.join(", ")}><Icon size={15}/><span>{label}</span></summary>
  <div className="activity-details">{activity.details.map(detail=><section key={detail.key}><strong>{detail.toolName}</strong><small>{detail.pending?"Running":detail.failed?"Failed":"Completed"}</small>{detail.content&&<pre>{detail.content}</pre>}</section>)}</div>
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

function activityLabel(activity:ActivityItem){
 if(activity.failed)return activity.kind==="command"?"Command failed":activity.kind==="edit"?"File change failed":"Tool failed";
 if(activity.pending)return activity.kind==="command"?"Running a command":activity.kind==="inspect"?"Inspecting files":activity.kind==="edit"?"Editing files":"Using a tool";
 if(activity.kind==="command")return activity.count>1?`Ran ${activity.count} commands`:"Ran a command";
 if(activity.kind==="inspect")return activity.count>1?"Inspected files":"Inspected a file";
 if(activity.kind==="edit")return activity.count>1?"Edited files":"Edited a file";
 return activity.count>1?`Used ${activity.count} tools`:"Used a tool";
}
