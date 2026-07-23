import {cleanup,render,screen} from "@testing-library/react";
import {afterEach,expect,it} from "vitest";
import {MarkdownText} from "./MarkdownText";

afterEach(cleanup);

it("renders visible transcript text as read-only GitHub-flavored Markdown",()=>{
 render(<MarkdownText text={"## 能做什么？\n\n我是 **Noval**。\n\n- 读取文件\n- 验证结果\n\n| 状态 | 结果 |\n| --- | --- |\n| 测试 | 通过 |"}/>);
 expect(screen.getByRole("heading",{name:"能做什么？",level:2})).toBeVisible();
 expect(screen.getByText("Noval").tagName).toBe("STRONG");
 expect(screen.getAllByRole("listitem")).toHaveLength(2);
 expect(screen.getByRole("table")).toBeVisible();
});

it("does not interpret raw HTML or unsafe links",()=>{
 const {container}=render(<MarkdownText text={'<img src="x" onerror="alert(1)"> [unsafe](javascript:alert(1))'}/>);
 expect(container.querySelector("img")).not.toBeInTheDocument();
 expect(screen.getByText("unsafe").tagName).toBe("SPAN");
 expect(screen.queryByRole("link",{name:"unsafe"})).not.toBeInTheDocument();
});
