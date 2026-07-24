export class SidecarRequestError extends Error {
  readonly code: string;
  readonly retryable: boolean;

  constructor(code: string, safeMessage: string, retryable: boolean) {
    super(safeMessage);
    this.name = "SidecarRequestError";
    this.code = code;
    this.retryable = retryable;
  }
}
