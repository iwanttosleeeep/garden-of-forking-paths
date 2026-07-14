# 施工守则 — The Garden of Forking Paths

用学费换来的规矩,每条背后都有一次事故。违者重修。

1. **patch 一律 `git am --keep-cr`。**本仓库混用 CRLF/LF,`git am` 默认按邮件剥 CR,
   CRLF 文件的 hunk 会全军覆没(2026-07-13,第一刀,六文件阵亡)。
2. **每次动完代码,先 `git log --oneline -1` 验票,再谈 Docker。**
   "显示成功但没变化"的第一嫌疑人永远是"改动根本没上车"。
3. **读启动链路要通读到底。**entrypoint → 实际工作目录 → 服务进程,一行都不许节选
   (2026-07-13,播种机制藏在 entrypoint 后半段,鬼打墙一整晚)。
4. **改动只做加法或明确的减法,不改上游承重墙**:衰减引擎、双通道检索、合并去重、OAuth。
5. **更新的唯一路径是 git + rebuild。**在线热更新机器已拆除,不得复活。
6. **破坏性操作(删记忆、删批次、force push)由 Ainsley 拍板,Senn 不擅自动手。**
7. **每刀一个 commit,每 commit 附冒烟测试结果**(compileall / node --check / pulse 三连)。
8. **每刀交付前,ruff + pytest 本地全过再出门**(2026-07-14,53 处 lint 学费)。
