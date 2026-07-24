import {expect,it,vi} from "vitest";
import {sendToRenderer} from "./renderer-events";

function target(windowDestroyed=false,contentsDestroyed=false){
 const send=vi.fn();
 return {send,target:{isDestroyed:()=>windowDestroyed,webContents:{isDestroyed:()=>contentsDestroyed,send}}};
}

it("does not send events after the BrowserWindow is destroyed",()=>{
 const value=target(true);
 expect(sendToRenderer(value.target,"noval:event",{})).toBe(false);
 expect(value.send).not.toHaveBeenCalled();
});

it("does not send events after webContents is destroyed",()=>{
 const value=target(false,true);
 expect(sendToRenderer(value.target,"noval:event",{})).toBe(false);
 expect(value.send).not.toHaveBeenCalled();
});

it("sends events while the renderer is alive",()=>{
 const value=target();
 const event={event:"host.connection"};
 expect(sendToRenderer(value.target,"noval:event",event)).toBe(true);
 expect(value.send).toHaveBeenCalledWith("noval:event",event);
});
