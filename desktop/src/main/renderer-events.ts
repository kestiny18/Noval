interface RendererTarget {
  isDestroyed(): boolean;
  webContents: {
    isDestroyed(): boolean;
    send(channel: string, value: unknown): void;
  };
}

export function sendToRenderer(
  target: RendererTarget | null,
  channel: string,
  value: unknown,
): boolean {
  if (
    target === null
    || target.isDestroyed()
    || target.webContents.isDestroyed()
  ) {
    return false;
  }
  target.webContents.send(channel, value);
  return true;
}
