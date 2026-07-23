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
