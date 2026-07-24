import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function MarkdownText({text,streaming=false}:{text:string;streaming?:boolean}){
 return <div className="body markdown-body">
  <ReactMarkdown
   remarkPlugins={[remarkGfm]}
   skipHtml
   components={{a:({href,children})=>isSafeLink(href)?<a href={href} target="_blank" rel="noreferrer">{children}</a>:<span className="unsafe-link">{children}</span>}}
  >{text}</ReactMarkdown>
  {streaming&&<span className="cursor" aria-hidden="true"/>}
 </div>
}

function isSafeLink(href:string|undefined){return Boolean(href&&/^(https?:|mailto:)/i.test(href))}
