# Tencent WeChatReading Skill

The files in this directory are vendored from
[`Tencent/WeChatReading`](https://github.com/Tencent/WeChatReading), version
1.0.4, and remain available under the upstream Apache-2.0 license. Garden only
moves the upstream `version` frontmatter value into `metadata.version` so the
skill passes the Codex Agent Skill validator; API behavior remains unchanged.

Garden never stores a WeRead API key in this directory. Configure
`WEREAD_API_KEY` only in the environment of the AI agent that uses the skill.
