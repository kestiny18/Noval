const SECRET = /(api[_-]?key|authorization|password|secret|token)(\s*[=:]\s*|"\s*:\s*")([^\s,"}]+)/gi;
const BEARER = /Bearer\s+[A-Za-z0-9._~+\/-]+/gi;

export function sanitizeDiagnostic(value:string):string {
  return value.replace(BEARER,"Bearer [REDACTED]").replace(SECRET,"$1$2[REDACTED]").slice(0,2000);
}

export class DiagnosticBuffer {
  private readonly lines:string[]=[];
  constructor(private readonly limit=200){}
  add(source:string,value:string):void {
    const text=sanitizeDiagnostic(value).replace(/[\r\n]+/g," ").trim();
    if(!text)return;
    this.lines.push(`${new Date().toISOString()} ${source} ${text}`);
    if(this.lines.length>this.limit)this.lines.splice(0,this.lines.length-this.limit);
  }
  snapshot():string[]{return [...this.lines]}
}
