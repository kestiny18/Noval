import pngToIco from "png-to-ico";
import {mkdirSync,writeFileSync} from "node:fs";
import {fileURLToPath} from "node:url";
const desktop=fileURLToPath(new URL("..",import.meta.url));
mkdirSync(fileURLToPath(new URL("../build",import.meta.url)),{recursive:true});
const value=await pngToIco(fileURLToPath(new URL("../../assets/brand/icon/noval-app-icon.png",import.meta.url)));
writeFileSync(fileURLToPath(new URL("../build/icon.ico",import.meta.url)),value);
