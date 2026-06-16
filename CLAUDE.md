# Karpathy Guidelines

Behavioral guidelines to reduce common LLM coding mistakes, derived from [Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876) on LLM coding pitfalls.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Dispatch Simple Work to Haiku Subagents

**Offload mechanical, self-contained tasks to haiku subagents. Main thread stays for judgment work.**

Four criteria. Delegate when ALL are true:

1. **Self-contained** — fully describable in 2-3 sentences. No dependence on conversation history.
2. **Mechanical** — operational work (search, read, extract, run commands). No design or tradeoff decisions.
3. **Verifiable** — can tell at a glance if the output is correct.
4. **No shared state** — doesn't depend on other in-progress tasks.

Keep on main thread when: architectural reasoning needed, multi-step with dependencies, or code quality judgment (sections 1-4).

### Usage

```
Agent(description: "short description", prompt: "self-contained instruction", model: "haiku")
```

The subagent sees only the prompt, not the conversation. Include exact paths and expected output format.

### Batching

3+ independent haiku-suitable tasks → dispatch in parallel for compound speed gain.

## 6. Caveman 自动化工作流

### 6.1 提交信息 (caveman-commit)

执行 `git commit` 或要求生成提交信息时，**自动**使用 caveman-commit 技能。Conventional Commits 格式，标题≤50字符，正文仅在"为什么"不显然时添加。

### 6.2 代码审查 (caveman-review)

用户说"review""代码审查""审查 PR""检查 diff"时，**自动**使用 caveman-review 技能。一行式评论：`文件:行号: 严重程度: 问题。怎么改。`

### 6.3 文档压缩 (caveman-compress)

不自动触发。仅用户显式执行 `/caveman-compress <file>` 时使用。
