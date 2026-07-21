import{rmSync}from"node:fs";for(const name of["../dist","../release"]){rmSync(new URL(name,import.meta.url),{recursive:true,force:true})}
