import {expect,it} from "vitest";
import type {TranscriptEntry} from "../shared/protocol";
import {buildTimeline} from "./ToolActivity";

it("keeps failed tool results explicit",()=>{
 const entries:TranscriptEntry[]=[
  {sequence:1,role:"assistant",text:"",timestamp:null,tool_calls:[{call_id:"call-1",name:"edit_file",argument_keys:["path"]}],tool_results:[]},
  {sequence:2,role:"tool",text:"",timestamp:null,tool_calls:[],tool_results:[{call_id:"call-1",content:"failed",is_error:true}]},
 ];
 expect(buildTimeline(entries)).toMatchObject([{type:"activity",kind:"edit",count:1,failed:true,pending:false}]);
});

it("retains safe tool result details for an expandable activity",()=>{
 const entries:TranscriptEntry[]=[
  {sequence:1,role:"assistant",text:"",timestamp:null,tool_calls:[{call_id:"call-1",name:"run_bash",argument_keys:["command"]}],tool_results:[]},
  {sequence:2,role:"tool",text:"",timestamp:null,tool_calls:[],tool_results:[{call_id:"call-1",content:"tests passed",is_error:false}]},
 ];
 expect(buildTimeline(entries)).toMatchObject([{type:"activity",details:[{toolName:"run_bash",content:"tests passed",failed:false,pending:false}]}]);
});

it("shows message actions only for user messages and final assistant replies",()=>{
 const entries:TranscriptEntry[]=[
  {sequence:1,role:"user",text:"Please inspect this",timestamp:"2026-07-24T15:00:00+08:00",tool_calls:[],tool_results:[]},
  {sequence:2,role:"assistant",text:"I will inspect it.",timestamp:"2026-07-24T15:01:00+08:00",tool_calls:[{call_id:"call-1",name:"read_file",argument_keys:["path"]}],tool_results:[]},
  {sequence:3,role:"tool",text:"",timestamp:"2026-07-24T15:01:01+08:00",tool_calls:[],tool_results:[{call_id:"call-1",content:"contents",is_error:false}]},
  {sequence:4,role:"assistant",text:"The inspection is complete.",timestamp:"2026-07-24T15:02:00+08:00",tool_calls:[],tool_results:[]},
 ];
 const messages=buildTimeline(entries).filter(item=>item.type==="message");
 expect(messages).toMatchObject([
  {text:"Please inspect this",showMeta:true},
  {text:"I will inspect it.",showMeta:false},
  {text:"The inspection is complete.",showMeta:true},
 ]);
});
