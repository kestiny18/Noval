import { SidecarRequestError } from "./sidecar-error.js";

const CONFIGURATION_STARTUP_CODES = new Set([
  "unsupported_settings_schema",
  "legacy_settings_fields",
  "invalid_json",
  "invalid_settings",
  "duplicate_id",
  "invalid_base_url",
  "builtin_profile_mismatch",
  "unknown_profile",
  "unsupported_adapter",
  "unsupported_profile_model",
  "configured_model_not_found",
  "connection_not_found",
  "configuration_error",
]);

export function isConfigurationStartupError(
  error: unknown,
): error is SidecarRequestError {
  return (
    error instanceof SidecarRequestError
    && CONFIGURATION_STARTUP_CODES.has(error.code)
  );
}
