# Tencent WeChatReading Skill

The files in this directory are vendored from
[`Tencent/WeChatReading`](https://github.com/Tencent/WeChatReading), version
1.0.4, and remain available under the upstream Apache-2.0 license. Garden only
moves the upstream `version` frontmatter value into `metadata.version` so the
skill passes the Codex Agent Skill validator; API behavior remains unchanged.

Garden never stores a WeRead API key in this skill directory. It can be saved
from the authenticated Garden Reading page into the mounted private
`config.yaml`, or supplied as `WEREAD_API_KEY` at deployment time. The latter
has precedence. The browser can only read the configured state and masked key,
never the secret itself.
