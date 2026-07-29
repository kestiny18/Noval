import { EnvelopeSchema, PROTOCOL_VERSION, UsageAnalyticsSchema } from "./protocol.js";
import { expect, it } from "vitest";

it("accepts a versioned response", () => {
  expect(EnvelopeSchema.parse({protocol_version: PROTOCOL_VERSION, kind:"response", request_id:"r1", ok:true, result:{ready:true}})).toBeTruthy();
});

it("rejects protocol major drift", () => {
  expect(() => EnvelopeSchema.parse({protocol_version: 1, kind:"response", request_id:"r1", ok:true})).toThrow();
});

it("accepts exactly 52 weeks of safe daily usage analytics",()=>{
  const days=Array.from({length:364},(_,offset)=>({
    day:new Date(Date.UTC(2025,7,1+offset)).toISOString().slice(0,10),
    total_tokens:offset,
    by_model:offset?[{model:"deepseek-v4-pro",total_tokens:offset}]:[],
  }));
  const parsed=UsageAnalyticsSchema.parse({
    schema_version:2,generated_at:"2026-07-30T08:00:00+08:00",
    window_start:days[0].day,window_end:days.at(-1)?.day,
    total_tokens:100,peak_daily_tokens:20,longest_turn_duration_ms:4000,
    models:[{model:"deepseek-v4-pro",total_tokens:100,peak_daily_tokens:20,longest_turn_duration_ms:4000}],
    days,
  });
  expect(parsed.days).toHaveLength(364);
  expect(()=>UsageAnalyticsSchema.parse({...parsed,days:parsed.days.slice(1)})).toThrow();
});
