import {describe,expect,it} from "vitest";
import {DiagnosticBuffer,sanitizeDiagnostic} from "./diagnostics.js";

describe("safe desktop diagnostics",()=>{
  it("redacts common credential forms",()=>{
    const value=sanitizeDiagnostic("Authorization: Bearer abc.def API_KEY=super-secret password=hunter2");
    expect(value).not.toContain("abc.def");expect(value).not.toContain("super-secret");expect(value).not.toContain("hunter2");
  });
  it("retains only the bounded tail",()=>{
    const buffer=new DiagnosticBuffer(2);buffer.add("test","one");buffer.add("test","two");buffer.add("test","three");
    expect(buffer.snapshot()).toHaveLength(2);expect(buffer.snapshot().join(" ")).not.toContain("one");
  });
});
