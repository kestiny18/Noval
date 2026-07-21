import { EnvelopeSchema, PROTOCOL_VERSION } from "./protocol.js";
import { expect, it } from "vitest";

it("accepts a versioned response", () => {
  expect(EnvelopeSchema.parse({protocol_version: PROTOCOL_VERSION, kind:"response", request_id:"r1", ok:true, result:{ready:true}})).toBeTruthy();
});

it("rejects protocol major drift", () => {
  expect(() => EnvelopeSchema.parse({protocol_version: 2, kind:"response", request_id:"r1", ok:true})).toThrow();
});
