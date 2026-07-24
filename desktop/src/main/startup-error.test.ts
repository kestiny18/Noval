import { describe, expect, it } from "vitest";

import { SidecarRequestError } from "./sidecar-error.js";
import { isConfigurationStartupError } from "./startup-error.js";

describe("configuration startup failures", () => {
  it.each([
    "unsupported_settings_schema",
    "legacy_settings_fields",
    "invalid_settings",
    "configuration_error",
  ])("treats %s as non-retryable configuration state", (code) => {
    expect(
      isConfigurationStartupError(
        new SidecarRequestError(code, "safe startup failure", false),
      ),
    ).toBe(true);
  });

  it("does not misclassify transient or internal failures", () => {
    expect(
      isConfigurationStartupError(
        new SidecarRequestError("internal_error", "safe failure", true),
      ),
    ).toBe(false);
    expect(isConfigurationStartupError(new Error("process exited"))).toBe(false);
  });
});
