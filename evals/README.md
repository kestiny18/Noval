# Noval Eval assets

`evals/` 保存 Noval 开发过程中沉淀的评测资产。公开仓库只保留脱敏、最小化、可复现的用例；真实 Session、日志、截图和终端记录只进入本地私有证据目录。

## 目录边界

```text
evals/
  README.md          # 本说明，提交
  context/           # Context 压缩用例、运行器与报告，提交
  task/              # 任务完成 judge 契约用例、运行器与报告，提交
  private/           # 原始验证证据，整体被 Git 忽略
    manifest.jsonl   # 来源、用途、敏感级别与文件哈希
    evidence/        # 按 evidence id 保存的原始副本
```

后续增加新的 Eval 领域时，应与 `context/` 平级，例如 `behavior/`、`persistence/` 和 `environment/`。不要让生产代码依赖 `evals/`。

## 私有证据规则

- 原始材料复制到 `evals/private/evidence/<evidence-id>/`，不移动或修改来源文件。
- `manifest.jsonl` 每行描述一组证据，并记录源路径、仓库内私有路径、SHA-256、大小和人工验证结论。
- 私有证据一律视为可能包含企业代码、路径、账号、凭据、个人信息和模型协议状态。
- `.gitignore` 不是安全边界；提交前仍要检查 staged diff 和敏感内容，禁止使用 `git add -f evals/private`。
- 私有目录不受 Git 保护，需要随工作区单独备份。

## 从证据派生公开用例

1. 选择带有“失败现象、用户纠正、最终验证”链路的真实证据。
2. 提取能够复现单一行为的最小上下文，不复制整段会话。
3. 替换项目名、内部路径、域名、账号、订单号、身份证号和任何凭据；编码类用例可保留合成中文文本。
4. 把用户结论转成 `expected`、`forbidden` 和 hard failure，而不是固定全文答案。
5. 使用当前版本生成基线，人工复核后才允许将报告加入公开资产。

原始证据只负责可追溯，公开用例才是可执行的回归契约。
